"""Real collaboration behavior with an explicitly selected on-demand strategy."""

import asyncio
import json
import sqlite3
from threading import Event

import pytest

from core.agent import Agent
from group import DispatchPlan, GroupLimits, GroupRuntime, RunProposal
from group.errors import LifecycleError
from models import AI, ToolCall
from tests.runtime_fakes import ScriptedModel


async def test_cumulative_limit_survives_settlement_and_rejects_whole_plan(tmp_path):
    limits = GroupLimits(max_runs=1)
    from group.errors import InvocationLimitError
    agent = Agent(ScriptedModel(AI('private')))
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': agent}, worker_safe=True, limits=limits) as group:
        scope = await group.open_invocation('s')
        before = await group.snapshot(scope)
        await group.commit_plan(DispatchPlan(scope, before.revision,
            runs=(RunProposal('a', 'first', origin_key='first'),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        settled = await group.snapshot(scope)
        with pytest.raises(InvocationLimitError) as rejected:
            await group.commit_plan(DispatchPlan(scope, settled.revision,
                runs=(RunProposal('a', 'again', origin_key='second'),), policy_state='{"advanced":true}'))
        assert rejected.value.reason == 'limited'
        after = await group.snapshot(scope)
        assert after.revision == settled.revision and after.policy_state == '{}'
        assert len(after.assignments.items) == 1


async def test_expired_invocation_rejects_new_work_but_retains_original_receipt(tmp_path):
    limits = GroupLimits(invocation_timeout=0.03)
    from group.errors import InvocationLimitError
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(ScriptedModel())},
                                        worker_safe=True, limits=limits) as group:
        scope = await group.open_invocation('s')
        receipt = await group.post(scope, 'original', key='same')
        await asyncio.sleep(0.04)
        assert await group.post(scope, 'original', key='same') == receipt
        with pytest.raises(InvocationLimitError) as rejected:
            await group.request(scope, 'too late', key='late')
        assert rejected.value.reason == 'timeout'
        assert [message.content for message in (await group.history(scope)).items] == ['original']


async def test_version_one_store_requires_import_and_retains_public_originals(tmp_path):
    path = tmp_path / 'g.sqlite'
    agent = Agent(ScriptedModel())
    async with await GroupRuntime.create(path, {'a': agent}, worker_safe=True) as group:
        scope = await group.open_invocation('old')
        receipt = await group.post(scope, 'preserve me', key='old')
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE IF EXISTS invocation_config')
        db.execute('UPDATE metadata SET version=1')
    from group.errors import GroupError
    with pytest.raises(GroupError, match='explicit reception schema import'):
        await GroupRuntime.create(path, {'a': agent}, worker_safe=True)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT content FROM messages WHERE id=?', (receipt.message_id,)).fetchone()[0] == 'preserve me'
        assert db.execute('SELECT version FROM metadata').fetchone()[0] == 1


async def test_profile_projects_background_without_activating_members(tmp_path):
    from group.profiles import CollaborationProfile
    model = ScriptedModel(AI('private'))
    agent = Agent(model)
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'b': agent}, worker_safe=True,
                                        profile=CollaborationProfile(background_messages=2)) as group:
        scope = await group.open_invocation('s')
        background = await group.post(scope, 'Existing background fact', key='p')
        assert not model.requests
        assert all(message.role == 'system' for message in agent.session.history)
        receipt = await group.request(scope, 'Review that fact', key='r', recipients=('b',))
        snap = await group.snapshot(scope)
        await group.commit_plan(DispatchPlan(scope, snap.revision,
            runs=(RunProposal('b', 'Review the fact', receipt.opportunity_ids),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        text = '\n'.join(str(message.message) for message in model.requests[0])
        assert 'Existing background fact' in text and background.message_id in text
        assert text.count('Review that fact') == 1
        assert not any(message.sender == 'b' for message in (await group.history(scope)).items)


async def test_no_group_tool_profile_can_publish_designated_final_result(tmp_path):
    from group.profiles import CollaborationProfile
    model = ScriptedModel(AI(message='Selected result', reasoning='Private reasoning'))
    agent = Agent(model)
    profile = CollaborationProfile(name='final-publication', tools=(), publish_final=True,
                                   require_public_reply=True, instructions='Return a useful final answer.')
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': agent}, worker_safe=True,
                                        profile=profile) as group:
        assert not agent.registry.names()
        scope = await group.open_invocation('s')
        receipt = await group.request(scope, 'Question', key='r', recipients=('a',))
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('a', 'Answer', receipt.opportunity_ids),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        public = (await group.history(scope)).items
        assert [(message.sender, message.content) for message in public] == [('user', 'Question'), ('a', 'Selected result')]
        assert public[-1].reply_to == receipt.message_id
        assert 'Private reasoning' not in '\n'.join(message.content for message in public)
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)


class ReplyModel(ScriptedModel):
    """Choose a real trigger at the provider boundary, then end via a real tool."""

    def __init__(self, extra=None):
        super().__init__()
        self.extra = extra

    def invoke(self, messages, **kwargs):
        payload = json.loads(next(m.message for m in reversed(messages) if m.role == 'user').rsplit('\n', 1)[1])
        trigger = payload['trigger_messages'][0]['message_id']
        calls = [ToolCall('post', 'group_post', {'content': 'Public reply', 'reply_to': trigger})]
        if self.extra:
            name, arguments = self.extra
            calls.append(ToolCall('request', name, arguments))
        calls.append(ToolCall('yield', 'group_yield', {}))
        self.responses.append(AI(tool_calls=calls))
        return super().invoke(messages, **kwargs)


async def test_on_demand_only_requests_activate_and_public_replies_do_not_loop(tmp_path):
    from group.strategies import OnDemandStrategy
    models = {name: ReplyModel() for name in ('a', 'b', 'c')}
    async with await GroupRuntime.create(tmp_path / 'g.sqlite',
            {name: Agent(model) for name, model in models.items()}, worker_safe=True,
            strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        await group.post(scope, 'Background only', key='p')
        assert (await group.drive(scope)).status == 'waiting'
        assert not any(model.requests for model in models.values())
        request = await group.request(scope, 'First responder please', key='r')
        result = await asyncio.wait_for(group.drive(scope), 2)
        assert result.status == 'waiting' and result.admitted_count == 1
        assert [len(model.requests) for model in models.values()] == [1, 0, 0]
        assert (await group.history(scope)).items[-1].reply_to == request.message_id
        await group.request(scope, 'Only C', key='c', recipients=('c',))
        result = await asyncio.wait_for(group.drive(scope), 2)
        assert result.status == 'waiting' and result.admitted_count == 2
        assert [len(model.requests) for model in models.values()] == [1, 0, 1]
        assert (await group.snapshot(scope)).state == 'open'
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)


@pytest.mark.parametrize('recipients', [[], ['b']])
async def test_voluntary_reply_to_own_broadcast_does_not_activate_another_run(tmp_path, recipients):
    from group.strategies import OnDemandStrategy

    class ContributingModel(ScriptedModel):
        def invoke(self, messages, **kwargs):
            if self.requests:
                receipt = json.loads(next(message.message for message in reversed(messages)
                    if message.role == 'tool' and message.tool_call_id == 'request'))
                self.responses.append(AI(tool_calls=[
                    ToolCall('follow-up', 'group_post', {
                        'content': 'An additional observation from the requester.',
                        'reply_to': receipt['message_id'],
                        'recipients': recipients,
                    }),
                    ToolCall('finish', 'group_yield', {}),
                ]))
                return super().invoke(messages, **kwargs)
            trigger = json.loads(next(message.message for message in reversed(messages)
                if message.role == 'user').rsplit('\n', 1)[1])['trigger_messages'][0]['message_id']
            self.responses.append(AI(tool_calls=[
                ToolCall('post', 'group_post', {'content': 'Initial reply', 'reply_to': trigger}),
                ToolCall('request', 'group_broadcast', {'content': 'Give one peer observation.'}),
            ]))
            return super().invoke(messages, **kwargs)

    models = {'a': ContributingModel(), 'b': ReplyModel(), 'c': ReplyModel()}
    async with await GroupRuntime.create(tmp_path / 'g.sqlite',
            {name: Agent(model) for name, model in models.items()}, worker_safe=True,
            strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('free-discussion')
        await group.request(scope, 'Start the discussion', key='initial', recipients=('a',))
        result = await group.drive(scope)
        assert result.status == 'waiting' and result.admitted_count == 3
        assert [len(model.requests) for model in models.values()] == [2, 1, 1]
        history = (await group.history(scope)).items
        broadcast = next(message for message in history if message.sender == 'a' and message.recipients)
        extra = next(message for message in history if message.content.startswith('An additional observation'))
        assert extra.sender == 'a' and extra.reply_to == broadcast.id and extra.run_id == broadcast.run_id
        assert extra.recipients == tuple(recipients)
        opportunities = (await group.opportunities(scope)).items
        assert len(opportunities) == 3 and all(item.state == 'settled' for item in opportunities)
        assert {item.target for item in opportunities if item.message_id == broadcast.id} == {'b', 'c'}
        assert not any(item.message_id == extra.id for item in opportunities)
        await group.post(scope, 'Another passive contribution.', key='follow-up')
        assert (await group.drive(scope)).admitted_count == 3
        assert [len(model.requests) for model in models.values()] == [2, 1, 1]
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)


@pytest.mark.parametrize('broadcast', [False, True])
async def test_on_demand_drives_peer_requests_to_quiescence(tmp_path, broadcast):
    from group.strategies import OnDemandStrategy
    extra = ('group_broadcast', {'content': 'Peer review'}) if broadcast else (
        'group_request', {'content': 'Peer review', 'recipients': ['b']})
    models = {'a': ReplyModel(extra), 'b': ReplyModel(), 'c': ReplyModel()}
    async with await GroupRuntime.create(tmp_path / 'g.sqlite',
            {name: Agent(model) for name, model in models.items()}, worker_safe=True,
            strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        await group.request(scope, 'Ask peers', key='r', recipients=('a',))
        result = await asyncio.wait_for(group.drive(scope), 2)
        assert result.status == 'waiting'
        assert [len(model.requests) for model in models.values()] == [1, 1, int(broadcast)]
        assert result.admitted_count == 2 + int(broadcast)
        view = await group.scheduling_view(scope)
        assert all(item.reply_id for item in view.evidence)
        assert view.snapshot.pending_count == view.snapshot.active_count == view.snapshot.queued_count == 0
        await group.finish(scope, revision=view.snapshot.revision)


@pytest.mark.parametrize('failed', [False, True])
async def test_private_result_or_failure_is_not_satisfied_and_never_retries(tmp_path, failed):
    from group.strategies import OnDemandStrategy
    model = ScriptedModel() if failed else ScriptedModel(AI('Private answer'))
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)},
            worker_safe=True, strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        receipt = await group.request(scope, 'Reply publicly', key='r')
        result = await asyncio.wait_for(group.drive(scope), 2)
        assert result.status == 'needs_input'
        assert result.missing_reply_ids == receipt.opportunity_ids
        assert len(model.requests) == 1
        assert (await group.drive(scope)).status == 'needs_input'
        assert len(model.requests) == 1
        with pytest.raises(LifecycleError, match='replies|failed'):
            await group.finish(scope, revision=(await group.snapshot(scope)).revision)


