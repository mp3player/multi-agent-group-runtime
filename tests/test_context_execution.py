"""Long-task context maintenance through the real shared execution loop."""
from copy import deepcopy

import pytest

from core.agent import Agent
from core.agent_runtime.options import AgentOptions
from models import AI, Chunk, ToolCall, User
from tools.registry import ToolRegistry
from tests.runtime_fakes import execute


class ContextModel:
    model = 'context-fixture'
    context_window = 4096

    def __init__(self, *, tool_turns=0, summary_error=None):
        self.tool_turns = tool_turns
        self.summary_error = summary_error
        self.main_calls = 0
        self.summary_calls = 0
        self.requests = []

    def invoke(self, messages, **kwargs):
        self.requests.append((deepcopy(messages), deepcopy(kwargs)))
        if messages and messages[0].message.startswith('CONTEXT_COMPACTION'):
            self.summary_calls += 1
            if self.summary_error:
                raise self.summary_error
            response = AI('Goal: keep the original constraint.\nConstraints: never change schema.\n'
                          'Done: earlier observations recorded.\nNext: continue the task.')
        else:
            self.main_calls += 1
            response = (AI(tool_calls=[ToolCall(str(self.main_calls), 'observe', {})])
                        if self.main_calls <= self.tool_turns else AI('finished'))
        return {'choices': [{'message': response.to_dict(), 'finish_reason': 'stop'}]}

    async def ainvoke(self, messages, **kwargs):
        return self.invoke(messages, **kwargs)

    def stream(self, messages, **kwargs):
        response = self.invoke(messages, **kwargs)['choices'][0]['message']
        yield Chunk(message=response.get('content', ''))
        calls = response.get('tool_calls') or []
        if calls:
            yield Chunk(tool_calls=[ToolCall(c['id'], c['function']['name'], c['function']['arguments'])
                                    for c in calls])
        yield Chunk(finish_reason='stop')

    async def astream(self, messages, **kwargs):
        for chunk in self.stream(messages, **kwargs):
            yield chunk


def make_agent(tmp_path, model, *, window=4096, registry=None, timeout=None, token_counter=None):
    from core.context_archive import FileContextArchive
    from core.session import Session
    return Agent(model, session=Session(archive_store=FileContextArchive(tmp_path)),
                 registry=registry, token_counter=token_counter, options=AgentOptions(
                     context_window=window, max_tokens=256, max_turns=20, run_timeout=timeout))


@pytest.mark.parametrize('mode', ['run', 'arun', 'run_stream', 'arun_stream'])
async def test_long_single_user_task_compacts_without_replaying_tools(tmp_path, mode):
    from core.agent_runtime.context import validate_tool_pairs
    from core.agent_runtime.context_management.budget import TokenCounter, ModelBudget
    seen = []
    registry = ToolRegistry()
    def observe():
        seen.append(len(seen) + 1)
        return f'observation {seen[-1]}: ' + 'detail ' * 140
    registry.register(observe)
    model = ContextModel(tool_turns=12)
    agent = make_agent(tmp_path, model, registry=registry)
    events = []
    agent.subscribe(events.append)
    result = await execute(agent, mode, 'Finish twelve observations; never change schema.')
    assert 'finished' in result
    assert seen == list(range(1, 13))
    assert model.summary_calls >= 3
    assert agent.session.checkpoint is not None
    assert any(e.type == 'context_compaction_end' for e in events)
    agent.session.validate_archives()
    raw = agent.session.archive_store.read(agent.session.session_id, agent.session.checkpoint['archive_id'])
    assert 'observation' in raw['content']
    for messages, options in model.requests:
        validate_tool_pairs(messages)
        assert TokenCounter().estimate(messages, options.get('tools')).tokens <= ModelBudget(4096).input_budget(options['max_tokens'])
        if not messages[0].message.startswith('CONTEXT_COMPACTION'):
            assert sum(m.role == 'user' and 'Finish twelve' in m.message for m in messages) == 1


