"""Offline acceptance through the actual HTTP protocol, tools and CLI."""
import asyncio
from contextlib import aclosing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread

import pytest
from application.agent_config import AgentAppConfig
from application.agent_service import AgentAppService


@pytest.fixture
def provider(tmp_path, monkeypatch):
    # Local protocol fixtures must not inherit machine proxy routing.
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(name, raising=False)
    requests = []
    source = tmp_path / 'source.txt'
    target = tmp_path / 'written.txt'
    source.write_text('fixture input')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            if body['messages'][-1]['content'] == 'continue':
                message = {'role': 'assistant', 'content': 'continued'}
            elif body['messages'][-1]['role'] == 'tool':
                message = {'role': 'assistant', 'content': 'files complete'}
            else:
                message = {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': 'read-1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': json.dumps({'path': str(source)})}},
                    {'id': 'write-1', 'type': 'function', 'function': {'name': 'write_file', 'arguments': json.dumps({'path': str(target), 'content': 'fixture output'})}},
                ]}
            finish = 'tool_calls' if 'tool_calls' in message else 'stop'
            if body['stream']:
                deltas = []
                if 'tool_calls' in message:
                    for index, call in enumerate(message['tool_calls']):
                        argument = call['function']['arguments']
                        deltas += [
                            {'tool_calls': [{'index': index, 'id': call['id'], 'type': 'function', 'function': {'name': call['function']['name'], 'arguments': argument[:7]}}]},
                            {'tool_calls': [{'index': index, 'function': {'arguments': argument[7:]}}]},
                        ]
                else:
                    deltas = [{'content': message['content'][:5]}, {'content': message['content'][5:]}]
                chunks = [{'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]} for delta in deltas]
                chunks.append({'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]})
                payload = ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
                content_type = 'text/event-stream'
            else:
                payload = json.dumps({'model': body['model'], 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})
                content_type = 'application/json'
            encoded = payload.encode()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', requests, target
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def assert_pairs(request):
    messages = request['messages']
    call_message = next(m for m in messages if m.get('tool_calls'))
    assert [c['id'] for c in call_message['tool_calls']] == ['read-1', 'write-1']
    results = [m for m in messages if m['role'] == 'tool']
    assert [r['tool_call_id'] for r in results] == ['read-1', 'write-1']
    assert 'fixture input' in results[0]['content']
    assert 'written.txt' in results[1]['content']


@pytest.mark.asyncio
@pytest.mark.parametrize('streaming', [False, True])
async def test_actual_http_tools_snapshot_continuation_and_instance_options(provider, tmp_path, streaming):
    url, requests, target = provider
    def app(tokens, temperature):
        return AgentAppService(AgentAppConfig.from_mapping({
            'BaseURL': url, 'BaseKey': 'agent-test-only', 'BaseModel': 'fixture',
            'MAS_WORKSPACE_ROOTS': str(tmp_path), 'MAS_DEFAULT_MAX_TOKENS': str(tokens),
            'MAS_DEFAULT_TEMPERATURE': str(temperature),
            'MAS_CONTEXT_WINDOW': '131072',
        }))
    first = app(123, 0.2)
    second = app(321, 0.7)
    async def run(service, prompt):
        if not streaming:
            return await service.arun(prompt)
        async with aclosing(service.arun_stream(prompt)) as chunks:
            return ''.join([chunk.message async for chunk in chunks])
    try:
        assert await run(first, 'copy') == 'files complete'
        assert target.read_text() == 'fixture output'
        assert len(first.tool_audit_records()) == 2
        assert_pairs(requests[1])
        snapshot = tmp_path / 'session.json'
        first.save_session(snapshot)
        count = len(requests)
        second.load_session(snapshot)
        assert len(requests) == count
        target.write_text('must not replay')
        assert await run(second, 'continue') == 'continued'
        assert target.read_text() == 'must not replay'
        assert_pairs(requests[2])
        assert [r['max_tokens'] for r in requests] == [123, 123, 321]
        assert [r['temperature'] for r in requests] == [0.2, 0.2, 0.7]
        assert all(r['stream'] is streaming for r in requests)
        assert len(first.usage_records()) == (0 if streaming else 2)
        clients = [first.model._async_client, second.model._async_client]
    finally:
        await first.aclose()
        await second.aclose()
    assert first.closed and second.closed
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize('module,streaming', [('main', False), ('cli.agent', True)])
def test_one_shot_cli_with_legacy_imports_blocked(provider, tmp_path, module, streaming):
    url, requests, target = provider
    code = '''
import importlib.abc, runpy, sys
blocked = ('core.group', 'domain.group', 'application.group', 'application.member_config',
           'core.member_config', 'prompting.group', 'application.config', 'application.builders',
           'core.config', 'cli.group')
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith(blocked):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Guard())
runpy.run_module(MODULE, run_name='__main__')
'''.replace('MODULE', repr(module))
    env = {**os.environ, 'PYTHONPATH': '', 'PYTHON_DOTENV_DISABLED': '1',
           'BaseURL': url, 'BaseKey': 'agent-test-only', 'BaseModel': 'fixture',
           'MAS_WORKSPACE_ROOTS': str(tmp_path), 'MAS_ENABLE_TOOLS': 'true',
           'MAS_CONTEXT_WINDOW': '131072'}
    result = subprocess.run([sys.executable, '-c', code, '--prompt', 'copy', *([] if streaming else ['--no-stream'])],
                            env=env, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'files complete' in result.stdout
    assert target.read_text() == 'fixture output'
    assert len(requests) == 2
    assert_pairs(requests[1])
