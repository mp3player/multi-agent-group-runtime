"""Explicit response repair and retryable scheduler backpressure."""

import asyncio
import sqlite3
from threading import Event

import pytest

from core.agent import Agent
from group import CollaborationProfile, DispatchPlan, GroupLimits, GroupRuntime, OnDemandStrategy, RunProposal
from group.errors import CapacityError, ConflictError, GroupError, LifecycleError
from models import AI, ToolCall
from tests.runtime_fakes import ScriptedModel


def public_reply(*message_ids):
    return AI(tool_calls=[
        *(ToolCall(f'post-{index}', 'group_post', {'content': 'Recovered answer', 'reply_to': message_id})
          for index, message_id in enumerate(message_ids)),
        ToolCall('yield', 'group_yield', {}),
    ])


@pytest.mark.parametrize('stage', ['observation', 'commit'])
async def test_driver_retries_real_store_backpressure_without_cancelling_work(tmp_path, monkeypatch, stage):
    class PrivateStrategy(OnDemandStrategy):
        # Isolate scheduler retries from separately rejected member tool commands.
        profile = CollaborationProfile(tools=())
    model = ScriptedModel(AI('Private result'))
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)}, worker_safe=True,
                                      strategy=PrivateStrategy(), limits=GroupLimits(queue_jobs=1))
    entered, release = Event(), Event()
    blockers = []
    timer = None
    try:
        scope = await group.open_invocation('s')
        await group.request(scope, 'Respond', key='r')
        name = 'scheduling_view' if stage == 'observation' else 'commit_plan'
        original = getattr(group, name)
        contested = False

        async def once(*args, **kwargs):
            nonlocal contested, timer
            if not contested:
                contested = True
                def hold(db):
                    entered.set()
                    assert release.wait(2)
                blockers.append(asyncio.create_task(group._mutate(hold)))
                assert await asyncio.to_thread(entered.wait, 1)
                timer = asyncio.get_running_loop().call_later(0.05, release.set)
            return await original(*args, **kwargs)

        monkeypatch.setattr(group, name, once)
        result = await asyncio.wait_for(group.drive(scope), 2)
        assert result.status == 'waiting' and result.admitted_count == 1
        assert len(model.requests) == 1
        assert (await group.snapshot(scope)).state == 'open'
        assert (await group.opportunities(scope)).items[0].outcome == 'completed'
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)
    finally:
        release.set()
        if timer:
            timer.cancel()
        await asyncio.gather(*blockers, return_exceptions=True)
        await group.close()


@pytest.mark.parametrize('first_failed', [False, True])
async def test_explicit_resolution_unblocks_completion_and_preserves_original_outcome(tmp_path, first_failed):
    path = tmp_path / 'g.sqlite'
    model = ScriptedModel() if first_failed else ScriptedModel(AI('Private only'))
    agent = Agent(model)
    async with await GroupRuntime.create(path, {'a': agent}, worker_safe=True, strategy=OnDemandStrategy(),
                                        limits=GroupLimits(max_runs=2)) as group:
        scope = await group.open_invocation('s')
        first = await group.request(scope, 'Reply publicly', key='first')
        assert (await group.drive(scope)).status == 'needs_input'
        original = (await group.assignments(scope)).items[0]
        retry = await group.request(scope, 'Publish the answer for both requests', key='retry', reply_to=first.message_id)
        model.responses.append(public_reply(first.message_id, retry.message_id))
        assert (await group.drive(scope)).status == 'needs_input'
        reply = next(m for m in (await group.history(scope)).items if m.sender == 'a' and m.reply_to == first.message_id)
        resolution = await group.resolve_response(scope, first.opportunity_ids[0], reply.id, key='resolve')
        assert resolution.message_id == reply.id and resolution.opportunity_ids == first.opportunity_ids
        view = await group.scheduling_view(scope)
        evidence = view.evidence[0]
        assert evidence.outcome == original.outcome and evidence.error == original.error
        assert evidence.reply_id is None
        assert evidence.resolved_by == reply.run_id and evidence.resolution_reply_id == reply.id
        assert evidence.satisfied
        assert (await group.assignments(scope)).items[0] == original
        result = await group.drive(scope)
        assert result.status == 'waiting' and not result.missing_reply_ids and result.admitted_count == 2
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)
        assert await group.resolve_response(scope, first.opportunity_ids[0], reply.id, key='resolve') == resolution
    async with await GroupRuntime.create(path, {'a': agent}, worker_safe=True, strategy=OnDemandStrategy()) as group:
        assert await group.resolve_response(scope, first.opportunity_ids[0], reply.id, key='resolve') == resolution
        evidence = (await group.scheduling_view(scope)).evidence[0]
        assert evidence.satisfied and evidence.outcome == original.outcome


