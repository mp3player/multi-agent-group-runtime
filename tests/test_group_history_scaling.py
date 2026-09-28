"""Bounded history reads retain cancellation and fresh coverage checks."""

import asyncio
from math import ceil
from threading import Event

import pytest

from core.agent import Agent
from core.agent_runtime.options import AgentOptions
from core.context_archive import FileContextArchive
from group import DispatchPlan, GroupLimits, GroupRuntime, RunProposal
from group.member_context import MemberContext
from group.profiles import CollaborationProfile
from models import AI
from tests.runtime_fakes import ScriptedModel


async def admit(group, scope, receipts):
    return (await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
        runs=(RunProposal('a', 'Read all assigned sources',
                          tuple(op for receipt in receipts for op in receipt.opportunity_ids)),))))[0]


@pytest.mark.parametrize('history_size', [9, 39, 79])
async def test_preparation_store_crossings_scale_with_pages(tmp_path, monkeypatch, history_size):
    # Reintroducing a read per source must exceed the page-scaled store budget.
    reads = []
    original_read = MemberContext._read

    def measured(self, operation):
        reads.append(1)
        return original_read(self, operation)

    monkeypatch.setattr(MemberContext, '_read', measured)
    model = ScriptedModel(AI('done'))
    limits = GroupLimits(page_size=10)
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)},
            worker_safe=True, limits=limits) as group:
        scope = await group.open_invocation('s')
        for index in range(history_size):
            await group.post(scope, f'Original source {index}.', key=f'p{index}')
        request = await group.request(scope, 'Answer the assigned task', key='r')
        await admit(group, scope, [request])
        await group.launch_ready(scope)
        await group.wait_idle()

        run = (await group.assignments(scope)).items[0]
        assert run.outcome == 'completed', run.error
        assert len(model.requests) == 1
        messages = '\n'.join(message.message for message in model.requests[0])
        assert all(f'Original source {index}.' in messages for index in range(history_size))
        assert messages.count('Answer the assigned task') == 1
        assert len(reads) <= 2 * ceil((history_size + 1) / limits.page_size)


@pytest.mark.parametrize('cancel_during_validation', [False, True])
async def test_cancel_stops_fully_covered_history_within_the_current_source(
        tmp_path, monkeypatch, cancel_during_validation):
    # Omitting checks in covered-source scans must keep walking after cancellation.
    entered, release, stopped = Event(), Event(), Event()
    model = ScriptedModel(AI('first'), AI('must not run'))
    agent = Agent(model)
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': agent},
                                     worker_safe=True, limits=GroupLimits(page_size=5))
    cancellation = None
    try:
        scope = await group.open_invocation('s')
        requests = [await group.request(scope, f'Previously assigned source {index}', key=f'r{index}')
                    for index in range(20)]
        await admit(group, scope, requests)
        await group.launch_ready(scope)
        await group.wait_idle()
        covered_operation = await group.store.read(lambda db: db.execute(
            'SELECT operation_id FROM context_sources LIMIT 1').fetchone()[0])
        request = await group.request(scope, 'Second task', key='next')
        await admit(group, scope, [request])
        original_covered = agent.session.context_batch_covered
        original_stop = agent.run_state.request_stop
        original_validate = MemberContext.validate
        validating = False
        inspected = []

        def validating_pass(self):
            nonlocal validating
            validating = True
            return original_validate(self)

        def covered(operation, **kwargs):
            result = original_covered(operation, **kwargs)
            if operation == covered_operation and validating == cancel_during_validation:
                inspected.append(operation)
                if len(inspected) == 1:
                    entered.set()
                    assert release.wait(5)
            return result

        def stop(owner, run):
            original_stop(owner, run)
            stopped.set()

        monkeypatch.setattr(MemberContext, 'validate', validating_pass)
        monkeypatch.setattr(agent.session, 'context_batch_covered', covered)
        monkeypatch.setattr(agent.run_state, 'request_stop', stop)
        await group.launch_ready(scope)
        assert await asyncio.to_thread(entered.wait, 3)
        cancellation = asyncio.create_task(group.cancel(scope))
        assert await asyncio.to_thread(stopped.wait, 3)
        release.set()
        await asyncio.wait_for(cancellation, 5)

        assert len(inspected) == 1
        assert len(model.requests) == 1
        runs = (await group.assignments(scope)).items
        assert runs[-1].outcome == 'cancelled'
    finally:
        release.set()
        if cancellation is not None:
            await asyncio.gather(cancellation, return_exceptions=True)
        await group.close()


