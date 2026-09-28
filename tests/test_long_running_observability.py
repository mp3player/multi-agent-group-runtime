"""Optional monitoring must stay bounded and must not lose completed effects."""
import json

import httpx
import pytest

from core.agent import Agent
from core.agent_runtime.options import AgentOptions
from core.llm import LLMClient
from core.usage import UsageMonitor
from models import ToolCall
from tools.audit import ToolAuditLog, audit_sink_from_path
from tools.permissions import ToolPermission
from tools.registry import ToolRegistry
from tools.runtime import ToolRuntime
from tools.file_ops import read_file
from tools.workspace import Workspace


def append_audit(log, result='ok'):
    return log.append(tool='work', caller='agent', permission=ToolPermission(),
                      arguments={}, result=result, error=False)


def test_audit_retention_keeps_recent_records_and_persists_every_event(tmp_path):
    path = tmp_path / 'audit.jsonl'
    log = ToolAuditLog(max_records=3, sink=audit_sink_from_path(path))
    for number in range(7):
        append_audit(log, str(number))
    assert [r.id for r in log.records()] == [5, 6, 7]
    assert [r.result for r in log.records(2)] == ['5', '6']
    assert [json.loads(line)['id'] for line in path.read_text().splitlines()] == list(range(1, 8))


def test_default_audit_retention_is_bounded():
    log = ToolAuditLog()
    for _ in range(1100):
        append_audit(log)
    assert len(log.records()) <= 1000
    assert log.records()[-1].id == 1100


def test_audit_can_persist_surrogate_text(tmp_path):
    path = tmp_path / 'audit.jsonl'
    log = ToolAuditLog(sink=audit_sink_from_path(path))
    append_audit(log, 'filename_\udcff')
    assert json.loads(path.read_text(encoding='utf-8'))['result'] == 'filename_\udcff'


def test_sink_failure_does_not_erase_successful_mutation_or_audit_status(tmp_path):
    class FailedSink:
        def write(self, record):
            raise ValueError('sink unavailable')
    registry = ToolRegistry()
    target = tmp_path / 'effect.txt'
    def write_once():
        target.write_text('completed')
        return 'completed'
    registry.register(write_once)
    log = ToolAuditLog(sink=FailedSink())
    runtime = ToolRuntime(registry, audit_log=log)
    result = runtime.execute([ToolCall('once', 'write_once', {})])[0]
    assert target.read_text() == 'completed'
    assert result.tool_success and result.message == 'completed'
    assert not log.records()[0].error
    assert log.sink_error_count == 1
    assert 'sink unavailable' in log.last_sink_error


def test_tool_results_are_serializable_and_bounded_before_session_retention():
    registry = ToolRegistry()
    registry.register(lambda: 'name_\udcff' + 'x' * 100000, name='large_result')
    runtime = ToolRuntime(registry, max_result_chars=256)
    result = runtime.execute([ToolCall('large', 'large_result', {})])[0]
    assert result.tool_success
    assert len(result.message) <= 256
    assert '\\udcff' in result.message
    assert 'truncated' in result.message.lower()
    result.message.encode('utf-8')


def test_default_result_limit_preserves_builtin_large_page_continuation(tmp_path):
    (tmp_path / 'long.txt').write_text('x' * 100000)
    registry = ToolRegistry()
    registry.register(read_file)
    runtime = ToolRuntime(registry, workspace=Workspace(roots=(str(tmp_path),)))
    result = runtime.execute([ToolCall('read', 'read_file', {
        'path': 'long.txt', 'max_chars': 65536,
    })])[0]
    assert result.tool_success
    assert 'char_offset=' in result.message
    assert result.message.endswith(')')


def test_usage_retention_preserves_lifetime_totals_and_clear_resets_them():
    monitor = UsageMonitor(max_records=3)
    for _ in range(7):
        monitor.record('agent', {'model': 'fixture', 'usage': {
            'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5,
        }})
    assert len(monitor.records()) == 3
    summary = monitor.summary()['agent']
    assert (summary.prompt_tokens, summary.completion_tokens, summary.total_tokens) == (14, 21, 35)
    summary.total_tokens = 999
    assert monitor.summary()['agent'].total_tokens == 35
    monitor.clear()
    assert monitor.records() == [] and monitor.summary() == {}


def test_default_usage_retention_is_bounded_and_missing_usage_is_not_recorded():
    monitor = UsageMonitor()
    monitor.record('agent', {'choices': []})
    assert monitor.records() == []
    for _ in range(1100):
        monitor.record('agent', {'usage': {'total_tokens': 2}})
    assert len(monitor.records()) <= 1000
    assert monitor.summary()['agent'].total_tokens == 2200


@pytest.mark.parametrize('mode', ['run', 'arun'])
@pytest.mark.parametrize('fault', ['malformed_usage', 'monitor_callback'])
async def test_optional_usage_failure_cannot_replace_valid_model_reply(monkeypatch, mode, fault):
    body = {'choices': [{'message': {'role': 'assistant', 'content': 'completed'},
                         'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 'bad' if fault == 'malformed_usage' else 1}}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    class BrokenMonitor:
        def record(self, *args, **kwargs):
            raise RuntimeError('monitor unavailable')
    monitor = UsageMonitor() if fault == 'malformed_usage' else BrokenMonitor()
    client = LLMClient('http://fixture.invalid/v1', 'fixture-key', 'fixture', usage_monitor=monitor)
    agent = Agent(client, options=AgentOptions(context_window=131072))
    with httpx.Client(transport=transport, trust_env=False) as sync_http:
        monkeypatch.setattr(httpx, 'post', lambda url, *, trust_env=True, **kw: sync_http.post(url, **kw))
        client._async_client = httpx.AsyncClient(transport=transport, trust_env=False)
        try:
            result = agent.run('task') if mode == 'run' else await agent.arun('task')
            assert result == 'completed'
            assert not agent.run_state.active
            assert agent.session.history[-1].message == 'completed'
            assert client.usage_error_count == 1
        finally:
            await client.aclose()