def test_missing_capacity_preserves_input_and_never_calls_model():
    model = ContextModel()
    model.context_window = None
    agent = Agent(model, options=AgentOptions(max_tokens=256))
    with pytest.raises(RuntimeError, match='(?i)(capacity|context.window|MAS_CONTEXT_WINDOW)'):
        agent.run('keep this input')
    assert model.main_calls == model.summary_calls == 0
    assert any(m.message == 'keep this input' for m in agent.session.active)
    assert not agent.run_state.active


@pytest.mark.parametrize('mode', ['run', 'arun', 'run_stream', 'arun_stream'])
async def test_failed_required_summary_keeps_messages(tmp_path, mode):
    model = ContextModel(summary_error=RuntimeError('summary offline'))
    agent = make_agent(tmp_path, model)
    for i in range(5):
        agent.session.add(User(f'old-{i} ' + 'x' * 400))
        agent.session.add(AI('y' * 400))
    before = deepcopy(agent.session.active)
    with pytest.raises(RuntimeError):
        await execute(agent, mode, 'keep newest request')
    assert agent.session.active[:-1] == before
    assert agent.session.active[-1].message == 'keep newest request'
    assert agent.session.checkpoint is None
    assert model.main_calls == 0
    assert not agent.run_state.active


def test_oversize_new_user_is_never_silently_truncated(tmp_path):
    model = ContextModel()
    agent = make_agent(tmp_path, model)
    text = '\u7528\u6237\u8981\u6c42' * 1200
    with pytest.raises(RuntimeError):
        agent.run(text)
    assert agent.session.active[-1].message == text
    assert model.main_calls == model.summary_calls == 0


def test_ordinary_short_turn_has_no_summary_call(tmp_path):
    model = ContextModel()
    agent = make_agent(tmp_path, model)
    assert agent.run('hello') == 'finished'
    assert model.main_calls == 1 and model.summary_calls == 0


@pytest.mark.parametrize('mode', ['run', 'arun', 'run_stream', 'arun_stream'])
async def test_compaction_selection_counts_transformed_rules(tmp_path, mode):
    from core.agent_runtime.context import validate_tool_pairs
    from core.agent_runtime.context_management.budget import TokenCounter
    from models import System
    rules = 'runtime rules ' + 'r' * 1800
    model = ContextModel()
    agent = make_agent(tmp_path, model)
    agent.context_transform = lambda messages: [System(rules), *messages]
    seed_old_context(agent, size=300, turns=5)
    assert 'finished' in await execute(agent, mode, 'continue')
    assert 1 <= model.summary_calls <= 4 and model.main_calls == 1
    assert agent.session.checkpoint is not None
    agent.session.validate_archives()
    request, options = model.requests[-1]
    assert request[0].message == rules
    assert any(m.role == 'user' and m.message == 'continue' for m in request)
    validate_tool_pairs(request)
    assert TokenCounter().estimate(request, options.get('tools')).tokens <= 3328


class BatchContextModel(ContextModel):
    def __init__(self, size):
        super().__init__(tool_turns=1)
        self.size = size

    def invoke(self, messages, **kwargs):
        response = super().invoke(messages, **kwargs)
        message = response['choices'][0]['message']
        if message.get('tool_calls'):
            message['tool_calls'] = [
                {'id': f'observe-{i}', 'type': 'function',
                 'function': {'name': 'observe', 'arguments': '{}'}}
                for i in range(self.size)]
        return response


