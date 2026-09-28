"""Reception failure boundaries and owned closure tested with explicit barriers."""

import asyncio
import json
from threading import Event

import pytest

from core.agent import Agent
from core.agent_runtime.options import AgentOptions
from group import DispatchPlan, GroupLimits, GroupRuntime, OnDemandStrategy, RunProposal
from group.errors import ConflictError, LifecycleError
from models import AI, ToolCall
from tests.runtime_fakes import ScriptedModel


async def admit(group, scope, member, receipt, *, required=()):
    return (await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
        runs=(RunProposal(member, 'Process assigned work', receipt.opportunity_ids,
                          required_source_ids=required),))))[0]


async def test_protected_task_alone_blocks_before_any_model_call(tmp_path):
    model = ScriptedModel()
    agent = Agent(model, options=AgentOptions(context_window=256, max_tokens=64))
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': agent}, worker_safe=True) as group:
        scope = await group.open_invocation('s')
        receipt = await group.request(scope, 'x' * 3000, key='r')
        await admit(group, scope, 'a', receipt)
        await group.launch_ready(scope)
        await group.wait_idle()
        run = (await group.assignments(scope)).items[0]
        assert run.state == 'blocked' and model.requests == []
        assert (await group.opportunities(scope)).items[0].state == 'assigned'


async def test_unicode_spans_reconstruct_all_sources_and_marker_text_is_only_data(tmp_path):
    from group.member_context import SYNTHETIC_RECEPTION
    model = ScriptedModel(AI('done'))
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)},
            worker_safe=True, limits=GroupLimits(input_bytes=1800)) as group:
        scope = await group.open_invocation('s')
        original = ('\U0001f680\u4e2d\\"\n' * 350) + SYNTHETIC_RECEPTION
        source = await group.post(scope, original, key='p')
        receipt = await group.request(scope, 'Count the spans', key='r')
        await admit(group, scope, 'a', receipt)
        await group.launch_ready(scope)
        await group.wait_idle()
        units = [json.loads(m.message.rsplit('\n', 1)[1]) for m in model.requests[0]
                 if m.role == 'user' and m.message.startswith('Historical public source')]
        assert {unit['message_id'] for unit in units} == {source.message_id}
        assert ''.join(unit['content'] for unit in units) == original
        assert units[0]['content_offset'] == 0 and units[-1]['next_content_offset'] == len(original)
        assert all(a['next_content_offset'] == b['content_offset'] for a, b in zip(units, units[1:]))
        assert (await group.history(scope)).high_water == 2
        assert len(model.requests) == 1


async def test_reconcile_applied_batch_after_lost_ack_and_keep_same_task(tmp_path, monkeypatch):
    import group.member_context as context
    model = ScriptedModel(AI('done'))
    agent = Agent(model)
    acknowledge = context.acknowledge
    lost = False
    def lose_once(db, member, receipt, source_ids=()):
        nonlocal lost
        if not lost:
            lost = True
            raise ValueError('Injected lost application acknowledgement')
        return acknowledge(db, member, receipt, source_ids)
    monkeypatch.setattr(context, 'acknowledge', lose_once)
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': agent}, worker_safe=True) as group:
        scope = await group.open_invocation('s')
        await group.post(scope, 'Early original', key='p')
        request = await group.request(scope, 'Answer', key='r')
        assignment = await admit(group, scope, 'a', request)
        await group.launch_ready(scope)
        await group.wait_idle()
        assert (await group.assignments(scope)).items[0].state == 'blocked'
        assert model.requests == []
        await group.retry_preparation(scope, assignment)
        await group.launch_ready(scope)
        await group.wait_idle()
        assert (await group.assignments(scope)).items[0].outcome == 'completed'
        texts = [m.message for m in agent.session.history if m.role == 'user']
        assert sum('Early original' in text for text in texts) == 1
        assert len(model.requests) == 1


async def test_required_source_is_full_and_excluded_from_passive_units(tmp_path):
    model = ScriptedModel(AI('done'))
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)}, worker_safe=True) as group:
        scope = await group.open_invocation('s')
        source = await group.post(scope, 'Mandatory old requirement', key='p')
        receipt = await group.request(scope, 'Answer now', key='r')
        await admit(group, scope, 'a', receipt, required=(source.message_id,))
        await group.launch_ready(scope)
        await group.wait_idle()
        user = [m.message for m in model.requests[0] if m.role == 'user']
        assert len(user) == 1
        payload = json.loads(user[0].rsplit('\n', 1)[1])
        assert payload['background_messages'][0]['content'] == 'Mandatory old requirement'