@pytest.mark.parametrize('invalid', ['external', 'other_member', 'wrong_message', 'failed_run'])
async def test_response_resolution_rejects_unrelated_or_unsuccessful_evidence(tmp_path, invalid):
    a_model, b_model = ScriptedModel(AI('Private')), ScriptedModel()
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(a_model), 'b': Agent(b_model)},
                                        worker_safe=True, strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        first = await group.request(scope, 'A must answer', key='r', recipients=('a',))
        await group.drive(scope)
        if invalid == 'external':
            message = await group.post(scope, 'Pretend reply', key='external', reply_to=first.message_id)
            reply_id = message.message_id
        else:
            member = 'b' if invalid == 'other_member' else 'a'
            model = b_model if member == 'b' else a_model
            other = await group.post(scope, 'Different topic', key='other')
            target = other.message_id if invalid == 'wrong_message' else first.message_id
            response = public_reply(target)
            if invalid == 'failed_run':
                response.tool_calls.pop()  # Commit the post, then fail the next model call.
            model.responses.append(response)
            await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
                runs=(RunProposal(member, 'Post', origin_key='repair'),)))
            await group.launch_ready(scope)
            await group.wait_idle()
            reply_id = (await group.history(scope)).items[-1].id
        before = await group.snapshot(scope)
        with pytest.raises(GroupError):
            await group.resolve_response(scope, first.opportunity_ids[0], reply_id, key='resolve')
        assert (await group.snapshot(scope)).revision == before.revision
        assert not (await group.scheduling_view(scope)).evidence[0].satisfied
        with pytest.raises(LifecycleError):
            await group.finish(scope, revision=before.revision)


async def test_resolution_keys_conflict_without_replacing_accepted_evidence(tmp_path):
    model = ScriptedModel(AI('Private'))
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)}, worker_safe=True,
                                        strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        request = await group.request(scope, 'Reply', key='r')
        await group.drive(scope)
        model.responses.append(public_reply(request.message_id, request.message_id))
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('a', 'Repair', origin_key='repair'),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        replies = [m for m in (await group.history(scope)).items if m.sender == 'a']
        accepted = await group.resolve_response(scope, request.opportunity_ids[0], replies[0].id, key='resolve')
        for key in ('resolve', 'different-key'):
            with pytest.raises(ConflictError):
                await group.resolve_response(scope, request.opportunity_ids[0], replies[1].id, key=key)
        assert await group.resolve_response(scope, request.opportunity_ids[0], replies[0].id, key='resolve') == accepted


async def test_permanent_plan_capacity_error_is_not_retried(tmp_path):
    from group.scheduling import SchedulingDecision

    class OversizedStrategy(OnDemandStrategy):
        calls = 0

        def decide(self, view):
            self.calls += 1
            return SchedulingDecision(DispatchPlan(view.snapshot.invocation_id, view.snapshot.revision,
                runs=(RunProposal('a', '', origin_key='a'), RunProposal('b', '', origin_key='b'))))

    strategy = OversizedStrategy()
    async with await GroupRuntime.create(tmp_path / 'g.sqlite',
            {'a': Agent(ScriptedModel()), 'b': Agent(ScriptedModel())}, worker_safe=True,
            strategy=strategy, limits=GroupLimits(max_queued=1)) as group:
        scope = await group.open_invocation('s')
        with pytest.raises(CapacityError, match='Assignment queue'):
            await asyncio.wait_for(group.drive(scope), 1)
        assert strategy.calls == 1
        snapshot = await group.snapshot(scope)
        assert snapshot.state == 'terminal' and snapshot.reason == 'error'
        assert snapshot.admitted_count == 0


