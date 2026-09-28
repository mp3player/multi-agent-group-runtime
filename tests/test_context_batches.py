"""Controlled context ingestion and authoritative worker preparation."""
import pytest

from core.agent import Agent
from core.agent_runtime.worker import AgentWorker
from core.session import Session
from core.session_store import JsonSessionStore
from models import AI, ToolCall, User
from tests.runtime_fakes import ScriptedModel


def batch(operation='reception-1', text='Attributed source'):
    from core.context_batch import ContextBatch
    return ContextBatch(operation, (User(text), AI('Runtime reception record')), {
        'origin': 'runtime_reception', 'source_ids': ['source-1'], 'adapter_version': 1})


def test_context_batch_is_detached_atomic_idempotent_and_non_inference():
    agent = Agent(ScriptedModel())
    value = batch()
    first = agent.apply_context_batch(value)
    value.messages[0].message = 'caller mutation'
    assert agent.session.history[0].message == 'Attributed source'
    assert agent.apply_context_batch(batch()) == first
    with pytest.raises(ValueError, match='identity'):
        agent.apply_context_batch(batch(text='different'))
    assert len(agent.session.history) == 2
    assert agent.llm.requests == []
    assert agent.session.context_batch_receipt(value.operation_id) == first
    assert agent.session.context_batch_covered(value.operation_id)


def test_receipt_cannot_authorize_reset_context():
    agent = Agent(ScriptedModel())
    receipt = agent.apply_context_batch(batch())
    agent.session.reset_to_system()
    assert agent.session.context_batch_receipt(receipt.operation_id) == receipt
    assert not agent.session.context_batch_covered(receipt.operation_id)
    with pytest.raises(ValueError, match='coverage'):
        agent.apply_context_batch(batch())


def test_invalid_batch_does_not_partially_mutate_session():
    from core.context_batch import ContextBatch
    agent = Agent(ScriptedModel())
    for messages, provenance in [((User('ok'), AI(tool_calls=[ToolCall('c', 'effect', {})])), {'origin': 'runtime'}),
                                 ((User('ok'),), {'origin': object()}),
                                 ((User('ok'),), {})]:
        with pytest.raises(ValueError):
            agent.apply_context_batch(ContextBatch('invalid', messages, provenance))
    assert agent.session.history == []


def test_snapshot_preserves_receipt_and_provenance(tmp_path):
    agent = Agent(ScriptedModel())
    receipt = agent.apply_context_batch(batch())
    path = tmp_path / 'session.json'
    JsonSessionStore().save(agent.session, path)
    restored = JsonSessionStore().load(path)
    assert restored.context_batch_receipt(receipt.operation_id) == receipt
    assert restored.context_batch_covered(receipt.operation_id)
    assert restored.context_entries()[0].provenance == batch().provenance


@pytest.mark.asyncio
async def test_worker_applies_lazy_batches_acknowledges_then_authorizes_main_inference():
    agent = Agent(ScriptedModel(AI('done')))
    worker = AgentWorker(agent, worker_safe=True)
    seen = []
    def batches():
        seen.append('yield')
        yield batch()
    def acknowledge(receipt):
        assert agent.llm.requests == []
        seen.append(receipt.operation_id)
    def authorize():
        assert agent.session.active[-1].message == 'current task'
        assert agent.llm.requests == []
        seen.append('authorize')
    try:
        result = await worker.submit('current task', context_batches=batches(),
            on_context_applied=acknowledge, before_inference=authorize).wait()
        assert result.status == 'completed'
        assert result.phase == 'execution'
        assert seen == ['yield', 'reception-1', 'authorize']
    finally:
        await worker.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure_callback', ['ack', 'admission'])
async def test_failed_authoritative_callback_prevents_main_inference(failure_callback):
    agent = Agent(ScriptedModel(AI('never')))
    worker = AgentWorker(agent, worker_safe=True)
    def fail(*args):
        raise OSError('durable write failed')
    try:
        outcome = await worker.submit('task', context_batches=[batch()],
            on_context_applied=fail if failure_callback == 'ack' else None,
            before_inference=fail if failure_callback == 'admission' else None).wait()
        assert outcome.status == 'error'
        assert outcome.phase == 'preparation'
        assert outcome.error_type == 'OSError'
        assert agent.llm.requests == []
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_identified_task_retry_is_not_appended_twice():
    agent = Agent(ScriptedModel(AI('done')))
    worker = AgentWorker(agent, worker_safe=True)
    def reject():
        raise OSError('blocked')
    try:
        first = await worker.submit('task', input_id='task-1', input_provenance={'origin': 'runtime'},
                                    before_inference=reject).wait()
        assert first.phase == 'preparation'
        second = await worker.submit('task', input_id='task-1', input_provenance={'origin': 'runtime'}).wait()
        assert second.status == 'completed'
        assert [m.message for m in agent.session.history].count('task') == 1
    finally:
        await worker.close()


