"""Offline provider faults and repeated execution through the real Agent/LLM boundary."""
from collections import Counter
import json

import httpx
import pytest

from core.agent import Agent
from core.agent_runtime.context import validate_tool_pairs
from core.agent_runtime.options import AgentOptions
from core.agent_runtime.context_management.errors import CompactionError
from core.context_archive import FileContextArchive
from core.llm import LLMClient, LLMError, RateLimitError, ServerError
from core.session import Session
from core.usage import UsageMonitor
from tests.runtime_fakes import execute
from tests.test_review_provider_boundaries import completed_response, sse
from tools.audit import ToolAuditLog
from tools.registry import ToolRegistry
from tools.runtime import ToolRuntime


MODES = ('run', 'arun', 'run_stream', 'arun_stream')


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('fault,error', [
    ('context', CompactionError), ('rate_limit', RateLimitError),
    ('server', ServerError), ('network', LLMError),
])
async def test_provider_fault_releases_run_without_retries_and_allows_reuse(monkeypatch, mode, fault, error):
    requests = 0
    effects = []
    def provider(request):
        nonlocal requests
        requests += 1
        body = json.loads(request.content)
        if fault == 'context':
            # Retrying with the same oversized history must still fail. This
            # fixture does not pretend that a busy-state reset shrinks context.
            if any(len(message.get('content') or '') > 4096 for message in body['messages']):
                return httpx.Response(400, json={'error': {'code': 'context_length_exceeded'}})
        elif requests == 1:
            if fault == 'network':
                raise httpx.ReadError('connection lost', request=request)
            return httpx.Response(429 if fault == 'rate_limit' else 503, json={'error': fault})
        return httpx.Response(200, text=completed_response('stream' in mode))

    transport = httpx.MockTransport(provider)
    client = LLMClient('http://fixture.invalid/v1', 'test-only', 'fixture')
    registry = ToolRegistry()
    registry.register(lambda: effects.append('unexpected'), name='effect')
    agent = Agent(client, registry=registry, options=AgentOptions(context_window=32768))
    events = []
    agent.subscribe(events.append)
    with httpx.Client(transport=transport, trust_env=False) as sync_http:
        monkeypatch.setattr(httpx, 'post', lambda url, *, trust_env=True, **kw: sync_http.post(url, **kw))
        monkeypatch.setattr(httpx, 'stream', lambda method, url, *, trust_env=True, **kw: sync_http.stream(method, url, **kw))
        client._async_client = httpx.AsyncClient(transport=transport, trust_env=False)
        try:
            with pytest.raises(error):
                await execute(agent, mode, 'x' * 5000 if fault == 'context' else 'task')
            assert requests == 1  # No hidden retry or tool replay.
            assert events[-1].status == 'error' and not agent.run_state.active
            assert effects == [] and registry.tool_audit_log.records() == []
            if fault == 'context':
                with pytest.raises(CompactionError):
                    await execute(agent, mode, 'try again')
                # A summary request may now be attempted, but its provider failure
                # must preserve the oversized original rather than drop it.
                assert 2 <= requests <= 3 and not agent.run_state.active
                assert any(m.message == 'x' * 5000 for m in agent.session.active)
                # Explicit application action, not an automatic context policy.
                agent.reset_active_to_system()
            before_next = requests
            assert await execute(agent, mode, 'next task') == 'recovered'
            assert requests == before_next + 1
            assert events[-1].status == 'completed' and not agent.run_state.active
            validate_tool_pairs(agent.session.active)
        finally:
            await client.aclose()


