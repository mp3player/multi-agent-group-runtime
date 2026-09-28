"""Exercise the actual Group CLI process, HTTP transport and shutdown ownership."""

import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import sys
from threading import Event, Thread
import time

import pytest


@pytest.fixture
def provider(tmp_path):
    requests, errors = [], []
    entered, release = Event(), Event()
    release.set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(body)
                entered.set()
                assert release.wait(10), 'Test did not release the provider'
                messages = body['messages']
                current = next(m for m in reversed(messages) if m['role'] == 'user')
                assignment = json.loads(current['content'].split('\n', 1)[1])
                trigger = assignment['trigger_messages'][0]
                if trigger['content'] == 'provider failure':
                    self.send_error(503, 'Fixture provider unavailable')
                    return
                if messages[-1]['role'] == 'tool':
                    names = [member['id'] for member in json.loads(messages[-1]['content'])]
                    calls = [('group_post', {
                        'content': f'{assignment["member_id"]}: {len(names)} members: {", ".join(names)}',
                        'reply_to': trigger['message_id']}), ('group_yield', {})]
                else:
                    calls = [('group_members', {})]
                response = {'choices': [{'finish_reason': 'tool_calls', 'message': {
                    'role': 'assistant', 'content': 'Private transport fixture reasoning',
                    'tool_calls': [{'id': f'call-{index}', 'type': 'function', 'function': {
                        'name': name, 'arguments': json.dumps(arguments)}}
                        for index, (name, arguments) in enumerate(calls)]}}]}
                payload = json.dumps(response).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as error:
                errors.append(error)
                raise

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {**os.environ, 'PYTHON_DOTENV_DISABLED': '1', 'PYTHONPATH': '',
           'BaseURL': f'http://127.0.0.1:{server.server_port}/v1',
           'BaseKey': 'group-test-only', 'BaseModel': 'fixture',
           'MAS_LLM_TRUST_ENV': 'false', 'MAS_LLM_TIMEOUT': '10',
           'MAS_CONTEXT_WINDOW': '131072', 'MAS_DEFAULT_MAX_TOKENS': '512',
           'MAS_CONTEXT_ARCHIVE_DIR': str(tmp_path / 'archives'),
           'MAS_PROMPTS_DIR': str(tmp_path / 'prompts'),
           'MAS_SKILLS_DIR': str(tmp_path / 'skills'), 'MAS_LOG_LEVEL': 'ERROR'}
    try:
        yield env, requests, entered, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()
        assert not errors


def command(path, *args):
    return [sys.executable, '-m', 'cli.group', '--store', str(path), '--max-turns', '4', *args]