def test_compaction_retains_synthetic_units_and_archive_provenance(tmp_path):
    from core.context_archive import FileContextArchive
    from core.agent_runtime.context_management.policy import complete_units, pinned_ids
    agent = Agent(ScriptedModel(), archive_store=FileContextArchive(tmp_path / 'archive'))
    old = agent.apply_context_batch(batch('old'))
    latest = agent.apply_context_batch(batch('latest', 'latest source'))
    entries = agent.session.context_entries()
    assert [[e.entry_id for e in unit] for unit in complete_units(entries)] == [list(old.entry_ids), list(latest.entry_ids)]
    protected, _ = pinned_ids(entries)
    assert set(latest.entry_ids) <= protected
    archive_id = agent.session.archive_entries(entries, reason='test')
    agent.session.commit_context(expected_revision=agent.session.revision,
        retained_ids=list(latest.entry_ids), summary='Prior sources were received as runtime context.',
        archive_id=archive_id, overrides={})
    agent.session.prune_history(2)
    assert agent.session.context_batch_receipt('old') == old
    assert agent.session.context_batch_covered('old')
    assert not agent.session.context_batch_covered('old', require_raw=True)
    assert agent.apply_context_batch(batch('old')) == old
    archive = agent.session.archive_store.load(agent.session.session_id, archive_id)
    assert archive['entries'][0]['context']['provenance']['origin'] == 'runtime_reception'
    JsonSessionStore().save(agent.session, tmp_path / 'compacted.json')
    restored = JsonSessionStore(agent.session.archive_store).load(tmp_path / 'compacted.json')
    assert restored.context_batch_covered('old')
    restored.reset_to_system()
    assert not restored.context_batch_covered('old')


@pytest.mark.asyncio
async def test_lazy_ingestion_compacts_before_fetching_all_sources_and_gates_main(tmp_path):
    from core.context_archive import FileContextArchive
    from core.agent_runtime.options import AgentOptions
    class SummarizingModel(ScriptedModel):
        def invoke(self, messages, **kwargs):
            self.requests.append(messages)
            summary = messages[0].message.startswith('CONTEXT_COMPACTION')
            if summary:
                assert 'protected current task' in messages[1].message
            return {'choices': [{'message': AI('summary' if summary else 'done').to_dict(), 'finish_reason': 'stop'}]}
    model = SummarizingModel()
    agent = Agent(model, options=AgentOptions(context_window=5000, max_tokens=256),
                  archive_store=FileContextArchive(tmp_path / 'archive'))
    worker = AgentWorker(agent, worker_safe=True)
    peak = []
    def batches():
        for index in range(20):
            peak.append(len(agent.session.active))
            if index == 10:
                assert any(r[0].message.startswith('CONTEXT_COMPACTION') for r in model.requests)
            yield batch(f'op-{index}', f'source-{index} ' + 'x' * 700)
    def authorize():
        assert len(model.requests) > 0
        assert all(r[0].message.startswith('CONTEXT_COMPACTION') for r in model.requests)
        assert all(agent.session.context_batch_covered(f'op-{i}') for i in range(20))
    try:
        outcome = await worker.submit('protected current task', context_batches=batches(), before_inference=authorize).wait()
        assert outcome.status == 'completed', outcome.error
        assert outcome.phase == 'execution'
        assert max(peak) < 12
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_cancelled_acknowledgement_never_starts_main_model():
    import asyncio
    from threading import Event
    entered, release = Event(), Event()
    agent = Agent(ScriptedModel(AI('never')))
    worker = AgentWorker(agent, worker_safe=True)
    def acknowledge(receipt):
        entered.set()
        release.wait(3)
    try:
        execution = worker.submit('task', context_batches=[batch()], on_context_applied=acknowledge)
        assert await asyncio.to_thread(entered.wait, 2)
        execution.request_stop()
        release.set()
        outcome = await execution.wait()
        assert outcome.status == 'cancelled'
        assert outcome.phase == 'preparation'
        assert agent.llm.requests == []
    finally:
        release.set()
        await worker.close()


@pytest.mark.asyncio
async def test_oversized_protected_input_blocks_before_reading_unbounded_context():
    from core.agent_runtime.options import AgentOptions
    agent = Agent(ScriptedModel(), options=AgentOptions(context_window=5000, max_tokens=256))
    worker = AgentWorker(agent, worker_safe=True)
    try:
        outcome = await worker.submit('x' * 10000, context_batches=[batch()]).wait()
        assert outcome.phase == 'preparation'
        assert outcome.error_type == 'InputTooLarge'
        assert agent.session.history == []
        assert agent.llm.requests == []
    finally:
        await worker.close()