async def test_continuing_requests_hit_cumulative_budget_without_false_completion(tmp_path):
    from group.strategies import OnDemandStrategy
    model = ReplyModel(('group_request', {'content': 'Continue', 'recipients': ['a']}))
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)}, worker_safe=True,
            strategy=OnDemandStrategy(), limits=GroupLimits(max_runs=3)) as group:
        scope = await group.open_invocation('s')
        await group.request(scope, 'Start', key='r')
        result = await asyncio.wait_for(group.drive(scope), 2)
        assert result.status == 'limited' and result.admitted_count == 3
        assert len(model.requests) == 3
        snap = await group.snapshot(scope)
        assert snap.state == 'terminal' and snap.reason == 'limited'
        assert len((await group.history(scope)).items) == 7


async def test_background_is_chunked_completely_without_truncating_or_deleting_originals(tmp_path):
    from group.profiles import CollaborationProfile
    model = ScriptedModel(AI('private'))
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)}, worker_safe=True,
            profile=CollaborationProfile(background_messages=2),
            limits=GroupLimits(message_bytes=4096, input_bytes=1700)) as group:
        scope = await group.open_invocation('s')
        old = await group.post(scope, 'x' * 3000, key='old')
        recent = await group.post(scope, 'Relevant short background', key='new')
        request = await group.request(scope, 'Complete trigger', key='r')
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('a', '', request.opportunity_ids),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        prompt = next(m.message for m in reversed(model.requests[0]) if m.role == 'user')
        payload = json.loads(prompt.rsplit('\n', 1)[1])
        assert len(prompt.encode()) <= 1700 and payload['background_omitted'] is False
        assert payload['trigger_messages'][0]['content'] == 'Complete trigger'
        sources = [json.loads(m.message.rsplit('\n', 1)[1]) for m in model.requests[0]
                   if m.role == 'user' and m.message.startswith('Historical public source')]
        assert ''.join(item['content'] for item in sources if item['message_id'] == old.message_id) == 'x' * 3000
        assert any(item['message_id'] == recent.message_id for item in sources)
        assert (await group.message(scope, old.message_id)).content == 'x' * 3000


