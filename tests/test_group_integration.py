"""Real Agent, tools and store behavior under explicit, policy-free plans."""

import asyncio
import json
from threading import Event

import pytest

from core.agent import Agent
from core.agent_runtime.run_state import AgentBusyError
from group import DispatchPlan, Disposition, GroupLimits, GroupRuntime, RunProposal
from group.errors import CapacityError, ConflictError, GroupError, LifecycleError
from models import AI, ToolCall
from tests.runtime_fakes import ScriptedModel
from tools.registry import ToolRegistry


async def plan_request(group, scope, receipt, member):
    snapshot = await group.snapshot(scope)
    return await group.commit_plan(DispatchPlan(scope, snapshot.revision,
        runs=(RunProposal(member, 'Respond to the explicit request', receipt.opportunity_ids),)))


async def test_peer_tool_exchange_uses_real_replies_and_preserves_private_answers(tmp_path):
    a_model, b_model = ScriptedModel(), ScriptedModel()
    a, b = Agent(a_model), Agent(b_model)
    group = await GroupRuntime.create(tmp_path / 'group.sqlite', {'a': a, 'b': b}, worker_safe=True)
    try:
        scope = await group.open_invocation('discussion')
        first = await group.request(scope, 'Inspect the design', key='r', recipients=('a',))
        a_model.responses.extend([
            AI(tool_calls=[
                ToolCall('reused', 'group_members', {}),
                ToolCall('h', 'group_history', {'limit': 1}),
                ToolCall('post', 'group_post', {'content': 'Public observation', 'reply_to': first.message_id}),
                ToolCall('r', 'group_request', {'content': 'Please review my observation', 'recipients': ['b'], 'reply_to': first.message_id}),
                ToolCall('y', 'group_yield', {}),
            ]),
        ])
        await plan_request(group, scope, first, 'a')
        await group.launch_ready(scope)
        await group.wait_idle()
        snapshot = await group.snapshot(scope)
        assert snapshot.pending_count == 1 and snapshot.state == 'open'
        assert snapshot.assignments.items[0].outcome == 'tool_stop'
        public = (await group.history(scope)).items
        assert [m.content for m in public] == ['Inspect the design', 'Public observation', 'Please review my observation']
        assert public[1].sender == public[2].sender == 'a'
        assert public[1].reply_to == public[2].reply_to == first.message_id
        result_messages = [m for m in a.session.history if m.role == 'tool']
        assert json.loads(result_messages[0].message)[0]['state'] == 'running'
        assert json.loads(result_messages[1].message)['items'][0]['id'] == first.message_id
        assert json.loads(result_messages[3].message)['accepted'] is True
        assert not b_model.requests
        assert all(message.role == 'system' for message in b.session.history)
        b_model.responses.extend([
            AI(tool_calls=[ToolCall('reused', 'group_post', {'content': 'Review complete', 'reply_to': public[2].id})]),
            AI('Private supporting analysis'),
        ])
        opportunity = snapshot.opportunities.items[0]
        await group.commit_plan(DispatchPlan(scope, snapshot.revision,
            runs=(RunProposal('b', 'Review the observation', (opportunity.id,)),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        public = (await group.history(scope)).items
        assert public[-1].sender == 'b' and public[-1].reply_to == public[-2].id
        assert all('Private supporting' not in m.content for m in public)
        assert b.session.history[-1].message == 'Private supporting analysis'
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)
    finally:
        await group.close()
    assert not a.registry.has('group_post') and not b.registry.has('group_post')


async def test_capacity_limits_launch_without_selecting_or_losing_queued_work(tmp_path):
    agents = {name: Agent(ScriptedModel(AI(name))) for name in ('a', 'b')}
    group = await GroupRuntime.create(tmp_path / 'group.sqlite', agents, worker_safe=True, limits=GroupLimits(max_active=1))
    try:
        scope = await group.open_invocation('s')
        receipt = await group.request(scope, 'work', key='r', recipients=('a', 'b'))
        snap = await group.snapshot(scope)
        await group.commit_plan(DispatchPlan(scope, snap.revision,
            runs=tuple(RunProposal(name, '', (oid,)) for name, oid in zip(agents, receipt.opportunity_ids))))
        assert len(await group.launch_ready(scope)) == 1
        await group.wait_idle()
        snap = await group.snapshot(scope)
        assert snap.queued_count == 1 and snap.active_count == 0
        assert not agents['b'].llm.requests
        assert all(message.role == 'system' for message in agents['b'].session.history)
        await group.launch_ready(scope)
        await group.wait_idle()
        assert (await group.snapshot(scope)).queued_count == 0
    finally:
        await group.close()


async def test_cancelled_close_waiter_keeps_member_owned_until_actual_exit(tmp_path):
    entered, release = Event(), Event()
    class BlockedModel(ScriptedModel):
        def invoke(self, *args, **kwargs):
            entered.set()
            release.wait(5)
            return super().invoke(*args, **kwargs)
    agent = Agent(BlockedModel(AI(tool_calls=[ToolCall('p', 'group_post', {'content': 'must not execute'})])))
    group = await GroupRuntime.create(tmp_path / 'group.sqlite', {'a': agent}, worker_safe=True)
    try:
        scope = await group.open_invocation('s')
        receipt = await group.request(scope, 'work', key='r', recipients=('a',))
        await plan_request(group, scope, receipt, 'a')
        await group.launch_ready(scope)
        assert await asyncio.to_thread(entered.wait, 2)
        closing = asyncio.create_task(group.cancel(scope))
        async def wait_closing():
            while True:
                snap = await group.snapshot(scope)
                if snap.state == 'closing':
                    return snap
                await group.wait_for_change(snap.revision)
        snap = await asyncio.wait_for(wait_closing(), 2)
        assert snap.active_count == 1
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        with pytest.raises(AgentBusyError):
            agent.run('overlap')
        with pytest.raises(LifecycleError):
            await group.post(scope, 'new external work', key='late')
        with pytest.raises(LifecycleError):
            await group.open_invocation('next')
        assert (await group.snapshot(scope)).state == 'closing'
        release.set()
        await group.wait_idle()
        snap = await group.snapshot(scope)
        assert snap.state == 'terminal' and snap.active_count == 0
        assert snap.assignments.items[0].outcome == 'cancelled'
        assert [m.content for m in (await group.history(scope)).items] == ['work']
    finally:
        release.set()
        await group.close()
    agent.system_prompt = 'Reusable after close'


async def test_invalid_targets_replies_origins_and_duplicate_reservations_are_atomic(tmp_path):
    agent = Agent(ScriptedModel(AI('done')))
    group = await GroupRuntime.create(tmp_path / 'group.sqlite', {'a': agent}, worker_safe=True)
    try:
        scope = await group.open_invocation('s')
        for options in ({'recipients': ('missing',)}, {'recipients': ('a', 'a')}, {'reply_to': 'not-a-message'}):
            with pytest.raises(GroupError):
                await group.post(scope, 'bad', key='same', **options)
        assert not (await group.history(scope)).items
        receipt = await group.request(scope, 'work', key='r', recipients=('a',))
        await plan_request(group, scope, receipt, 'a')
        snap = await group.snapshot(scope)
        with pytest.raises(ConflictError):
            await group.commit_plan(DispatchPlan(scope, snap.revision, runs=(RunProposal('a', 'again', origin_key='phase2'),)))
        assert (await group.snapshot(scope)).revision == snap.revision
        await group.launch_ready(scope)
        await group.wait_idle()
        snap = await group.snapshot(scope)
        await group.commit_plan(DispatchPlan(scope, snap.revision, runs=(RunProposal('a', 'second phase', origin_key='phase2'),)))
        await group.cancel(scope)
        assert (await group.snapshot(scope)).assignments.items[-1].outcome == 'cancelled'
    finally:
        await group.close()


async def test_repeated_invocations_remain_bounded_and_reopen_preserves_originals(tmp_path):
    class RepeatModel(ScriptedModel):
        def invoke(self, *args, **kwargs):
            return {'choices': [{'message': {'role': 'assistant', 'content': 'private'}}]}
    path = tmp_path / 'group.sqlite'
    agent = Agent(RepeatModel())
    group = await GroupRuntime.create(path, {'a': agent}, worker_safe=True, limits=GroupLimits(page_size=2))
    for index in range(25):
        scope = await group.open_invocation(f's{index}')
        receipt = await group.request(scope, f'original {index}', key='same-provider-independent-key', recipients=('a',))
        await plan_request(group, scope, receipt, 'a')
        await group.launch_ready(scope)
        await group.wait_idle()
        snap = await group.snapshot(scope)
        assert snap.pending_count == snap.active_count == snap.queued_count == 0
        await group.finish(scope, revision=snap.revision)
    await group.close()
    group = await GroupRuntime.create(path, {'a': agent}, worker_safe=True)
    try:
        assert (await group.history('s0')).items[0].content == 'original 0'
        assert (await group.history('s24')).items[0].content == 'original 24'
        assert (await group.snapshot('s24')).assignments.items[0].outcome == 'completed'
        assert len([m for m in agent.session.history if m.role == 'user']) == 25
    finally:
        await group.close()


async def test_binding_rejects_shared_sessions_unsupported_tools_and_restores_failed_composition(tmp_path):
    agent = Agent(ScriptedModel())
    with pytest.raises(GroupError):
        await GroupRuntime.create(tmp_path / 'duplicate.sqlite', {'a': agent, 'b': agent}, worker_safe=True)
    registry = ToolRegistry()
    registry.register(lambda: None, name='write', permission='workspace_mutating')
    with pytest.raises(GroupError):
        await GroupRuntime.create(tmp_path / 'unsafe.sqlite', {'a': Agent(ScriptedModel(), registry=registry)}, worker_safe=True)
    registry = ToolRegistry()
    async def async_tool():
        return 'unsupported'
    registry.register(async_tool, permission='read_only')
    bad = Agent(ScriptedModel(), registry=registry)
    with pytest.raises(TypeError):
        await GroupRuntime.create(tmp_path / 'failed.sqlite', {'a': agent, 'b': bad}, worker_safe=True)
    assert not agent.registry.has('group_post') and not bad.registry.has('group_post')
    agent.system_prompt = 'not leased'


async def test_dispositions_do_not_discard_public_originals(tmp_path):
    group = await GroupRuntime.create(tmp_path / 'group.sqlite', {'a': Agent(ScriptedModel())}, worker_safe=True,
                                      limits=GroupLimits(max_pending=1))
    try:
        scope = await group.open_invocation('s')
        receipt = await group.request(scope, 'keep original', key='r')
        with pytest.raises(CapacityError):
            await group.request(scope, 'over limit', key='r2')
        snap = await group.snapshot(scope)
        await group.commit_plan(DispatchPlan(scope, snap.revision,
            dispositions=(Disposition(receipt.opportunity_ids[0], 'refused'),), policy_state='{"phase":"done"}'))
        after = await group.snapshot(scope)
        assert after.pending_count == 0 and json.loads(after.policy_state)['phase'] == 'done'
        assert (await group.history(scope)).items[0].content == 'keep original'
        await group.finish(scope, revision=after.revision)
    finally:
        await group.close()


async def test_parallel_members_do_not_block_public_command_admission(tmp_path):
    release = Event()
    entered = {name: Event() for name in ('a', 'b')}
    class BlockingModel(ScriptedModel):
        def __init__(self, name):
            super().__init__(AI('done'))
            self.name = name
        def invoke(self, *args, **kwargs):
            entered[self.name].set()
            release.wait(5)
            return super().invoke(*args, **kwargs)
    group = await GroupRuntime.create(tmp_path / 'parallel.sqlite',
        {name: Agent(BlockingModel(name)) for name in entered}, worker_safe=True)
    try:
        scope = await group.open_invocation('s')
        snap = await group.snapshot(scope)
        await group.commit_plan(DispatchPlan(scope, snap.revision,
            runs=tuple(RunProposal(name, 'work', origin_key=name) for name in entered)))
        await group.launch_ready(scope)
        for event in entered.values():
            assert await asyncio.to_thread(event.wait, 2)
        receipt = await asyncio.wait_for(group.post(scope, 'control remains responsive', key='p'), 1)
        assert receipt.message_id
        assert (await group.snapshot(scope)).active_count == 2
    finally:
        release.set()
        await group.close()


async def test_blocking_audit_retains_run_but_does_not_block_storage_or_replay_effect(tmp_path):
    entered, release = Event(), Event()
    class BlockingSink:
        def __init__(self):
            self.records = []
        def write(self, record):
            self.records.append(record)
            entered.set()
            release.wait(5)
    agent = Agent(ScriptedModel(AI(tool_calls=[ToolCall('p', 'group_post', {'content': 'accepted effect'})]), AI('private')))
    sink = BlockingSink()
    agent.runtime.tool_runtime.audit_log.sink = sink
    group = await GroupRuntime.create(tmp_path / 'audit.sqlite', {'a': agent}, worker_safe=True)
    try:
        scope = await group.open_invocation('s')
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('a', 'post publicly', origin_key='post'),)))
        await group.launch_ready(scope)
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.wait_for(group.post(scope, 'independent control', key='control'), 1)
        cancellation = asyncio.create_task(group.cancel(scope))
        while True:
            snapshot = await group.snapshot(scope)
            if snapshot.state == 'closing':
                break
            await group.wait_for_change(snapshot.revision)
        assert snapshot.active_count == 1 and not cancellation.done()
        release.set()
        await cancellation
        assert [m.content for m in (await group.history(scope)).items] == ['accepted effect', 'independent control']
        assert len(sink.records) == 1
        assert (await group.snapshot(scope)).assignments.items[0].outcome == 'cancelled'
    finally:
        release.set()
        await group.close()