def test_snapshot_rejects_receipt_with_changed_content_identity(tmp_path):
    import json
    agent = Agent(ScriptedModel())
    agent.apply_context_batch(batch())
    path = tmp_path / 'session.json'
    store = JsonSessionStore()
    store.save(agent.session, path)
    data = json.loads(path.read_text())
    data['context_batches']['reception-1']['digest'] = '0' * 64
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='identity'):
        store.load(path)


@pytest.mark.asyncio
async def test_callback_returning_unawaited_coroutine_blocks_inference():
    agent = Agent(ScriptedModel(AI('never')))
    worker = AgentWorker(agent, worker_safe=True)
    async def async_callback():
        pass
    try:
        outcome = await worker.submit('task', before_inference=lambda: async_callback()).wait()
        assert outcome.status == 'error'
        assert outcome.phase == 'preparation'
        assert outcome.error_type == 'TypeError'
        assert agent.llm.requests == []
    finally:
        await worker.close()


def test_identified_units_cannot_be_split_by_direct_context_commit(tmp_path):
    from core.context_archive import FileContextArchive
    agent = Agent(ScriptedModel(), archive_store=FileContextArchive(tmp_path / 'archive'))
    receipt = agent.apply_context_batch(batch())
    agent.session.add(User('current task'))
    entries = agent.session.context_entries()
    archive_id = agent.session.archive_entries(entries, reason='test')
    with pytest.raises(ValueError, match='complete identified'):
        agent.session.commit_context(expected_revision=agent.session.revision,
            retained_ids=[receipt.entry_ids[1], entries[-1].entry_id], summary='summary',
            archive_id=archive_id, overrides={})


@pytest.mark.asyncio
async def test_timeout_in_admission_is_preparation_without_main_call():
    import time
    agent = Agent(ScriptedModel(AI('never')), run_timeout=0.005)
    worker = AgentWorker(agent, worker_safe=True)
    try:
        result = await worker.submit('task', before_inference=lambda: time.sleep(0.02)).wait()
        assert result.status == 'timeout'
        assert result.phase == 'preparation'
        assert agent.llm.requests == []
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_cancel_during_commit_preparation_does_not_publish_compaction(tmp_path):
    import asyncio
    from threading import Event
    from core.context_archive import FileContextArchive
    from core.agent_runtime.options import AgentOptions
    entered, release = Event(), Event()
    class BlockingSession(Session):
        def prepare_context_commit(self, **kwargs):
            candidate = super().prepare_context_commit(**kwargs)
            entered.set()
            release.wait(3)
            return candidate
    class SummaryModel(ScriptedModel):
        def invoke(self, messages, **kwargs):
            self.requests.append(messages)
            return {'choices': [{'message': AI('summary').to_dict(), 'finish_reason': 'stop'}]}
    agent = Agent(SummaryModel(), options=AgentOptions(context_window=5000, max_tokens=256),
                  session=BlockingSession(), archive_store=FileContextArchive(tmp_path / 'archive'))
    worker = AgentWorker(agent, worker_safe=True)
    try:
        execution = worker.submit('task', context_batches=(batch(f'op-{i}', 'x' * 700) for i in range(20)))
        assert await asyncio.to_thread(entered.wait, 2)
        execution.request_stop()
        release.set()
        result = await execution.wait()
        assert result.status == 'cancelled'
        assert result.phase == 'preparation'
        assert agent.session.checkpoint is None
    finally:
        release.set()
        await worker.close()


def test_archive_rejects_tool_bearing_synthetic_context(tmp_path):
    from uuid import uuid4
    from core.context_archive import FileContextArchive
    from core.session_codec import _encode
    archive = FileContextArchive(tmp_path / 'archive')
    with pytest.raises(ValueError, match='without tools'):
        archive.write(uuid4().hex, entries=[{'entry_id': uuid4().hex,
            'message': _encode(AI(tool_calls=[ToolCall('c', 'effect', {})])),
            'context': {'operation_id': 'op', 'provenance': {'origin': 'runtime'}}}],
            reason='test', revision=0, checkpoint=None, overrides={}, parent_archive_ids=[])


def test_old_standalone_snapshot_needs_no_context_batch_metadata(tmp_path):
    import json
    session = Session()
    session.add(User('standalone history'))
    store = JsonSessionStore()
    path = tmp_path / 'old.json'
    store.save(session, path)
    data = json.loads(path.read_text())
    del data['context_batches']
    path.write_text(json.dumps(data))
    restored = store.load(path)
    assert restored.history[0].message == 'standalone history'
    assert restored.context_batch_receipt('missing') is None