async def test_store_backpressure_obeys_deadline_and_keeps_owned_driver_after_cancelled_waiter(tmp_path, monkeypatch):
    entered, release, rejected = Event(), Event(), asyncio.Event()
    model = ScriptedModel(AI('Must not start'))
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)}, worker_safe=True,
        strategy=OnDemandStrategy(), limits=GroupLimits(queue_jobs=1, invocation_timeout=0.15))
    blockers, driver, timer = [], None, None
    try:
        scope = await group.open_invocation('s')
        await group.request(scope, 'Pending work', key='r')
        original = group.commit_plan
        started = False

        async def contested(plan):
            nonlocal started, timer
            if not started:
                started = True
                def hold(db):
                    entered.set()
                    assert release.wait(2)
                blockers.append(asyncio.create_task(group._mutate(hold)))
                assert await asyncio.to_thread(entered.wait, 1)
                # Outlast the deadline; the control write must still await SQLite.
                timer = asyncio.get_running_loop().call_later(0.25, release.set)
            try:
                return await original(plan)
            except CapacityError:
                rejected.set()
                raise

        monkeypatch.setattr(group, 'commit_plan', contested)
        driver = asyncio.create_task(group.drive(scope))
        await asyncio.wait_for(rejected.wait(), 1)
        driver.cancel()
        with pytest.raises(asyncio.CancelledError):
            await driver
        result = await asyncio.wait_for(group.drive(scope), 2)
        assert result.status == 'timeout' and result.admitted_count == 0
        assert not model.requests
        snapshot = await group.snapshot(scope)
        assert snapshot.state == 'terminal' and snapshot.reason == 'timeout'
        assert (await group.opportunities(scope)).items[0].outcome == 'timeout'
    finally:
        release.set()
        if timer:
            timer.cancel()
        await asyncio.gather(*blockers, return_exceptions=True)
        await group.close()
        if driver:
            await asyncio.gather(driver, return_exceptions=True)


async def test_version_two_store_requires_import_without_losing_receipts_or_outcomes(tmp_path):
    path = tmp_path / 'g.sqlite'
    agent = Agent(ScriptedModel())
    async with await GroupRuntime.create(path, {'a': agent}, worker_safe=True) as group:
        scope = await group.open_invocation('old')
        receipt = await group.post(scope, 'Keep original', key='old')
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE response_resolutions')
        db.execute('UPDATE metadata SET version=2')
    from group.errors import GroupError
    with pytest.raises(GroupError, match='explicit reception schema import'):
        await GroupRuntime.create(path, {'a': agent}, worker_safe=True)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT content FROM messages WHERE id=?', (receipt.message_id,)).fetchone()[0] == 'Keep original'
        assert db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0] == 1
        assert db.execute('SELECT reason FROM invocations').fetchone()[0] == 'completed'
        assert db.execute('SELECT version FROM metadata').fetchone()[0] == 2


async def test_partial_resolution_does_not_hide_other_obligations_of_a_failed_run(tmp_path):
    model = ScriptedModel()
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)}, worker_safe=True,
                                        strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        first = await group.request(scope, 'First', key='first')
        second = await group.request(scope, 'Second', key='second')
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('a', 'Answer both', first.opportunity_ids + second.opportunity_ids),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        assert (await group.drive(scope)).status == 'needs_input'
        model.responses.append(public_reply(first.message_id, second.message_id))
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('a', 'Repair both', origin_key='repair'),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        replies = {m.reply_to: m.id for m in (await group.history(scope)).items if m.sender == 'a'}
        await group.resolve_response(scope, first.opportunity_ids[0], replies[first.message_id], key='resolve-first')
        partial = await group.drive(scope)
        assert partial.status == 'needs_input' and partial.missing_reply_ids == second.opportunity_ids
        with pytest.raises(LifecycleError):
            await group.finish(scope, revision=(await group.snapshot(scope)).revision)
        await group.resolve_response(scope, second.opportunity_ids[0], replies[second.message_id], key='resolve-second')
        assert (await group.drive(scope)).status == 'waiting'
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)


async def test_reply_cannot_resolve_original_until_its_execution_settles(tmp_path):
    entered, release = Event(), Event()

    class PausedModel(ScriptedModel):
        def invoke(self, *args, **kwargs):
            if len(self.requests) == 2:
                entered.set()
                assert release.wait(2)
            return super().invoke(*args, **kwargs)

    model = PausedModel(AI('Private'))
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)}, worker_safe=True,
                                      strategy=OnDemandStrategy())
    try:
        scope = await group.open_invocation('s')
        request = await group.request(scope, 'Answer', key='r')
        await group.drive(scope)
        response = public_reply(request.message_id)
        response.tool_calls.pop()
        model.responses.extend((response, AI('Complete')))
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('a', 'Repair', origin_key='repair'),)))
        await group.launch_ready(scope)
        assert await asyncio.to_thread(entered.wait, 1)
        reply = (await group.history(scope)).items[-1]
        assert reply.reply_to == request.message_id
        with pytest.raises(GroupError):
            await group.resolve_response(scope, request.opportunity_ids[0], reply.id, key='resolve')
        release.set()
        await group.wait_idle()
        await group.resolve_response(scope, request.opportunity_ids[0], reply.id, key='resolve')
        assert (await group.drive(scope)).status == 'waiting'
    finally:
        release.set()
        await group.close()
