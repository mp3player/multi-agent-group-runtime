"""Driver ownership and fair admission using real Agents, workers, and stores."""

import asyncio
from threading import Event
from types import SimpleNamespace

import pytest

from core.agent import Agent
from core.agent_runtime.run_state import AgentBusyError
from group import DispatchPlan, GroupLimits, GroupRuntime, RunProposal
from group.profiles import CollaborationProfile
from models import AI
from tests.runtime_fakes import ScriptedModel


class BlockingFirstModel(ScriptedModel):
    """Block only the first provider call, with an explicit bounded release."""

    def __init__(self, *responses):
        super().__init__(*responses)
        self.entered = Event()
        self.release = Event()
        self.returned = Event()
        self.block_next = True

    def invoke(self, *args, **kwargs):
        if self.block_next:
            self.block_next = False
            self.entered.set()
            if not self.release.wait(5):
                raise TimeoutError('Test did not release the blocked provider')
            try:
                return super().invoke(*args, **kwargs)
            finally:
                self.returned.set()
        return super().invoke(*args, **kwargs)


async def create_group(tmp_path, members, *, limits=None):
    from group.strategies import OnDemandStrategy

    selection = OnDemandStrategy()
    strategy = SimpleNamespace(
        name=selection.name,
        version=selection.version,
        decide=selection.decide,
        profile=CollaborationProfile(
            name='lifecycle-final-publication', tools=(), publish_final=True,
            require_public_reply=True, instructions='Return the requested result.',
        ),
    )
    return await GroupRuntime.create(
        tmp_path / 'driver.sqlite', members, worker_safe=True,
        limits=limits or GroupLimits(max_runs=10), strategy=strategy,
    )


async def wait_entered(model):
    assert await asyncio.wait_for(asyncio.to_thread(model.entered.wait, 2), 3)


async def wait_for_state(group, scope, state):
    while True:
        snapshot = await group.snapshot(scope)
        if snapshot.state == state:
            return snapshot
        await group.wait_for_change(snapshot.revision)


async def close_group(group, *waiters):
    try:
        await asyncio.wait_for(group.close(), 3)
    finally:
        for waiter in waiters:
            if not waiter.done():
                waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)


async def test_deadline_keeps_driver_and_agent_owned_until_blocked_worker_returns(tmp_path):
    model = BlockingFirstModel(AI('Too late to publish'))
    agent = Agent(model)
    group = await create_group(tmp_path, {'a': agent}, limits=GroupLimits(invocation_timeout=0.25))
    waiters = []
    try:
        scope = await group.open_invocation('deadline')
        request = await group.request(scope, 'Slow work', key='request', recipients=('a',))
        driver = asyncio.create_task(group.drive(scope))
        waiters.append(driver)
        await wait_entered(model)

        closing = await asyncio.wait_for(wait_for_state(group, scope, 'closing'), 2)
        assert closing.reason == 'timeout'
        assert closing.active_count == 1
        assert not driver.done()
        assert not model.returned.is_set()
        with pytest.raises(AgentBusyError):
            agent.run('Overlapping work')

        model.release.set()
        result = await asyncio.wait_for(driver, 2)
        assert result.status == 'timeout'
        snapshot = await group.snapshot(scope)
        assert snapshot.state == 'terminal'
        assert snapshot.reason == 'timeout'
        assert snapshot.active_count == 0
        assert snapshot.admitted_count == 1
        public = (await group.history(scope)).items
        assert public[0].id == request.message_id
        assert public[0].content == 'Slow work'
        # Closing may accept a completed result; it must retain real provenance.
        assert all(
            message.sender == 'a' and message.reply_to == request.message_id
            and message.run_id == snapshot.assignments.items[0].id
            for message in public[1:]
        )
    finally:
        model.release.set()
        await close_group(group, *waiters)
    agent.system_prompt = 'Reusable after the worker and Group close'


async def test_cancelled_drive_waiter_keeps_execution_for_a_second_waiter(tmp_path):
    model = BlockingFirstModel(AI('Public result'))
    peer_model = BlockingFirstModel(AI('Peer result'))
    agent = Agent(model)
    group = await create_group(tmp_path, {'a': agent, 'b': Agent(peer_model)})
    waiters = []
    try:
        scope = await group.open_invocation('cancel-waiter')
        request = await group.request(scope, 'Work once', key='request', recipients=('a',))
        first = asyncio.create_task(group.drive(scope))
        waiters.append(first)
        await wait_entered(model)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        snapshot = await group.snapshot(scope)
        assert snapshot.state == 'open'
        assert snapshot.active_count == snapshot.admitted_count == 1
        with pytest.raises(AgentBusyError):
            agent.system_prompt = 'Cannot mutate a still-owned Agent'
        peer_request = await group.request(scope, 'Work without a waiter', key='peer', recipients=('b',))
        # The existing driver must admit new work even while it has no caller waiting.
        await wait_entered(peer_model)
        second = asyncio.create_task(group.drive(scope))
        waiters.append(second)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(second), 0.03)

        model.release.set()
        peer_model.release.set()
        assert (await asyncio.wait_for(second, 2)).status == 'waiting'
        view = await group.scheduling_view(scope)
        assert view.snapshot.state == 'open'
        assert view.snapshot.admitted_count == 2
        assert view.snapshot.active_count == view.snapshot.pending_count == 0
        public = (await group.history(scope)).items
        assert {(message.sender, message.content, message.reply_to) for message in public
                if message.sender != 'user'} == {
            ('a', 'Public result', request.message_id), ('b', 'Peer result', peer_request.message_id),
        }
    finally:
        model.release.set()
        peer_model.release.set()
        await close_group(group, *waiters)