@pytest.mark.asyncio
async def test_identified_current_task_cannot_reuse_a_summarized_historical_input(tmp_path):
    from core.context_archive import FileContextArchive
    from core.context_batch import ContextBatch
    agent = Agent(ScriptedModel(AI('never')), archive_store=FileContextArchive(tmp_path / 'archive'))
    receipt = agent.apply_context_batch(ContextBatch('old-task', (User('old task'),), {'origin': 'runtime'}))
    agent.session.add(User('later task'))
    entries = agent.session.context_entries()
    archive_id = agent.session.archive_entries(entries, reason='test')
    agent.session.commit_context(expected_revision=agent.session.revision,
        retained_ids=[entries[-1].entry_id], summary='old task summary', archive_id=archive_id, overrides={})
    assert agent.session.context_batch_covered(receipt.operation_id)
    worker = AgentWorker(agent, worker_safe=True)
    try:
        outcome = await worker.submit('old task', input_id='old-task', input_provenance={'origin': 'runtime'}).wait()
        assert outcome.phase == 'preparation'
        assert outcome.status == 'error'
        assert agent.llm.requests == []
    finally:
        await worker.close()


def test_repeated_coverage_checks_do_not_reload_archived_source_bodies(tmp_path):
    from core.context_archive import FileContextArchive
    class CountingArchive(FileContextArchive):
        loads = 0
        def load(self, *args):
            self.loads += 1
            return super().load(*args)
    archive = CountingArchive(tmp_path / 'archive')
    agent = Agent(ScriptedModel(), archive_store=archive)
    agent.apply_context_batch(batch())
    agent.session.add(User('current task'))
    entries = agent.session.context_entries()
    archive_id = agent.session.archive_entries(entries, reason='test')
    agent.session.commit_context(expected_revision=agent.session.revision,
        retained_ids=[entries[-1].entry_id], summary='summary', archive_id=archive_id, overrides={})
    archive.loads = 0
    for _ in range(10):
        assert agent.session.context_batch_covered('reception-1')
    assert archive.loads == 0


def test_compaction_cannot_publish_archive_that_lost_context_provenance(tmp_path):
    from core.context_archive import FileContextArchive
    from core.session_codec import _encode
    agent = Agent(ScriptedModel(), archive_store=FileContextArchive(tmp_path / 'archive'))
    agent.apply_context_batch(batch())
    agent.session.add(User('current task'))
    entries = agent.session.context_entries()
    archive_id = agent.session.archive_store.write(agent.session.session_id,
        entries=[{'entry_id': e.entry_id, 'message': _encode(e.message)} for e in entries],
        reason='malformed-adapter', revision=agent.session.revision, checkpoint=None,
        overrides={}, parent_archive_ids=[])
    with pytest.raises(ValueError, match='provenance'):
        agent.session.commit_context(expected_revision=agent.session.revision,
            retained_ids=[entries[-1].entry_id], summary='summary', archive_id=archive_id, overrides={})
    assert agent.session.checkpoint is None
    assert agent.session.context_batch_covered('reception-1', require_raw=True)


@pytest.mark.parametrize('projection', ['active_ids', 'history_ids'])
@pytest.mark.parametrize('mutation', ['reverse', 'split', 'partial'])
def test_snapshot_rejects_disordered_or_partial_identified_units(tmp_path, projection, mutation):
    import json
    agent = Agent(ScriptedModel())
    receipt = agent.apply_context_batch(batch())
    agent.session.add(User('other task'))
    path = tmp_path / 'session.json'
    store = JsonSessionStore()
    store.save(agent.session, path)
    data = json.loads(path.read_text())
    if mutation == 'reverse':
        data[projection][:2] = reversed(data[projection][:2])
    elif mutation == 'split':
        first, second, other = data[projection]
        data[projection] = [first, other, second]
    else:
        data[projection].remove(receipt.entry_ids[0])
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='context.*unit'):
        store.load(path)


def test_current_coverage_rejects_reordered_raw_unit():
    agent = Agent(ScriptedModel())
    agent.apply_context_batch(batch())
    agent.session._active_ids.reverse()
    assert not agent.session.context_batch_covered('reception-1')
    with pytest.raises(ValueError, match='coverage'):
        agent.apply_context_batch(batch())


def test_history_pruning_keeps_identified_units_whole(tmp_path):
    from core.context_archive import FileContextArchive
    agent = Agent(ScriptedModel(), archive_store=FileContextArchive(tmp_path / 'archive'))
    agent.apply_context_batch(batch())
    agent.session.add(User('current task'))
    agent.session.prune_history(2)
    assert [m.message for m in agent.session.history] == ['current task']
    assert agent.session.context_batch_covered('reception-1')
    path = tmp_path / 'session.json'
    JsonSessionStore().save(agent.session, path)
    assert JsonSessionStore(agent.session.archive_store).load(path).context_batch_covered('reception-1')
