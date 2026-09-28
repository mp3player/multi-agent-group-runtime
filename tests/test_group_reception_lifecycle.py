"""Owned automatic reception, preparation failure and completion boundaries."""

import asyncio
import json

from core.agent import Agent
from core.agent_runtime.options import AgentOptions
from group import DispatchPlan, GroupLimits, GroupRuntime, OnDemandStrategy, RunProposal
from models import AI, ToolCall
from tests.runtime_fakes import ScriptedModel


class ReplyModel(ScriptedModel):
    def invoke(self, messages, **kwargs):
        payload = json.loads(next(m.message for m in reversed(messages) if m.role == 'user').rsplit('\n', 1)[1])
        trigger = payload['trigger_messages'][0]['message_id']
        self.responses.append(AI(tool_calls=[
            ToolCall('reply', 'group_post', {'content': 'Reviewed', 'reply_to': trigger}),
            ToolCall('yield', 'group_yield', {}),
        ]))
        return super().invoke(messages, **kwargs)


async def eventually(check):
    async with asyncio.timeout(3):
        while not await check():
            await asyncio.sleep(0.005)


async def test_service_wakes_from_passive_wait_and_has_one_owned_lifetime(tmp_path):
    model = ReplyModel()
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)},
            worker_safe=True, strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        service = await group.start_service(scope)
        assert await group.start_service(scope) is service
        waiter = asyncio.create_task(service.wait())
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        await group.post(scope, 'Passive background', key='p')
        async def received():
            return (await group.reception(scope, 'a')).received_count == 1
        await eventually(received)
        assert model.requests == []
        await group.request(scope, 'Review background', key='r', recipients=('a',))
        async def replied():
            snapshot = await group.snapshot(scope)
            return bool(snapshot.assignments.items and snapshot.assignments.items[0].state == 'settled')
        await eventually(replied)
        await group.wait_idle()
        from group.errors import StalePlanError
        async with asyncio.timeout(3):
            while True:
                try:
                    await group.finish(scope, revision=(await group.snapshot(scope)).revision)
                    break
                except StalePlanError:
                    await asyncio.sleep(0)
        assert (await service.wait()).status == 'completed'
        assert (await group.reception(scope, 'a')).pending_count == 0
        assert len(model.requests) == 1


async def test_preparation_blockage_preserves_task_and_does_not_consume_other_member_capacity(tmp_path):
    small = Agent(ScriptedModel(), options=AgentOptions(context_window=256, max_tokens=64))
    healthy_model = ScriptedModel(AI('done'))
    async with await GroupRuntime.create(tmp_path / 'g.sqlite',
            {'small': small, 'healthy': Agent(healthy_model)}, worker_safe=True,
            limits=GroupLimits(max_active=1)) as group:
        scope = await group.open_invocation('s')
        first = await group.request(scope, 'x' * 3000, key='large', recipients=('small',))
        second = await group.request(scope, 'Small task', key='normal', recipients=('healthy',))
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision, runs=(
            RunProposal('small', 'Handle large task', first.opportunity_ids),
            RunProposal('healthy', 'Handle small task', second.opportunity_ids),
        )))
        await group.launch_ready(scope)
        await group.wait_idle()
        await group.launch_ready(scope)
        await group.wait_idle()
        rows = (await group.assignments(scope)).items
        assert rows[0].state == 'blocked'
        assert rows[1].outcome == 'completed'
        assert len(healthy_model.requests) == 1
        assert small.llm.requests == []
        assert (await group.opportunities(scope)).items[0].state == 'assigned'
        history_size = len(small.session.history)
        await group.retry_preparation(scope, rows[0].id)
        await group.launch_ready(scope)
        await group.wait_idle()
        assert (await group.assignments(scope)).items[0].state == 'blocked'
        assert len(small.session.history) == history_size


async def test_normal_finish_drains_passive_reception_without_inference(tmp_path):
    model = ScriptedModel()
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)},
            worker_safe=True, limits=GroupLimits(page_size=1)) as group:
        scope = await group.open_invocation('s')
        for index in range(4):
            await group.post(scope, f'Original {index}', key=str(index))
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)
        assert (await group.snapshot(scope)).reason == 'completed'
        assert (await group.reception(scope, 'a')).received_count == 4
        assert model.requests == []


async def test_render_budget_blocks_only_affected_member_and_retains_original(tmp_path):
    models = {name: ScriptedModel(AI('done')) for name in ('small', 'healthy')}
    async with await GroupRuntime.create(tmp_path / 'g.sqlite',
            {name: Agent(model) for name, model in models.items()}, worker_safe=True,
            limits=GroupLimits(input_bytes=1800, message_bytes=4096)) as group:
        scope = await group.open_invocation('s')
        large = await group.request(scope, 'x' * 3000, key='large', recipients=('small',))
        normal = await group.request(scope, 'Small task', key='normal', recipients=('healthy',))
        ids = await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision, runs=(
            RunProposal('small', 'Do large task', large.opportunity_ids),
            RunProposal('healthy', 'Do small task', normal.opportunity_ids),
        )))
        await group.launch_ready(scope)
        await group.wait_idle()
        rows = (await group.assignments(scope)).items
        assert rows[0].state == 'blocked' and 'byte' in rows[0].error
        assert rows[1].outcome == 'completed'
        assert models['small'].requests == [] and len(models['healthy'].requests) == 1
        await group.retry_preparation(scope, ids[0])
        assert (await group.assignments(scope)).items[0].state == 'blocked'
        assert (await group.message(scope, large.message_id)).content == 'x' * 3000


async def test_discovery_observes_every_page_without_reactivating_posts(tmp_path):
    class Observer(OnDemandStrategy):
        def __init__(self):
            self.observed = []

        def decide(self, view):
            self.observed.extend(message.content for message in view.messages.items)
            return super().decide(view)

    strategy = Observer()
    model = ScriptedModel()
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)},
            worker_safe=True, strategy=strategy, limits=GroupLimits(max_page_size=2, page_size=1)) as group:
        scope = await group.open_invocation('s')
        for index in range(7):
            await group.post(scope, str(index), key=str(index))
        assert (await group.drive(scope)).status == 'waiting'
        assert strategy.observed == [str(index) for index in range(7)]
        revision = (await group.snapshot(scope)).revision
        await group.drive(scope)
        assert (await group.snapshot(scope)).revision == revision
        assert model.requests == []