@pytest.mark.parametrize('mode', ['run', 'arun', 'run_stream', 'arun_stream'])
@pytest.mark.parametrize('batch_size,rule_size,body', [
    (6, 0, 'x' * 2000), (1, 1900, 'x' * 2000), (6, 200, '\u4e2d\u6587"\\\n' * 250),
])
async def test_tool_previews_use_available_request_space(tmp_path, mode, batch_size, rule_size, body):
    from core.agent_runtime.context import validate_tool_pairs
    from core.agent_runtime.context_management.budget import TokenCounter
    from models import System
    seen = []
    original = 'BEGIN ' + body + ' UNIQUE_END'
    registry = ToolRegistry()
    def observe():
        seen.append(len(seen))
        return original
    registry.register(observe)
    model = BatchContextModel(batch_size)
    agent = make_agent(tmp_path, model, registry=registry)
    if rule_size:
        agent.context_transform = lambda messages: [System('r' * rule_size), *messages]
    assert 'finished' in await execute(agent, mode, 'inspect the observations')
    assert seen == list(range(batch_size)) and model.main_calls == 2
    assert agent.session.overrides
    assert [m.message for m in agent.session.active if m.role == 'tool'] == [original] * batch_size
    for override in agent.session.overrides.values():
        archive = agent.session.archive_store.load(agent.session.session_id, override['archive_id'])
        assert any(e['message'].get('content') == original for e in archive['entries'])
    request, options = model.requests[-1]
    validate_tool_pairs(request)
    assert len([m for m in request if m.role == 'tool']) == batch_size
    assert TokenCounter().estimate(request, options.get('tools')).tokens <= 3328


def test_unfit_tool_batch_stops_without_losing_completed_results(tmp_path):
    from core.agent_runtime.context import validate_tool_pairs
    from core.agent_runtime.context_management.errors import CompactionError
    seen = []
    registry = ToolRegistry()
    original = 'x' * 2000
    def observe():
        seen.append(len(seen))
        return original
    registry.register(observe)
    model = BatchContextModel(12)
    agent = make_agent(tmp_path, model, registry=registry)
    with pytest.raises(CompactionError):
        agent.run('inspect the observations')
    assert model.main_calls == 1 and seen == list(range(12))
    assert [m.message for m in agent.session.active if m.role == 'tool'] == [original] * 12
    assert agent.session.overrides == {} and agent.session.checkpoint is None
    validate_tool_pairs(agent.session.active)
    assert not agent.run_state.active


@pytest.mark.parametrize('separator', ['\n', '"'])
@pytest.mark.parametrize('compressed_placeholder', [False, True])
def test_previews_refit_to_actual_serialized_summary(tmp_path, separator, compressed_placeholder):
    from models import Message, System
    from core.agent_runtime.context_management.budget import TokenCounter
    class RepeatedTokenCounter(TokenCounter):
        def text_tokens(self, text):
            return super().text_tokens(text) - 16 * text.count('0' * 32)
    counter = RepeatedTokenCounter() if compressed_placeholder else TokenCounter()
    summary = separator.join(['done'] * 20)
    summary += 'x' * (256 - len(summary))
    class EscapedSummary(ContextModel):
        def invoke(self, messages, **kwargs):
            response = super().invoke(messages, **kwargs)
            if messages[0].message.startswith('CONTEXT_COMPACTION'):
                response['choices'][0]['message']['content'] = summary
            return response
    agent = make_agent(tmp_path, EscapedSummary(), token_counter=counter)
    agent.context_transform = lambda messages: [System('r' * 1400), *messages]
    agent.session.add_many([User('old source ' + 'x' * 300), AI('old answer'),
                            User('current task'), AI(tool_calls=[ToolCall('observation', 'observe', {})])])
    original = Message('tool', 'complete original ' + 'y' * 2000)
    original.tool_call_id = 'observation'
    agent.session.add(original)
    assert agent.compact() == summary
    assert agent.llm.summary_calls == 1 and agent.llm.main_calls == 0
    assert agent.session.active[-1].message == original.message
    agent.session.validate_archives()
    assert counter.estimate(agent.runtime.prepare_messages(), agent.runtime.tools_payload()).tokens <= 3328
    assert len(list((tmp_path / agent.session.session_id).glob('*.json'))) == 1