async def test_validation_rereads_application_receipts_added_after_preparation(tmp_path, monkeypatch):
    # A cached no-application result would miss the changed binding at admission.
    import group.member_context as context
    original_acknowledge = context.acknowledge
    model = ScriptedModel(AI('must not run'))
    agent = Agent(model)
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': agent}, worker_safe=True) as group:
        scope = await group.open_invocation('s')
        source = await group.post(scope, 'Original passive source', key='p')
        request = await group.request(scope, 'Protected task', key='r')
        await admit(group, scope, [request])

        def changed_receipt(db, member, receipt, source_ids=()):
            original_acknowledge(db, member, receipt, source_ids)
            if source_ids:
                db.execute('INSERT INTO context_sources VALUES (?,?,?,?)',
                           (member, source.message_id, receipt.operation_id, receipt.session_id))
                db.execute('UPDATE context_applications SET digest=? WHERE operation_id=?',
                           ('changed-after-preparation', receipt.operation_id))

        monkeypatch.setattr(context, 'acknowledge', changed_receipt)
        await group.launch_ready(scope)
        await group.wait_idle()
        run = (await group.assignments(scope)).items[0]
        assert run.state == 'blocked'
        assert 'Previously active source lacks verified current context coverage' in run.error
        assert model.requests == []
        assert [message.content for message in (await group.history(scope)).items] == [
            'Original passive source', 'Protected task']


async def test_batched_existing_receipts_do_not_hide_missing_current_coverage(tmp_path):
    model = ScriptedModel(AI('first'), AI('must not run'))
    agent = Agent(model)
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': agent}, worker_safe=True) as group:
        scope = await group.open_invocation('s')
        first = await group.request(scope, 'Original protected task', key='first')
        await admit(group, scope, [first])
        await group.launch_ready(scope)
        await group.wait_idle()
        assert (await group.assignments(scope)).items[0].outcome == 'completed'
        agent.session.clear_active()
        second = await group.request(scope, 'New task after lost context', key='second')
        await admit(group, scope, [second])
        await group.launch_ready(scope)
        await group.wait_idle()
        run = (await group.assignments(scope)).items[-1]
        assert run.state == 'blocked'
        assert 'current context coverage' in run.error
        assert len(model.requests) == 1


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
async def test_post_compaction_validation_rechecks_archived_originals(tmp_path, monkeypatch, damage):
    # Neither session revision nor previously verified metadata can hide file damage.
    class SummarizingModel(ScriptedModel):
        def invoke(self, messages, **kwargs):
            self.requests.append(messages)
            return {'choices': [{'message': AI('Retained source summary.').to_dict(),
                                 'finish_reason': 'stop'}]}

    model = SummarizingModel()
    archive = FileContextArchive(tmp_path / 'archive')
    agent = Agent(model, options=AgentOptions(context_window=6000, max_tokens=256),
                  archive_store=archive)
    original_validate = MemberContext.validate
    damaged = []

    def damage_before_validation(self):
        session = agent.session
        assert session.checkpoint is not None
        session.validate_archives()
        revision = session.revision
        path = archive.root / session.session_id / (session.checkpoint['archive_id'] + '.json')
        damaged.append((path, path.read_bytes()))
        if damage == 'missing':
            path.unlink()
        else:
            path.chmod(0o600)
            path.write_bytes(damaged[-1][1].replace(b'"version":1', b'"version":2', 1))
            path.chmod(0o400)
        assert session.revision == revision
        return original_validate(self)

    monkeypatch.setattr(MemberContext, 'validate', damage_before_validation)
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': agent}, worker_safe=True,
            profile=CollaborationProfile(tools=())) as group:
        scope = await group.open_invocation('s')
        for index in range(20):
            await group.post(scope, f'Original {index}: ' + 'x' * 700, key=f'p{index}')
        request = await group.request(scope, 'Only execute with complete source coverage', key='r')
        await admit(group, scope, [request])
        await group.launch_ready(scope)
        await group.wait_idle()
        run = (await group.assignments(scope)).items[0]
        assert damaged and run.state == 'blocked', run.error
        assert model.requests and all(request[0].message.startswith('CONTEXT_COMPACTION')
                                      for request in model.requests)
        path, original = damaged[0]
        if path.exists():
            path.chmod(0o600)
        path.write_bytes(original)
        path.chmod(0o400)
        session_operations = await group.store.read(lambda db: db.execute(
            'SELECT operation_id FROM context_applications').fetchall())
        assert all(agent.session.context_batch_covered(operation) for (operation,) in session_operations)
