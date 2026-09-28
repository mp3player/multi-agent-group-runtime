"""Group commands never substitute for a scheduling decision."""

import asyncio

import pytest

from core.agent import Agent
from models import AI
from tests.runtime_fakes import ScriptedModel


async def make_group(tmp_path, **kwargs):
    from group import GroupRuntime
    agents = {name: Agent(ScriptedModel(AI(message=f'{name} private answer'))) for name in ('a', 'b')}
    return await GroupRuntime.create(tmp_path / 'group.sqlite', agents, worker_safe=True, **kwargs)


async def test_requests_wait_for_explicit_plan_and_results_stay_private(tmp_path):
    from group import DispatchPlan, RunProposal
    group = await make_group(tmp_path)
    try:
        scope = await group.open_invocation('discussion')
        receipt = await group.request(scope, 'Review this', key='request-1', recipients=('a', 'b'))
        initial = await group.snapshot(scope)
        assert initial.pending_count == 2
        assert initial.active_count == initial.queued_count == 0
        assert all(not worker.agent.llm.requests for worker in group.workers.values())
        assert all(message.role == 'system' for worker in group.workers.values()
                   for message in worker.agent.session.history)
        plan = DispatchPlan(scope, initial.revision, runs=(
            RunProposal('a', 'Check correctness', (receipt.opportunity_ids[0],)),
            RunProposal('b', 'Check usability', (receipt.opportunity_ids[1],)),
        ))
        assignments = await group.commit_plan(plan)
        assert len(assignments) == 2
        assert (await group.snapshot(scope)).queued_count == 2
        await group.launch_ready(scope)
        await group.wait_idle()
        snap = await group.snapshot(scope)
        assert snap.pending_count == snap.queued_count == snap.active_count == 0
        assert {run.outcome for run in snap.assignments.items} == {'completed'}
        assert [msg.content for msg in (await group.history(scope)).items] == ['Review this']
        await group.finish(scope, revision=snap.revision)
    finally:
        await group.close()


async def test_idempotency_survives_close_but_new_commands_do_not(tmp_path):
    from group.errors import ConflictError, LifecycleError
    group = await make_group(tmp_path)
    try:
        scope = await group.open_invocation('discussion')
        receipt = await group.post(scope, 'original', key='same')
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)
        assert await group.post(scope, 'original', key='same') == receipt
        with pytest.raises(ConflictError):
            await group.post(scope, 'changed', key='same')
        with pytest.raises(LifecycleError):
            await group.post(scope, 'late', key='new')
        assert len((await group.history(scope)).items) == 1
    finally:
        await group.close()


async def test_stale_and_invalid_plans_roll_back_all_dispositions_and_state(tmp_path):
    from group import DispatchPlan, RunProposal, Disposition
    from group.errors import GroupError, StalePlanError
    group = await make_group(tmp_path)
    try:
        scope = await group.open_invocation('discussion')
        receipt = await group.request(scope, 'work', key='r', recipients=('a', 'b'))
        snapshot = await group.snapshot(scope)
        await group.post(scope, 'new fact', key='p')
        with pytest.raises(StalePlanError):
            await group.commit_plan(DispatchPlan(scope, snapshot.revision, runs=(RunProposal('a', '', (receipt.opportunity_ids[0],)),)))
        snapshot = await group.snapshot(scope)
        with pytest.raises(GroupError):
            await group.commit_plan(DispatchPlan(scope, snapshot.revision,
                runs=(RunProposal('missing', '', (receipt.opportunity_ids[1],)),),
                dispositions=(Disposition(receipt.opportunity_ids[0], 'refused'),), policy_state='{"phase":2}'))
        after = await group.snapshot(scope)
        assert after.revision == snapshot.revision
        assert after.pending_count == 2 and after.policy_state == '{}'
        with pytest.raises(GroupError):
            await group.finish(scope, revision=after.revision)
    finally:
        await group.close()


async def test_bounded_pages_keep_originals_and_wait_cannot_miss_commit(tmp_path):
    group = await make_group(tmp_path)
    try:
        scope = await group.open_invocation('discussion')
        before = await group.snapshot(scope)
        for index in range(5):
            await group.post(scope, f'fact {index}', key=f'p{index}')
        revision = await asyncio.wait_for(group.wait_for_change(before.revision), 1)
        assert revision > before.revision
        first = await group.history(scope, limit=2)
        assert len(first.items) == 2 and not first.exhausted
        await group.post(scope, 'later', key='p5')
        second = await group.history(scope, after=first.next_cursor, high_water=first.high_water, limit=2)
        third = await group.history(scope, after=second.next_cursor, high_water=first.high_water, limit=2)
        assert [m.content for m in first.items + second.items + third.items] == [f'fact {i}' for i in range(5)]
        assert third.exhausted
    finally:
        await group.close()


async def test_reopen_unfinished_invocation_requires_reconciliation(tmp_path):
    from group.errors import RecoveryRequiredError
    group = await make_group(tmp_path)
    scope = await group.open_invocation('discussion')
    # Simulate a process loss after a committed command, without running a model.
    await group.post(scope, 'retained', key='p')
    await group.store.close()
    for worker in group.workers.values():
        await worker.close()
    with pytest.raises(RecoveryRequiredError):
        await make_group(tmp_path)