async def test_post_during_real_tool_batch_waits_for_next_input_boundary(tmp_path):
    from tools.registry import ToolRegistry
    entered, release = Event(), Event()
    def hold():
        entered.set()
        assert release.wait(5)
        return 'completed'
    registry = ToolRegistry()
    registry.register(hold)
    from tools.permissions import ToolPermission
    registry.set_permission('hold', ToolPermission(side_effect='read_only'))
    model = ScriptedModel(AI(tool_calls=[ToolCall('hold', 'hold', {})]), AI('done'), AI('next'))
    agent = Agent(model, registry=registry)
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': agent}, worker_safe=True)
    try:
        scope = await group.open_invocation('s')
        receipt = await group.request(scope, 'Start', key='r')
        await admit(group, scope, 'a', receipt)
        await group.launch_ready(scope)
        assert await asyncio.to_thread(entered.wait, 2)
        await group.post(scope, 'Arrived during tool', key='later')
        await group.receive(scope)
        assert not any('Arrived during tool' in str(m.message) for m in agent.session.history)
        release.set()
        await group.wait_idle()
        assert not any('Arrived during tool' in str(m.message) for m in model.requests[1])
        receipt = await group.request(scope, 'Continue', key='next')
        await admit(group, scope, 'a', receipt)
        await group.launch_ready(scope)
        await group.wait_idle()
        assert any('Arrived during tool' in str(m.message) for m in model.requests[2])
    finally:
        release.set()
        await group.close()


@pytest.mark.parametrize('cancel_scope', [False, True])
async def test_sealed_finish_owns_drain_and_rejects_new_messages(tmp_path, monkeypatch, cancel_scope):
    import group.lifecycle as lifecycle
    entered, release = Event(), Event()
    original = lifecycle.receive_page
    def hold(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    model = ScriptedModel()
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)}, worker_safe=True,
                                      limits=GroupLimits(page_size=1))
    try:
        scope = await group.open_invocation('s')
        receipt = await group.post(scope, 'Accepted before seal', key='p')
        await group.post(scope, 'Second page', key='q')
        monkeypatch.setattr(lifecycle, 'receive_page', hold)
        waiter = asyncio.create_task(group.finish(scope, revision=(await group.snapshot(scope)).revision))
        assert await asyncio.to_thread(entered.wait, 2)
        owner = group._finish_tasks[scope]
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        rejected = asyncio.create_task(group.post(scope, 'Too late', key='late'))
        duplicate = asyncio.create_task(group.post(scope, 'Accepted before seal', key='p'))
        cancellation = asyncio.create_task(group.cancel(scope)) if cancel_scope else None
        release.set()
        with pytest.raises(LifecycleError):
            await rejected
        assert await duplicate == receipt
        if cancellation:
            await cancellation
            await asyncio.gather(owner, return_exceptions=True)
            assert (await group.snapshot(scope)).reason == 'cancelled'
        else:
            await owner
            assert (await group.snapshot(scope)).reason == 'completed'
            assert (await group.reception(scope, 'a')).received_count == 2
        assert model.requests == []
    finally:
        release.set()
        await group.close()


async def test_idle_service_retains_deadline_and_close_releases_agent(tmp_path):
    model = ScriptedModel()
    agent = Agent(model)
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': agent}, worker_safe=True,
        strategy=OnDemandStrategy(), limits=GroupLimits(invocation_timeout=0.05))
    scope = await group.open_invocation('s')
    service = await group.start_service(scope)
    assert (await asyncio.wait_for(service.wait(), 2)).status == 'timeout'
    assert model.requests == []
    await group.close()
    model.responses.append(AI('independent'))
    assert agent.run('Standalone again') == 'independent'


async def test_unknown_manifest_adapter_blocks_without_orphaning_reservation(tmp_path):
    model = ScriptedModel()
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)}, worker_safe=True) as group:
        scope = await group.open_invocation('s')
        receipt = await group.request(scope, 'Task', key='r')
        await admit(group, scope, 'a', receipt)
        await group.store.write(lambda db: db.execute("UPDATE assignment_inputs SET adapter_version='unknown'"))
        await group.launch_ready(scope)
        await group.wait_idle()
        assert (await group.assignments(scope)).items[0].state == 'blocked'
        assert model.requests == []
        await group.cancel(scope)
        assert (await group.snapshot(scope)).state == 'terminal'