def inspect_closed_store(path, reason):
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT state,reason FROM invocations').fetchall() == [('terminal', reason)]
        assert not db.execute("SELECT 1 FROM assignments WHERE state!='settled'").fetchall()
        assert not db.execute("SELECT 1 FROM opportunities WHERE state='pending'").fetchall()
        messages = db.execute('SELECT sender,content,recipients FROM messages ORDER BY sequence').fetchall()
    with path.with_suffix('.sqlite.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return messages


@pytest.mark.parametrize('ending,reason', [('/finish\n/exit\n', 'completed'), ('', 'cancelled')])
def test_interactive_http_routing_and_eof_cleanup(tmp_path, provider, ending, reason):
    env, requests, _, _ = provider
    path = tmp_path / 'chat.sqlite'
    commands = '/members\n/post background\nhello\n@beta hello\n@all hello\n/history\n/status\n'
    result = subprocess.run(command(path, '--members', 'alpha', 'beta'),
                            input=commands + ending, env=env, text=True, capture_output=True,
                            cwd=Path(__file__).resolve().parents[1], timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '2 Agent members: alpha, beta' in result.stdout
    assert '[alpha] alpha: 2 members: alpha, beta' in result.stdout
    assert '[beta] beta: 2 members: alpha, beta' in result.stdout
    assert 'Private transport fixture' not in result.stdout
    assert 'Traceback' not in result.stderr and '[error]' not in result.stdout
    assert len(requests) == 8
    assert all(request['stream'] is False for request in requests)
    messages = inspect_closed_store(path, reason)
    assert len(messages) == 8
    assert [(content, json.loads(recipients)) for sender, content, recipients in messages if sender == 'user'] == [
        ('background', []), ('hello', []), ('hello', ['beta']), ('hello', ['alpha', 'beta'])]


def test_sigint_waits_for_inflight_http_and_settles_members(tmp_path, provider):
    env, requests, entered, release = provider
    release.clear()
    path = tmp_path / 'chat.sqlite'
    process = subprocess.Popen(command(path, '--prompt', 'hello'), env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               cwd=Path(__file__).resolve().parents[1])
    try:
        assert entered.wait(5), 'CLI did not reach the provider'
        process.send_signal(signal.SIGINT)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with sqlite3.connect(path) as db:
                if db.execute('SELECT state FROM invocations').fetchone() == ('closing',):
                    break
            time.sleep(0.01)
        else:
            pytest.fail('SIGINT did not close admission while the HTTP call was in flight')
        assert process.poll() is None, 'CLI exited before its worker settled'
        release.set()
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 130, stdout + stderr
        assert 'Interrupted; chat closed' in stdout
        assert 'Traceback' not in stderr and 'Task was destroyed' not in stderr
        assert len(requests) == 1
        assert len(inspect_closed_store(path, 'cancelled')) == 1
    finally:
        release.set()
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_one_shot_http_failure_is_nonzero_and_does_not_replay(tmp_path, provider):
    env, requests, _, _ = provider
    path = tmp_path / 'chat.sqlite'
    result = subprocess.run(command(path, '--prompt', 'provider failure'), env=env,
                            text=True, capture_output=True, timeout=15,
                            cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'needs_input' in result.stdout and '[error] alice:' in result.stdout
    assert len(requests) == 1
    assert len(inspect_closed_store(path, 'cancelled')) == 1


@pytest.mark.parametrize('partial', [b'', b'hello \xc3'])
def test_idle_sigint_exits_without_an_extra_input_line(tmp_path, provider, partial):
    env, requests, _, _ = provider
    path = tmp_path / 'chat.sqlite'
    process = subprocess.Popen(command(path), env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               cwd=Path(__file__).resolve().parents[1])
    output = b''
    try:
        deadline = time.monotonic() + 5
        while b'group> ' not in output:
            assert select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))[0]
            chunk = os.read(process.stdout.fileno(), 4096)
            assert chunk, output
            output += chunk
        if partial:
            process.stdin.write(partial)
            process.stdin.flush()
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=3) == 130
        stdout, stderr = process.communicate(timeout=3)
        assert b'Interrupted; chat closed' in stdout
        assert b'Traceback' not in stderr and not requests
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT COUNT(*) FROM invocations').fetchone() == (0,)
        with path.with_suffix('.sqlite.lock').open('a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


@pytest.mark.parametrize('regular_file', [False, True])
def test_stdin_eof_preserves_a_final_unicode_line_without_newline(tmp_path, provider, regular_file):
    env, requests, _, _ = provider
    path = tmp_path / 'chat.sqlite'
    content = 'hello caf\u00e9'
    options = dict(env=env, text=True, capture_output=True, timeout=15,
                   cwd=Path(__file__).resolve().parents[1])
    if regular_file:
        source = tmp_path / 'input.txt'
        source.write_text(content, encoding='utf-8')
        with source.open('rb') as stdin:
            result = subprocess.run(command(path), stdin=stdin, **options)
    else:
        result = subprocess.run(command(path), input=content, **options)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(requests) == 2
    assert inspect_closed_store(path, 'cancelled')[0][1] == content


def test_devnull_stdin_is_normal_eof_without_provider_calls(tmp_path, provider):
    env, requests, _, _ = provider
    path = tmp_path / 'chat.sqlite'
    result = subprocess.run(command(path), stdin=subprocess.DEVNULL, env=env,
                            text=True, capture_output=True, timeout=10,
                            cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Bye.' in result.stdout and not requests
    assert '[error]' not in result.stdout