async def test_close_waits_for_driver_and_blocked_worker_before_releasing_agent(tmp_path):
    model = BlockingFirstModel(AI('Cancelled result'))
    agent = Agent(model)
    group = await create_group(tmp_path, {'a': agent})
    waiters = []
    try:
        scope = await group.open_invocation('close')
        await group.request(scope, 'Blocked work', key='request', recipients=('a',))
        driver = asyncio.create_task(group.drive(scope))
        waiters.append(driver)
        await wait_entered(model)

        closing = asyncio.create_task(group.close())
        waiters.append(closing)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(closing), 0.03)
        assert not model.returned.is_set()
        with pytest.raises(AgentBusyError):
            agent.run('Overlapping work')

        model.release.set()
        await asyncio.wait_for(closing, 2)
        assert model.returned.is_set()
        assert driver.done()
        agent.system_prompt = 'The closed Group no longer owns this Agent'
        assert not agent.run_state.active
    finally:
        model.release.set()
        await close_group(group, *waiters)


async def test_request_for_busy_target_runs_after_its_current_assignment_settles(tmp_path):
    model = BlockingFirstModel(AI('First result'), AI('Second result'))
    group = await create_group(tmp_path, {'a': Agent(model)})
    waiters = []
    try:
        scope = await group.open_invocation('busy-target')
        first = await group.request(scope, 'First task', key='first', recipients=('a',))
        driver = asyncio.create_task(group.drive(scope))
        waiters.append(driver)
        await wait_entered(model)
        second = await group.request(scope, 'Second task', key='second', recipients=('a',))

        view = await group.scheduling_view(scope)
        assert view.snapshot.active_count == view.snapshot.admitted_count == 1
        assert [(item.message_id, item.target) for item in view.pending] == [(second.message_id, 'a')]
        assert not model.returned.is_set()

        model.release.set()
        assert (await asyncio.wait_for(driver, 2)).status == 'waiting'
        view = await group.scheduling_view(scope)
        assert view.snapshot.admitted_count == 2
        assert view.snapshot.active_count == view.snapshot.pending_count == 0
        assignments = (await group.assignments(scope)).items
        assert [assignment.member_id for assignment in assignments] == ['a', 'a']
        assert [assignment.state for assignment in assignments] == ['settled', 'settled']
        public = (await group.history(scope)).items
        assert [(message.content, message.reply_to) for message in public if message.sender == 'a'] == [
            ('First result', first.message_id), ('Second result', second.message_id),
        ]
    finally:
        model.release.set()
        await close_group(group, *waiters)


async def test_pending_work_beyond_one_page_does_not_starve_an_idle_target(tmp_path):
    a_model = BlockingFirstModel(AI('Initial result'), AI('Next result'), AI('Another result'))
    b_model = BlockingFirstModel(AI('Idle peer result'))
    group = await create_group(
        tmp_path, {'a': Agent(a_model), 'b': Agent(b_model)},
        limits=GroupLimits(page_size=1, max_active=2, max_runs=10),
    )
    waiters = []
    try:
        scope = await group.open_invocation('beyond-page')
        first = await group.request(scope, 'Occupy a', key='first', recipients=('a',))
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision, runs=(
            RunProposal('a', 'Handle the initial request', first.opportunity_ids),
        )))
        await group.launch_ready(scope)
        await wait_entered(a_model)
        queued_one = await group.request(scope, 'Wait for a once', key='a-one', recipients=('a',))
        queued_two = await group.request(scope, 'Wait for a twice', key='a-two', recipients=('a',))
        idle = await group.request(scope, 'Work for b', key='b', recipients=('b',))

        view = await group.scheduling_view(scope)
        assert len(view.snapshot.opportunities.items) == 1
        assert [(item.message_id, item.target) for item in view.pending] == [
            (queued_one.message_id, 'a'), (queued_two.message_id, 'a'), (idle.message_id, 'b'),
        ]
        driver = asyncio.create_task(group.drive(scope))
        waiters.append(driver)
        await wait_entered(b_model)
        assert not a_model.returned.is_set()
        assert (await group.snapshot(scope)).active_count == 2

        a_model.release.set()
        b_model.release.set()
        assert (await asyncio.wait_for(driver, 2)).status == 'waiting'
        view = await group.scheduling_view(scope)
        assert view.snapshot.active_count == view.snapshot.pending_count == 0
        public = (await group.history(scope, limit=20)).items
        assert {(message.sender, message.reply_to) for message in public if message.sender != 'user'} == {
            ('a', first.message_id), ('a', queued_one.message_id),
            ('a', queued_two.message_id), ('b', idle.message_id),
        }
    finally:
        a_model.release.set()
        b_model.release.set()
        await close_group(group, *waiters)