@pytest.mark.parametrize('same_session_id', [False, True])
async def test_replacement_session_cannot_inherit_changed_receipt_content(tmp_path, same_session_id):
    from core.context_batch import ContextBatch
    from core.session import Session
    from models import User
    path = tmp_path / 'g.sqlite'
    first = Agent(ScriptedModel(AI('done')))
    async with await GroupRuntime.create(path, {'a': first}, worker_safe=True) as group:
        scope = await group.open_invocation('first')
        receipt = await group.request(scope, 'Original protected requirement', key='r')
        await admit(group, scope, 'a', receipt)
        await group.launch_ready(scope)
        await group.wait_idle()
        binding = await group.store.read(lambda db: db.execute('SELECT operation_id,session_id FROM context_sources').fetchone())
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)
    replacement = Session()
    if same_session_id:
        replacement.session_id = binding[1]
    replacement.apply_context_batch(ContextBatch(binding[0], (User('different content'),), {'origin': 'runtime'}))
    model = ScriptedModel(AI('must not run'))
    async with await GroupRuntime.create(path, {'a': Agent(model, session=replacement)}, worker_safe=True) as group:
        scope = await group.open_invocation('second')
        receipt = await group.request(scope, 'Next task', key='r')
        await admit(group, scope, 'a', receipt)
        await group.launch_ready(scope)
        await group.wait_idle()
        assert (await group.assignments(scope)).items[0].state == 'blocked'
        assert model.requests == []


@pytest.mark.parametrize('mutate', [False, True])
async def test_profile_cannot_omit_or_rewrite_protected_source(tmp_path, mutate):
    from group.profiles import CollaborationProfile
    from group.errors import GroupError
    class OmittingProfile(CollaborationProfile):
        def render_input(self, context):
            if mutate:
                context.triggers[0]['content'] = 'Replaced original'
                return super().render_input(context)
            return 'do task'
    model = ScriptedModel()
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)},
            worker_safe=True, profile=OmittingProfile()) as group:
        scope = await group.open_invocation('s')
        receipt = await group.request(scope, 'Protected original', key='r')
        with pytest.raises(GroupError, match='protected|source'):
            await admit(group, scope, 'a', receipt)
        assert (await group.assignments(scope)).items == ()
        assert (await group.opportunities(scope)).items[0].state == 'pending'
        assert model.requests == []


async def test_compacted_passive_sources_keep_verified_coverage_before_main_inference(tmp_path):
    from core.context_archive import FileContextArchive
    from group.profiles import CollaborationProfile
    class SummarizingModel(ScriptedModel):
        def invoke(self, messages, **kwargs):
            self.requests.append(messages)
            summary = messages[0].message.startswith('CONTEXT_COMPACTION')
            return {'choices': [{'message': AI('Sources retained as historical data; only assigned work is active.'
                                              if summary else 'done').to_dict(), 'finish_reason': 'stop'}]}
    model = SummarizingModel()
    agent = Agent(model, options=AgentOptions(context_window=6000, max_tokens=256),
                  archive_store=FileContextArchive(tmp_path / 'archive'))
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': agent}, worker_safe=True,
            profile=CollaborationProfile(tools=())) as group:
        scope = await group.open_invocation('s')
        for index in range(20):
            await group.post(scope, f'Source {index}: ' + 'x' * 700, key=str(index))
        receipt = await group.request(scope, 'Execute once after reading all sources', key='r')
        await admit(group, scope, 'a', receipt)
        await group.launch_ready(scope)
        await group.wait_idle()
        run = (await group.assignments(scope)).items[0]
        assert run.outcome == 'completed', run.error
        summaries = [request for request in model.requests if request[0].message.startswith('CONTEXT_COMPACTION')]
        assert summaries and len(model.requests) == len(summaries) + 1
        assert agent.session.checkpoint is not None
        assert (await group.reception(scope, 'a')).received_count == 21
        operations = await group.store.read(lambda db: db.execute('SELECT operation_id FROM context_applications').fetchall())
        assert all(agent.session.context_batch_covered(operation) for (operation,) in operations)
        assert any('group_reception' in str(message.message) for request in summaries for message in request)
        assert not any(item.sender == 'a' for item in (await group.history(scope)).items)


async def test_cancellation_does_not_wait_for_unlaunched_reception_backlog(tmp_path, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    model = ScriptedModel()
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(model)}, worker_safe=True)
    launch = None
    try:
        scope = await group.open_invocation('s')
        receipt = await group.request(scope, 'Task', key='r')
        await admit(group, scope, 'a', receipt)
        original = group.receive
        async def hold(scope):
            entered.set()
            await release.wait()
            return await original(scope)
        monkeypatch.setattr(group, 'receive', hold)
        launch = asyncio.create_task(group.launch_ready(scope))
        await entered.wait()
        await asyncio.wait_for(group.cancel(scope), 1)
        assert (await group.snapshot(scope)).state == 'terminal'
        assert model.requests == [] and not launch.done()
        release.set()
        assert await launch == ()
    finally:
        release.set()
        if launch:
            await asyncio.gather(launch, return_exceptions=True)
        await group.close()