async def test_scheduling_observations_include_posts_without_any_activation(tmp_path):
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(ScriptedModel())},
                                        worker_safe=True, limits=GroupLimits(page_size=1, max_page_size=2)) as group:
        scope = await group.open_invocation('s')
        for index in range(3):
            await group.post(scope, str(index), key=str(index))
        view = await group.scheduling_view(scope)
        assert [m.content for m in view.messages.items] == ['0', '1']
        assert not view.pending and view.snapshot.admitted_count == 0
        assert (await group.history(scope, limit=2)).items[0].content == '0'


async def test_another_members_reply_cannot_satisfy_the_assigned_run(tmp_path):
    from group.strategies import OnDemandStrategy
    a, b = ScriptedModel(), ScriptedModel()
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(a), 'b': Agent(b)},
            worker_safe=True, strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        request = await group.request(scope, 'A must respond', key='r', recipients=('a',))
        a.responses.append(AI('Private A'))
        b.responses.append(AI(tool_calls=[ToolCall('p', 'group_post', {'content': 'B replies', 'reply_to': request.message_id}),
                                         ToolCall('y', 'group_yield', {})]))
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('b', '', origin_key='b-interjects'),)))
        result = await group.drive(scope)
        assert result.status == 'needs_input' and result.missing_reply_ids == request.opportunity_ids
        assert (await group.scheduling_view(scope)).evidence[0].reply_id is None