def seed_old_context(agent, *, size=300, turns=2):
    for i in range(turns):
        agent.session.add(User(f'old {i} ' + 'x' * size))
        agent.session.add(AI('y' * size))


@pytest.mark.parametrize('mode', ['run', 'arun', 'run_stream', 'arun_stream'])
@pytest.mark.parametrize('failures', [1, 2])
@pytest.mark.parametrize('window', [4096, 32768])
async def test_typed_overflow_retries_only_once(tmp_path, mode, failures, window):
    from core.llm_runtime.errors import ContextWindowExceeded
    class OverflowModel(ContextModel):
        attempts = 0
        def invoke(self, messages, **kwargs):
            if not messages[0].message.startswith('CONTEXT_COMPACTION'):
                self.attempts += 1
                if self.attempts <= failures:
                    raise ContextWindowExceeded('provider rejected context')
            return super().invoke(messages, **kwargs)
    model = OverflowModel()
    agent = make_agent(tmp_path, model, window=window)
    seed_old_context(agent)
    if failures == 1:
        assert 'finished' in await execute(agent, mode, 'continue')
    else:
        with pytest.raises(ContextWindowExceeded):
            await execute(agent, mode, 'continue')
    assert model.attempts == 2 and model.summary_calls == 1
    assert not agent.run_state.active


@pytest.mark.parametrize('mode', ['run_stream', 'arun_stream'])
async def test_visible_stream_cannot_silently_restart(tmp_path, mode):
    from core.llm_runtime.errors import ContextWindowExceeded
    class PartialModel(ContextModel):
        def stream(self, messages, **kwargs):
            self.main_calls += 1
            yield Chunk(message='already visible')
            raise ContextWindowExceeded('late overflow')
    model = PartialModel()
    agent = make_agent(tmp_path, model)
    seed_old_context(agent)
    with pytest.raises(ContextWindowExceeded):
        await execute(agent, mode, 'continue')
    assert model.main_calls == 1 and model.summary_calls == 0
    assert agent.session.checkpoint is None


async def test_cancelled_summary_keeps_original_context_and_releases_owner(tmp_path):
    import asyncio
    started = asyncio.Event()
    class WaitingSummary(ContextModel):
        async def ainvoke(self, messages, **kwargs):
            if messages[0].message.startswith('CONTEXT_COMPACTION'):
                started.set()
                await asyncio.Event().wait()
            return self.invoke(messages, **kwargs)
    agent = make_agent(tmp_path, WaitingSummary())
    seed_old_context(agent, turns=4)
    before = deepcopy(agent.session.active)
    task = asyncio.create_task(agent.arun('continue'))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert agent.session.checkpoint is None
    assert agent.session.active[:-1] == before
    assert not agent.run_state.active


@pytest.mark.parametrize('phase', ['write', 'prepare_context_commit', 'prepare_history_maintenance'])
async def test_cancelled_archive_work_cannot_publish_into_a_later_run(tmp_path, monkeypatch, phase):
    import asyncio
    import threading
    agent = make_agent(tmp_path, ContextModel())
    seed_old_context(agent, turns=4)
    if phase == 'prepare_history_maintenance':
        agent.session.history_limit = 4
    before = deepcopy(agent.session.active)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    owner = agent.session.archive_store if phase == 'write' else agent.session
    original = getattr(owner, phase)
    def held_work(*args, **kwargs):
        result = original(*args, **kwargs)
        if started.is_set():
            return result
        started.set()
        try:
            assert release.wait(3)
            return result
        finally:
            finished.set()
    monkeypatch.setattr(owner, phase, held_work)
    task = asyncio.create_task(agent.acompact())
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not agent.run_state.active
        assert agent.session.checkpoint is None and agent.session.active == before
        assert await agent.arun('continue as a new run') == 'finished'
        after = deepcopy((agent.session.active, agent.session.checkpoint, agent.session.revision))
    finally:
        release.set()
        await asyncio.to_thread(finished.wait, 2)
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert (agent.session.active, agent.session.checkpoint, agent.session.revision) == after


