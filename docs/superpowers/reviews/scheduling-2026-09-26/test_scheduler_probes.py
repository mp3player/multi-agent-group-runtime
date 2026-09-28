"""Read-only review probes against the current Group implementation."""

import asyncio
import json
from threading import Event

import pytest

from core.agent import Agent
from group import CollaborationProfile, DispatchPlan, GroupLimits, GroupRuntime, OnDemandStrategy, RunProposal
from group.scheduling import SchedulingDecision
from models import AI, ToolCall
from tests.runtime_fakes import ScriptedModel


class ReplyModel(ScriptedModel):
    def invoke(self, messages, **kwargs):
        payload = json.loads(next(m.message for m in reversed(messages) if m.role == 'user').rsplit('\n', 1)[1])
        self.responses.append(AI(tool_calls=[
            *(ToolCall(f'p-{i}', 'group_post', {'content': 'Reviewed with full context', 'reply_to': item['message_id']})
              for i, item in enumerate(payload['trigger_messages'])),
            ToolCall('yield', 'group_yield', {}),
        ]))
        return super().invoke(messages, **kwargs)


async def test_repeated_mixed_requests_keep_independent_slots_and_passive_silence(tmp_path):
    models = {name: ReplyModel() for name in ('a', 'b', 'c', 'd')}
    async with await GroupRuntime.create(tmp_path / 'g.sqlite',
            {name: Agent(model) for name, model in models.items()}, worker_safe=True,
            strategy=OnDemandStrategy(), limits=GroupLimits(max_active=2, max_queued=2,
                page_size=2, max_page_size=3, max_runs=60)) as group:
        scope = await group.open_invocation('mixed')
        expected = 0
        for cycle in range(6):
            await group.post(scope, f'Passive observation {cycle}', key=f'p{cycle}')
            before = sum(len(model.requests) for model in models.values())
            await group.drive(scope)
            assert sum(len(model.requests) for model in models.values()) == before
            await group.request(scope, f'All peers review cycle {cycle}', key=f'all{cycle}',
                                recipients=tuple(models))
            await group.request(scope, f'One peer additional review {cycle}', key=f'one{cycle}')
            expected += 5
            result = await asyncio.wait_for(group.drive(scope), 5)
            assert result.status == 'waiting'
            assert result.admitted_count == expected
            assert sum(len(model.requests) for model in models.values()) == expected
            view = await group.scheduling_view(scope)
            assert len(view.evidence) == expected and all(item.satisfied for item in view.evidence)
            assert all(member.state == 'idle' for member in view.snapshot.members)
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)
        for member in models:
            assert (await group.reception(scope, member)).pending_count == 0
        print({'repeated_mixed_assignments': expected, 'provider_calls': expected})


class PostReactionStrategy:
    """Exercise extension semantics, not a new production strategy."""
    name = 'review_post_reaction'
    version = '1'
    profile = CollaborationProfile(tools=(), publish_final=True)

    def decide(self, view):
        backlog = list(json.loads(view.snapshot.policy_state).get('backlog', []))
        backlog.extend(message.id for message in view.messages.items if message.sender == 'user')
        runs = []
        idle = [member.id for member in view.snapshot.members if member.state == 'idle']
        if backlog and idle and view.queue_capacity:
            message_id = backlog.pop(0)
            runs.append(RunProposal(idle[0], 'React to this source once',
                origin_key=f'post:{message_id}', required_source_ids=(message_id,)))
        if not runs and not view.messages.items:
            return SchedulingDecision(reason='Waiting for a member or a source')
        return SchedulingDecision(DispatchPlan(view.snapshot.invocation_id, view.snapshot.revision,
            runs=tuple(runs), policy_state=json.dumps({'backlog': backlog}),
            observed_through=view.messages.next_cursor if view.messages.items else None))


async def test_alternate_post_policy_defers_without_losing_sources_or_reacting_to_own_replies(tmp_path):
    model = ScriptedModel(*(AI(f'Public reaction {i}') for i in range(7)))
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)},
            worker_safe=True, strategy=PostReactionStrategy(),
            limits=GroupLimits(max_active=1, max_queued=1, page_size=1, max_page_size=2)) as group:
        scope = await group.open_invocation('alternate')
        for index in range(7):
            await group.post(scope, f'User source {index}', key=str(index))
        result = await asyncio.wait_for(group.drive(scope), 5)
        assert result.status == 'waiting' and result.admitted_count == 7
        assert len(model.requests) == 7
        assert (await group.opportunities(scope)).items == ()
        view = await group.scheduling_view(scope)
        assert not json.loads(view.snapshot.policy_state)['backlog']
        assert len({run.origin_key for run in view.runs}) == 7
        assert not view.messages.items
        await group.drive(scope)
        assert len(model.requests) == 7
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)


async def test_idle_service_survives_transient_read_pressure_and_keeps_its_deadline(tmp_path):
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(ScriptedModel())},
        worker_safe=True, strategy=OnDemandStrategy(),
        limits=GroupLimits(queue_jobs=1, invocation_timeout=0.3))
    reached, proceed = asyncio.Event(), asyncio.Event()
    held, release = Event(), Event()
    wait_for_change = group.wait_for_change
    async def gate(revision):
        reached.set()
        await proceed.wait()
        return await wait_for_change(revision)
    group.wait_for_change = gate
    blocker = None
    try:
        scope = await group.open_invocation('pressure')
        service = await group.start_service(scope)
        await asyncio.wait_for(reached.wait(), 2)
        def hold(db):
            held.set()
            assert release.wait(3)
        blocker = asyncio.create_task(group.store.read(hold))
        assert await asyncio.to_thread(held.wait, 1)
        proceed.set()
        asyncio.get_running_loop().call_later(0.03, release.set)
        result = await asyncio.wait_for(service.wait(), 2)
        assert result.status == 'timeout'
        assert (await group.snapshot(scope)).reason == 'timeout'
    finally:
        release.set()
        proceed.set()
        if blocker:
            await asyncio.gather(blocker, return_exceptions=True)
        await group.close()
