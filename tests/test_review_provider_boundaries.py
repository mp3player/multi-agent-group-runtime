"""Provider errors must never authorize tool effects or successful responses."""

from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

import pytest

from core.agent import Agent
from core.agent_runtime.options import AgentOptions
from core.llm import LLMClient, LLMError
from core.usage import UsageMonitor
from models import AI, Chunk, ToolCall, User
from tests.runtime_fakes import ScriptedModel, execute
from tools.registry import ToolRegistry


def sse(value):
    return 'data: ' + json.dumps(value) + '\n\n'


@pytest.fixture
def wire_provider(monkeypatch):
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(key, raising=False)
    replies = deque()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            if not replies:
                self.send_error(500, 'Unexpected extra model request')
                return
            payload = replies.popleft().encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream' if request['stream'] else 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', replies, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def failed_response(fault, streaming):
    call = {'id': 'effect-1', 'type': 'function',
            'function': {'name': 'write_marker', 'arguments': '{}'}}
    if not streaming:
        if fault == 'provider_error':
            return json.dumps({'error': {'message': 'provider failed', 'type': 'server_error'}})
        if fault == 'malformed':
            return json.dumps({'choices': [{}]})
        return json.dumps({'choices': [{'index': 0, 'finish_reason': fault,
                                        'message': {'role': 'assistant', 'content': None, 'tool_calls': [call]}}]})
    prefix = sse({'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, **call}]}, 'finish_reason': None}]})
    if fault == 'eof':
        return prefix
    if fault == 'done_without_finish':
        return prefix + 'data: [DONE]\n\n'
    if fault == 'provider_error':
        return prefix + sse({'error': {'message': 'provider failed', 'type': 'server_error'}})
    if fault == 'bad_json':
        return prefix + 'data: {broken\n\n'
    if fault == 'malformed':
        return prefix + sse({'choices': 'invalid'})
    return prefix + sse({'choices': [{'index': 0, 'delta': {}, 'finish_reason': fault}]}) + 'data: [DONE]\n\n'


def completed_response(streaming, *, done=True):
    if not streaming:
        return json.dumps({'choices': [{'index': 0, 'finish_reason': 'stop',
                                        'message': {'role': 'assistant', 'content': 'recovered'}}]})
    result = sse({'choices': [{'index': 0, 'delta': {'content': 'recovered'}, 'finish_reason': None}]})
    result += sse({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})
    result += sse({'choices': [], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})
    return result + ('data: [DONE]\n\n' if done else '')


FAILURES = [
    (mode, fault)
    for mode in ('run', 'arun', 'run_stream', 'arun_stream')
    for fault in ('length', 'content_filter', 'provider_error', 'malformed')
] + [
    (mode, fault)
    for mode in ('run_stream', 'arun_stream')
    for fault in ('eof', 'done_without_finish', 'bad_json')
]


@pytest.mark.parametrize('mode,fault', FAILURES)
async def test_failed_http_response_cannot_commit_or_execute_tools_and_agent_recovers(wire_provider, tmp_path, mode, fault):
    url, replies, requests = wire_provider
    streaming = 'stream' in mode
    replies.extend([failed_response(fault, streaming), completed_response(streaming)])
    marker = tmp_path / 'must-not-exist.txt'
    registry = ToolRegistry()
    registry.register(lambda: marker.write_text('unexpected effect'), name='write_marker')
    client = LLMClient(base_url=url, api_key='test-only', model='fixture')
    agent = Agent(client, registry=registry, options=AgentOptions(context_window=131072))
    events = []
    agent.subscribe(events.append)
    try:
        with pytest.raises(LLMError):
            await execute(agent, mode)
        assert not marker.exists()
        assert registry.tool_audit_log.records() == []
        assert not any(isinstance(message, AI) for message in agent.session.history)
        assert events[-1].status == 'error'
        assert not agent.run_state.active
        assert await execute(agent, mode, 'retry') == 'recovered'
        assert agent.session.history[-1].message == 'recovered'
        assert events[-1].status == 'completed'
        assert len(requests) == 2
    finally:
        await client.aclose()


@pytest.mark.parametrize('mode', ['run', 'arun'])
@pytest.mark.parametrize('finish', [{}, {'finish_reason': None}, {'finish_reason': ''}],
                         ids=['missing', 'null', 'empty'])
@pytest.mark.parametrize('with_tool', [False, True], ids=['text', 'tool'])
async def test_nonstream_http_requires_completion_before_commit_and_recovers(
    wire_provider, tmp_path, mode, finish, with_tool,
):
    url, replies, requests = wire_provider
    message = {'role': 'assistant', 'content': 'unconfirmed answer'}
    if with_tool:
        message['tool_calls'] = [{'id': 'effect-1', 'type': 'function',
                                 'function': {'name': 'write_marker', 'arguments': '{}'}}]
    replies.extend([
        json.dumps({'choices': [{'index': 0, 'message': message, **finish}],
                    'usage': {'prompt_tokens': 11, 'completion_tokens': 3, 'total_tokens': 14}}),
        completed_response(False),
    ])
    marker = tmp_path / 'must-not-exist.txt'
    registry = ToolRegistry()
    registry.register(lambda: marker.write_text('unexpected effect'), name='write_marker')
    monitor = UsageMonitor()
    client = LLMClient(base_url=url, api_key='test-only', model='fixture', usage_monitor=monitor)
    agent = Agent(client, registry=registry, options=AgentOptions(context_window=131072))
    events = []
    agent.subscribe(events.append)
    try:
        with pytest.raises(LLMError, match='finish_reason'):
            await execute(agent, mode, 'original request')
        assert not marker.exists()
        assert registry.tool_audit_log.records() == []
        assert not any(isinstance(message, AI) for message in agent.session.history)
        assert [message.message for message in agent.session.history if isinstance(message, User)] == ['original request']
        assert events[-1].status == 'error'
        assert not agent.run_state.active
        assert len(monitor.records()) == 1
        usage = monitor.records()[0]
        assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (11, 3, 14)
        assert await execute(agent, mode, 'retry') == 'recovered'
        assert agent.session.history[-1].message == 'recovered'
        assert events[-1].status == 'completed'
        assert len(requests) == 2
    finally:
        await client.aclose()


@pytest.mark.parametrize('mode', ['run', 'arun'])
async def test_custom_nonstream_adapter_without_finish_metadata_still_executes_tools(tmp_path, mode):
    marker = tmp_path / 'custom-effect.txt'
    registry = ToolRegistry()
    registry.register(lambda: marker.write_text('expected effect'), name='write_marker')
    model = ScriptedModel(AI(tool_calls=[ToolCall('custom-1', 'write_marker', {})]), AI('completed'))
    agent = Agent(model, registry=registry)
    assert await execute(agent, mode) == 'completed'
    assert marker.read_text() == 'expected effect'
    assert len(registry.tool_audit_log.records()) == 1
    assert agent.session.history[-1].message == 'completed'
    assert not agent.run_state.active


@pytest.mark.parametrize('mode', ['run_stream', 'arun_stream'])
@pytest.mark.parametrize('done', [False, True])
async def test_finish_only_chunk_reaches_stream_caller(wire_provider, mode, done):
    url, replies, _ = wire_provider
    replies.append(completed_response(True, done=done))
    client = LLMClient(base_url=url, api_key='test-only', model='fixture')
    agent = Agent(client, options=AgentOptions(context_window=131072))
    try:
        chunks = list(agent.run_stream('go')) if mode == 'run_stream' else [chunk async for chunk in agent.arun_stream('go')]
        assert ''.join(chunk.message for chunk in chunks) == 'recovered'
        assert chunks[-1].finish_reason == 'stop'
        assert len([chunk for chunk in chunks if chunk.finish_reason]) == 1
        assert agent.session.history[-1].message == 'recovered'
    finally:
        await client.aclose()


@pytest.mark.parametrize('mode', ['run_stream', 'arun_stream'])
@pytest.mark.parametrize('reason', ['length', 'content_filter', 'error', 'aborted'])
async def test_runtime_rejects_custom_model_incomplete_output_before_tool_effects(tmp_path, mode, reason):
    class IncompleteModel(ScriptedModel):
        def stream(self, messages, **kwargs):
            response = self._next(messages, **kwargs)
            try:
                yield Chunk(message=response.message, tool_calls=response.tool_calls)
                yield Chunk(finish_reason=reason if response.tool_calls else 'stop')
            finally:
                self.streams_closed += 1

    marker = tmp_path / 'must-not-exist.txt'
    registry = ToolRegistry()
    registry.register(lambda: marker.write_text('unexpected effect'), name='write_marker')
    model = IncompleteModel(AI(tool_calls=[ToolCall('bad', 'write_marker', {})]), AI('recovered'))
    agent = Agent(model, registry=registry)
    events = []
    agent.subscribe(events.append)
    with pytest.raises(LLMError, match=reason):
        await execute(agent, mode)
    assert not marker.exists()
    assert registry.tool_audit_log.records() == []
    assert not any(isinstance(message, AI) for message in agent.session.history)
    assert events[-1].status == 'error'
    assert not agent.run_state.active
    assert await execute(agent, mode, 'retry') == 'recovered'
    assert model.streams_closed == 2