async def test_finish_deadline_during_drain_never_reports_completed(tmp_path, monkeypatch):
    import group.lifecycle as lifecycle
    import time
    from group.errors import InvocationLimitError
    original = lifecycle.receive_page
    entered, release = Event(), Event()
    def hold(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(ScriptedModel())}, worker_safe=True,
        limits=GroupLimits(page_size=1, invocation_timeout=0.2))
    try:
        scope = await group.open_invocation('s')
        for index in range(3):
            await group.post(scope, str(index), key=str(index))
        monkeypatch.setattr(lifecycle, 'receive_page', hold)
        task = asyncio.create_task(group.finish(scope, revision=(await group.snapshot(scope)).revision))
        assert await asyncio.to_thread(entered.wait, 1)
        await asyncio.sleep(0.25)
        release.set()
        with pytest.raises(InvocationLimitError):
            await task
        assert (await group.snapshot(scope)).reason == 'timeout'
        assert (await group.reception(scope, 'a')).received_count < 3
    finally:
        release.set()
        await group.close()


async def test_group_configuration_cannot_change_under_owned_work(tmp_path):
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(ScriptedModel())},
            worker_safe=True, strategy=OnDemandStrategy()) as group:
        for attribute, value in [('limits', GroupLimits(input_bytes=1)), ('profile', None), ('strategy', None)]:
            with pytest.raises(AttributeError):
                setattr(group, attribute, value)


async def test_blocked_driver_reports_barrier_without_retrying_on_passive_posts(tmp_path):
    agent = Agent(ScriptedModel(), options=AgentOptions(context_window=256, max_tokens=64))
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': agent}, worker_safe=True,
            strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        await group.request(scope, 'Task', key='r')
        result = await group.drive(scope)
        assert result.status == 'needs_input' and len(result.blocked_assignment_ids) == 1
        original = tuple(agent.session.history)
        for index in range(3):
            await group.post(scope, str(index), key=str(index))
            assert (await group.drive(scope)).blocked_assignment_ids == result.blocked_assignment_ids
        assert tuple(agent.session.history) == original
        assert agent.llm.requests == []


@pytest.mark.parametrize('boundary', ['claim', 'manifest'])
async def test_close_during_claim_or_manifest_does_not_launch_new_worker(tmp_path, monkeypatch, boundary):
    entered, release = asyncio.Event(), asyncio.Event()
    model = ScriptedModel(AI('must not run'))
    path = tmp_path / 'g.sqlite'
    group = await GroupRuntime.create(path, {'a': Agent(model)}, worker_safe=True)
    launch = closing = None
    try:
        scope = await group.open_invocation('s')
        receipt = await group.request(scope, 'Task', key='r')
        await admit(group, scope, 'a', receipt)
        mutate, store_control = group._mutate, group._store_control
        async def pause_claim(operation, **kwargs):
            if operation.__name__ == 'claim':
                entered.set()
                await release.wait()
            return await mutate(operation, **kwargs)
        async def pause_manifest(operation, **kwargs):
            result = await store_control(operation, **kwargs)
            if isinstance(result, dict) and 'adapter_version' in result:
                entered.set()
                await release.wait()
            return result
        monkeypatch.setattr(group, '_mutate', pause_claim)
        if boundary == 'manifest':
            monkeypatch.setattr(group, '_mutate', mutate)
            monkeypatch.setattr(group, '_store_control', pause_manifest)
        launch = asyncio.create_task(group.launch_ready(scope))
        await asyncio.wait_for(entered.wait(), 1)
        closing = asyncio.create_task(group.close())
        await asyncio.sleep(0)
        assert group._closing is not None
        release.set()
        assert await launch == ()
        await closing
        assert model.requests == []
        async with await GroupRuntime.create(path, {'a': Agent(ScriptedModel())}, worker_safe=True) as reopened:
            snapshot = await reopened.snapshot(scope)
            assert snapshot.reason == 'cancelled' and snapshot.state == 'terminal'
    finally:
        release.set()
        if launch:
            await asyncio.gather(launch, return_exceptions=True)
        await group.close()
        if closing:
            await asyncio.gather(closing, return_exceptions=True)
