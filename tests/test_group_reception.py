"""Complete member reception, independent of business response obligations."""

import sqlite3

import pytest

from core.agent import Agent
from group import DispatchPlan, GroupLimits, GroupRuntime, RunProposal
from group.errors import GroupError
from tests.runtime_fakes import ScriptedModel
from models import AI


async def test_receive_covers_every_page_without_model_calls_or_settling_requests(tmp_path):
    models = {'b': ScriptedModel(), 'c': ScriptedModel()}
    limits = GroupLimits(page_size=2, max_page_size=3)
    async with await GroupRuntime.create(tmp_path / 'g.sqlite',
            {name: Agent(model) for name, model in models.items()},
            worker_safe=True, limits=limits) as group:
        scope = await group.open_invocation('work')
        earlier = await group.request(scope, 'C must review reliability', key='c', recipients=('c',))
        for index in range(8):
            await group.post(scope, f'Background {index}', key=f'p{index}')
        await group.request(scope, 'B must calculate cost', key='b', recipients=('b',))

        await group.receive(scope)

        for name, model in models.items():
            status = await group.reception(scope, name)
            assert status.received_count == 10
            assert status.pending_count == 0
            assert status.batch_count > 1
            assert model.requests == []
        opportunities = (await group.opportunities(scope, limit=3)).items
        assert len(opportunities) == 2
        assert all(item.state == 'pending' for item in opportunities)
        assert opportunities[0].id == earlier.opportunity_ids[0]
        assert (await group.snapshot(scope)).admitted_count == 0
        before = (await group.snapshot(scope)).revision
        await group.receive(scope)
        assert (await group.snapshot(scope)).revision == before


async def test_receive_catches_accepted_sources_from_cancelled_scope_without_reopening_it(tmp_path):
    async with await GroupRuntime.create(tmp_path / 'g.sqlite',
            {'c': Agent(ScriptedModel())}, worker_safe=True) as group:
        old = await group.open_invocation('old')
        await group.request(old, 'Retain this source, cancel this task', key='old', recipients=('c',))
        await group.cancel(old)
        current = await group.open_invocation('current')
        await group.post(current, 'Current information', key='new')

        await group.receive(current)

        assert (await group.reception(old, 'c')).received_count == 1
        assert (await group.reception(current, 'c')).received_count == 1
        assert (await group.snapshot(old)).reason == 'cancelled'
        assert (await group.opportunities(old)).items[0].outcome == 'cancelled'
        assert (await group.snapshot(current)).admitted_count == 0


async def test_old_nonempty_schema_does_not_assume_members_received_history(tmp_path):
    path = tmp_path / 'g.sqlite'
    async with await GroupRuntime.create(path, {'a': Agent(ScriptedModel())}, worker_safe=True) as group:
        scope = await group.open_invocation('old')
        await group.post(scope, 'Original', key='p')
        await group.cancel(scope)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE metadata SET version=3')
    with pytest.raises(GroupError, match='reception|schema|import'):
        await GroupRuntime.create(path, {'a': Agent(ScriptedModel())}, worker_safe=True)


async def test_delayed_activation_receives_early_sources_and_freezes_queued_input(tmp_path):
    model = ScriptedModel(AI('first'), AI('second'))
    agent = Agent(model)
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'c': agent},
            worker_safe=True, limits=GroupLimits(page_size=2, max_page_size=3)) as group:
        scope = await group.open_invocation('s')
        original = 'Reliability requires durable acknowledgement after commit.'
        await group.post(scope, original, key='brief')
        for index in range(12):
            await group.post(scope, f'Informational update {index}', key=f'p{index}')
        request = await group.request(scope, 'Review the original requirements', key='review', recipients=('c',))
        snapshot = await group.snapshot(scope)
        await group.commit_plan(DispatchPlan(scope, snapshot.revision,
            runs=(RunProposal('c', 'Review', request.opportunity_ids),)))
        await group.post(scope, 'LATER_THAN_ADMISSION', key='later')
        await group.launch_ready(scope)
        await group.wait_idle()
        first = '\n'.join(message.message for message in model.requests[0])
        assert original in first
        assert all(f'Informational update {i}' in first for i in range(12))
        assert 'LATER_THAN_ADMISSION' not in first
        assert first.count('Review the original requirements') == 1
        second = await group.request(scope, 'Continue', key='next', recipients=('c',))
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('c', 'Continue', second.opportunity_ids),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        assert 'LATER_THAN_ADMISSION' in '\n'.join(m.message for m in model.requests[1])
        assert not any(m.sender == 'c' for m in (await group.history(scope, limit=3)).items)