def test_oversized_tool_observation_is_archived_before_preview(tmp_path):
    registry = ToolRegistry()
    original = 'BEGIN ' + ('\u4e2d\u6587 observations ' * 400) + ' UNIQUE_END'
    registry.register(lambda: original, name='observe')
    model = ContextModel(tool_turns=1)
    agent = make_agent(tmp_path, model, registry=registry)
    assert agent.run('read the observation') == 'finished'
    assert agent.session.overrides
    assert any(m.role == 'tool' and m.message == original for m in agent.session.active)
    latest = next(kwargs for messages, kwargs in reversed(model.requests)
                  if not messages[0].message.startswith('CONTEXT_COMPACTION'))
    assert latest['max_tokens'] == 256
    for override in agent.session.overrides.values():
        raw = agent.session.archive_store.read(agent.session.session_id, override['archive_id'])
        assert 'UNIQUE_END' in raw['content']
        assert len(override['preview']) < len(original)


def test_archive_failure_does_not_publish_summary(tmp_path, monkeypatch):
    model = ContextModel()
    agent = make_agent(tmp_path, model)
    seed_old_context(agent, turns=4)
    before = deepcopy(agent.session.active)
    def fail(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(agent.session.archive_store, 'write', fail)
    with pytest.raises(RuntimeError, match='disk full'):
        agent.compact()
    assert agent.session.active == before and agent.session.checkpoint is None
    assert not agent.run_state.active


async def test_manual_compaction_shares_run_owner(tmp_path):
    from core.agent_runtime.run_state import AgentBusyError
    agent = make_agent(tmp_path, ContextModel())
    seed_old_context(agent, turns=3)
    before_users = [m.message for m in agent.session.active if m.role == 'user']
    agent.run_state.enter_run()
    try:
        with pytest.raises(AgentBusyError):
            await agent.acompact()
    finally:
        agent.run_state.exit_run()
    assert await agent.acompact()
    after_users = [m.message for m in agent.session.active if m.role == 'user']
    assert after_users[-1] == before_users[-1] and set(after_users).issubset(before_users)
    assert before_users[0] not in after_users
    assert not agent.run_state.active


def test_context_status_distinguishes_estimates_from_unknown_usage(tmp_path):
    model = ContextModel()
    agent = make_agent(tmp_path, model)
    agent.run('hello')
    status = agent.context_status()
    assert status['capacity_source'] == 'configuration'
    assert status['summary_usage'] is None
    assert status['last_usage'] is None
    assert status['fixed_input_tokens'] > 0  # includes archive tool schema
    assert status['counting_method'] == 'conservative_utf8_bytes'


def test_summary_usage_does_not_present_partial_aggregate_as_complete(tmp_path):
    agent = make_agent(tmp_path, ContextModel())
    manager = agent.runtime.context_manager
    manager.summary_calls = 2
    manager.observe_usage({'prompt_tokens': 10, 'completion_tokens': 2}, phase='summary')
    manager.observe_usage({'prompt_tokens': 20}, phase='summary')
    status = agent.context_status()
    assert status['summary_usage'] == {'prompt_tokens': 30, 'completion_tokens': None}
    assert status['summary_usage_reported_calls'] == 2


def test_deadline_after_archive_validation_cannot_publish(tmp_path, monkeypatch):
    from core.agent_runtime.run_state import AgentTimeoutError
    import time
    agent = make_agent(tmp_path, ContextModel(), timeout=0.05)
    seed_old_context(agent, turns=3)
    before = deepcopy(agent.session.active)
    original = agent.session._validate_archive_chain
    def delayed(roots):
        original(roots)
        if roots:
            time.sleep(0.06)
    monkeypatch.setattr(agent.session, '_validate_archive_chain', delayed)
    with pytest.raises(AgentTimeoutError):
        agent.compact()
    assert agent.session.checkpoint is None and agent.session.active == before
    assert not agent.run_state.active


def test_previous_huge_user_input_can_be_summarized_after_a_new_request(tmp_path):
    agent = make_agent(tmp_path, ContextModel())
    agent.session.add(User('old supplied material ' + 'x' * 4300))
    assert agent.run('continue with the available material') == 'finished'
    assert agent.session.checkpoint is not None
    assert [m.message for m in agent.session.active if m.role == 'user'] == ['continue with the available material']


def test_summary_packs_complete_batches_and_labels_oversized_fragments(tmp_path):
    import json
    from models import Message
    model = ContextModel()
    agent = make_agent(tmp_path, model)
    agent.session.add(User('goal'))
    for i in range(6):
        agent.session.add(AI(tool_calls=[ToolCall(f'c{i}', 'observe', {})]))
        result = Message('tool', f'observation {i}: ' + 'x' * 600)
        result.tool_call_id = f'c{i}'
        agent.session.add(result)
    agent.compact()
    summaries = [messages for messages, _ in model.requests
                 if messages[0].message.startswith('CONTEXT_COMPACTION')]
    assert len(summaries) >= 2
    for messages in summaries:
        text = messages[1].message.split('Historical source segment:\n', 1)[1]
        records = [json.loads(line)['message'] for line in text.splitlines()]
        calls = {c['id'] for record in records for c in record.get('tool_calls', [])}
        results = {record['tool_call_id'] for record in records if record['role'] == 'tool'}
        assert calls == results

    huge = make_agent(tmp_path / 'huge', ContextModel())
    original = 'large source ' + 'z' * 5000
    huge.session.add(User(original))
    huge.run('continue')
    fragments = []
    for messages, _ in huge.llm.requests:
        if messages[0].message.startswith('CONTEXT_COMPACTION'):
            text = messages[1].message.split('Historical source segment:\n', 1)[1]
            fragments.extend(json.loads(line) for line in text.splitlines())
    assert len(fragments) >= 2
    assert all(part['historical_source_unit'][0]['role'] == 'user' for part in fragments)
    assert [part['source_char_offset'] for part in fragments] == sorted(part['source_char_offset'] for part in fragments)
    reconstructed = json.loads(''.join(part['quoted_fragment'] for part in fragments))
    assert reconstructed['message']['content'] == original


def test_summary_call_ceiling_preserves_whole_old_context(tmp_path):
    model = ContextModel()
    agent = make_agent(tmp_path, model)
    original = User('huge historical material ' + 'x' * 22000)
    agent.session.add(original)
    with pytest.raises(RuntimeError, match='four calls'):
        agent.run('continue')
    assert model.summary_calls == 4 and model.main_calls == 0
    assert agent.session.active[0] == original and agent.session.checkpoint is None


@pytest.mark.parametrize('failure', ['empty', 'tool_call', 'truncated', 'oversized'])
def test_invalid_summary_never_replaces_originals(tmp_path, failure):
    class InvalidSummary(ContextModel):
        def invoke(self, messages, **kwargs):
            if messages[0].message.startswith('CONTEXT_COMPACTION'):
                response = AI('' if failure == 'empty' else 'x' * 4000 if failure == 'oversized' else 'summary')
                if failure == 'tool_call':
                    response.tool_calls = [ToolCall('unexpected', 'observe', {})]
                return {'choices': [{'message': response.to_dict(),
                                     'finish_reason': 'length' if failure == 'truncated' else 'stop'}]}
            return super().invoke(messages, **kwargs)
    agent = make_agent(tmp_path, InvalidSummary())
    seed_old_context(agent, turns=4)
    before = deepcopy(agent.session.active)
    with pytest.raises(RuntimeError):
        agent.compact()
    assert agent.session.active == before and agent.session.checkpoint is None