async def test_oversized_final_publication_is_a_visible_failure(tmp_path):
    from group.profiles import CollaborationProfile
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(ScriptedModel(AI('x' * 200)))},
            worker_safe=True, profile=CollaborationProfile(tools=(), publish_final=True, require_public_reply=True),
            limits=GroupLimits(message_bytes=100)) as group:
        scope = await group.open_invocation('s')
        request = await group.request(scope, 'Question', key='r')
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('a', '', request.opportunity_ids),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        run = (await group.assignments(scope)).items[0]
        assert run.outcome == 'completed' and 'Final publication failed' in run.error
        with pytest.raises(LifecycleError, match='replies|failed'):
            await group.finish(scope, revision=(await group.snapshot(scope)).revision)


async def test_strategy_failure_closes_scope_but_waits_for_real_execution(tmp_path):
    from core.agent_runtime.run_state import AgentBusyError
    from group.strategies import OnDemandStrategy
    entered, release = Event(), Event()

    class Blocked(ScriptedModel):
        def invoke(self, *args, **kwargs):
            entered.set()
            release.wait(5)
            return super().invoke(*args, **kwargs)

    class BrokenStrategy(OnDemandStrategy):
        def decide(self, view):
            if view.snapshot.active_count:
                raise RuntimeError('Decision failed')
            return super().decide(view)

    agent = Agent(Blocked(AI('private')))
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': agent}, worker_safe=True,
                                      strategy=BrokenStrategy())
    task = None
    try:
        scope = await group.open_invocation('s')
        await group.request(scope, 'Block', key='r')
        task = asyncio.create_task(group.drive(scope))
        assert await asyncio.to_thread(entered.wait, 2)

        async def closing():
            while True:
                snap = await group.snapshot(scope)
                if snap.state == 'closing':
                    return snap
                await group.wait_for_change(snap.revision)

        snap = await asyncio.wait_for(closing(), 0.5)
        assert snap.reason == 'error' and snap.active_count == 1 and not task.done()
        with pytest.raises(AgentBusyError):
            agent.run('overlap')
        release.set()
        with pytest.raises(RuntimeError, match='Decision failed'):
            await task
        assert (await group.snapshot(scope)).reason == 'error'
        assert (await group.snapshot(scope)).state == 'terminal'
    finally:
        release.set()
        await group.close()
        if task:
            await asyncio.gather(task, return_exceptions=True)


async def test_cancelled_reservation_retains_durable_cumulative_count(tmp_path):
    path = tmp_path / 'g.sqlite'
    agent = Agent(ScriptedModel())
    async with await GroupRuntime.create(path, {'a': agent}, worker_safe=True,
                                        limits=GroupLimits(max_runs=1)) as group:
        scope = await group.open_invocation('s')
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('a', '', origin_key='reserved'),)))
        await group.cancel(scope)
        assert (await group.snapshot(scope)).admitted_count == 1
    async with await GroupRuntime.create(path, {'a': agent}, worker_safe=True) as group:
        snapshot = await group.snapshot(scope)
        assert snapshot.admitted_count == snapshot.max_runs == 1
        assert snapshot.assignments.items[0].outcome == 'cancelled'