async def test_one_thousand_mixed_runs_keep_bounds_and_never_replay_completed_effects(monkeypatch, tmp_path):
    requests = 0
    summary_requests = 0
    billable_responses = 0
    writes = 0
    outcomes = Counter()
    marker = tmp_path / 'effect-count.txt'
    monitor = UsageMonitor(max_records=32)
    audit = ToolAuditLog(max_records=32)
    registry = ToolRegistry()

    def write_marker():
        nonlocal writes
        writes += 1
        marker.write_text(str(writes))
        return 'written'

    registry.register(write_marker)

    def provider(request):
        nonlocal requests, billable_responses, summary_requests
        requests += 1
        body = json.loads(request.content)
        messages = body['messages']
        assert len(json.dumps({'messages': messages, 'tools': body.get('tools')},
                              ensure_ascii=False, separators=(',', ':')).encode()) <= 8192 - body['max_tokens']
        if messages[0]['content'].startswith('CONTEXT_COMPACTION'):
            summary_requests += 1
            billable_responses += 1
            return httpx.Response(200, json={'choices': [{'message': {
                'role': 'assistant', 'content': 'Earlier numbered tasks wrote their markers once. Continue the current task.'},
                'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})
        turn = int(next(m['content'] for m in reversed(messages) if m['role'] == 'user'))
        after_effect = messages[-1]['role'] == 'tool'
        if after_effect and turn % 11 == 0:
            return httpx.Response(503, json={'error': 'failure after completed write'})
        message = {'role': 'assistant', 'content': 'done' if after_effect else None}
        if not after_effect:
            message['tool_calls'] = [{'id': f'write-{turn}', 'type': 'function',
                'function': {'name': 'write_marker', 'arguments': '{}'}}]
        reason = 'stop' if after_effect else 'tool_calls'
        usage = {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}
        billable_responses += 1
        if body['stream']:
            delta = {k: v for k, v in message.items() if k != 'role'}
            if 'tool_calls' in delta:
                delta['tool_calls'] = [{'index': 0, **delta['tool_calls'][0]}]
            response = sse({'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]})
            response += sse({'choices': [{'index': 0, 'delta': {}, 'finish_reason': reason}]})
            response += sse({'choices': [], 'usage': usage}) + 'data: [DONE]\n\n'
            return httpx.Response(200, text=response)
        return httpx.Response(200, json={'choices': [{'message': message, 'finish_reason': reason}], 'usage': usage})

    transport = httpx.MockTransport(provider)
    client = LLMClient('http://fixture.invalid/v1', 'test-only', 'fixture', usage_monitor=monitor)
    agent = Agent(client, session=Session(history_limit=64,
                                        archive_store=FileContextArchive(tmp_path / 'archives')),
                  options=AgentOptions(context_window=8192, max_tokens=512, active_message_limit=20),
                  tool_runtime=ToolRuntime(registry, audit_log=audit))
    agent.subscribe(lambda event: outcomes.update([event.status]) if event.type == 'run_end' else None)
    with httpx.Client(transport=transport, trust_env=False) as sync_http:
        monkeypatch.setattr(httpx, 'post', lambda url, *, trust_env=True, **kw: sync_http.post(url, **kw))
        monkeypatch.setattr(httpx, 'stream', lambda method, url, *, trust_env=True, **kw: sync_http.stream(method, url, **kw))
        client._async_client = httpx.AsyncClient(transport=transport, trust_env=False)
        try:
            for turn in range(1, 1001):
                mode = MODES[(turn - 1) % len(MODES)]
                if turn % 11 == 0:
                    with pytest.raises(ServerError):
                        await execute(agent, mode, str(turn))
                else:
                    assert await execute(agent, mode, str(turn)) == 'done'
                assert not agent.run_state.active
                assert writes == turn and marker.read_text() == str(turn)
                assert len(agent.session.history) <= 64
                assert len(agent.session.active) <= 80  # bounded working projection, no count-based dropping
                assert len(audit.records()) <= 32 and len(monitor.records()) <= 32
                validate_tool_pairs(agent.session.active)
                validate_tool_pairs(agent.session.history)
            assert requests == 2000 + summary_requests
            assert summary_requests >= 3 and agent.session.checkpoint is not None
            agent.session.validate_archives()
            assert outcomes == {'completed': 910, 'error': 90}
            assert audit.records()[-1].id == 1000
            assert monitor.summary()['fixture'].total_tokens == billable_responses * 2
        finally:
            await client.aclose()
