"""Regression cases for control and tool boundaries found during review."""

import asyncio
import json
from threading import Event

import pytest

from core.agent import Agent
from group import DispatchPlan, GroupLimits, GroupRuntime, RunProposal
from group.errors import CapacityError, GroupError, StoreFailedError
from models import AI, ToolCall
from tests.runtime_fakes import ScriptedModel
from tools.registry import ToolRegistry


async def start(group, scope, member='a'):
    await group.open_invocation(scope)
    snap = await group.snapshot(scope)
    await group.commit_plan(DispatchPlan(scope, snap.revision,
        runs=(RunProposal(member, 'test', origin_key='run'),)))
    await group.launch_ready(scope)


async def test_cancelling_old_scope_cannot_stop_current_execution(tmp_path):
    entered, release = Event(), Event()
    class Blocking(ScriptedModel):
        def invoke(self, *args, **kwargs):
            entered.set()
            release.wait(5)
            return super().invoke(*args, **kwargs)
    agent = Agent(Blocking(AI(tool_calls=[ToolCall('p', 'group_post', {'content': 'current post'})]), AI('done')))
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': agent}, worker_safe=True)
    try:
        await group.open_invocation('old')
        await group.finish('old', revision=(await group.snapshot('old')).revision)
        await start(group, 'current')
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.wait_for(group.cancel('old'), 0.5)
        release.set()
        await group.wait_idle()
        assert (await group.snapshot('current')).assignments.items[0].outcome == 'completed'
        assert (await group.history('current')).items[0].content == 'current post'
    finally:
        release.set()
        await group.close()


async def test_history_tools_return_parseable_pages_and_full_original_via_chunks(tmp_path):
    from core.agent_runtime.options import AgentOptions
    model = ScriptedModel()
    # Full reception includes all seven originals before testing retrieval.
    agent = Agent(model, options=AgentOptions(context_window=1048576, active_message_limit=0))
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': agent}, worker_safe=True)
    try:
        await group.open_invocation('s')
        original = '\x01' * 16000
        receipt = await group.post('s', original, key='first')
        for i in range(6):
            await group.post('s', 'x' * 16000, key=f'p{i}')
        model.responses.extend([
            AI(tool_calls=[ToolCall('h', 'group_history', {})]),
            AI(tool_calls=[ToolCall('r', 'group_message', {'message_id': receipt.message_id, 'offset': 0, 'limit': 16000})]),
            AI('done'),
        ])
        await start(group, 's')
        await group.wait_idle()
        result = [message.message for message in agent.session.history if message.role == 'tool']
        page = json.loads(result[0])
        assert page['items'][0]['content_complete'] is False
        part = json.loads(result[1])
        assert original.startswith(part['content']) and part['next_offset'] > 0
        assert part['exhausted'] is False
        assert (await group.message('s', receipt.message_id)).content == original
    finally:
        await group.close()


async def test_failed_read_wakes_revision_waiter(tmp_path):
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(ScriptedModel())}, worker_safe=True)
    await group.open_invocation('s')
    revision = (await group.snapshot('s')).revision
    waiter = asyncio.create_task(group.wait_for_change(revision))
    await asyncio.sleep(0.02)
    try:
        import sqlite3
        with pytest.raises(sqlite3.DatabaseError):
            await group.store.read(lambda db: db.execute('SELECT missing FROM missing'))
        with pytest.raises(StoreFailedError):
            await asyncio.wait_for(waiter, 1)
    finally:
        with pytest.raises(StoreFailedError):
            await group.close()


async def test_failed_tool_install_rolls_back_partial_registry_changes(tmp_path):
    class RejectingRegistry(ToolRegistry):
        def register(self, function, **kwargs):
            if function.__name__ == 'group_request':
                raise ValueError('registration rejected')
            return super().register(function, **kwargs)
    registry = RejectingRegistry()
    agent = Agent(ScriptedModel(), registry=registry)
    with pytest.raises(ValueError, match='registration rejected'):
        await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': agent}, worker_safe=True)
    assert not registry.names()
    agent.system_prompt = 'still usable'


async def test_invalid_page_filter_does_not_poison_store(tmp_path):
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(ScriptedModel())}, worker_safe=True)
    try:
        await group.open_invocation('s')
        with pytest.raises((TypeError, GroupError)):
            await group.history('s', state='pending')
        assert not group.store.failed
        await group.post('s', 'still works', key='p')
    finally:
        await group.close()


async def test_duplicate_launch_controls_share_a_bounded_owned_operation(tmp_path):
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': Agent(ScriptedModel())}, worker_safe=True,
                                      limits=GroupLimits(queue_jobs=2))
    await group.open_invocation('s')
    await group._launch_lock.acquire()
    tasks = [asyncio.create_task(group.launch_ready('s')) for _ in range(30)]
    try:
        await asyncio.sleep(0)
        # Pending controls must coalesce or reject, never silently spool jobs.
        assert len(group._operations) <= 2
    finally:
        group._launch_lock.release()
        await asyncio.gather(*tasks, return_exceptions=True)
        await group.close()


async def test_default_tool_queries_respect_smaller_configured_limits(tmp_path):
    model = ScriptedModel()
    agent = Agent(model)
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'a': agent}, worker_safe=True,
        limits=GroupLimits(page_size=1, max_page_size=1, message_bytes=128))
    try:
        await group.open_invocation('s')
        receipt = await group.post('s', 'Complete original', key='p')
        model.responses.extend([AI(tool_calls=[
            ToolCall('h', 'group_history', {}),
            ToolCall('m', 'group_message', {'message_id': receipt.message_id}),
        ]), AI('done')])
        await start(group, 's')
        await group.wait_idle()
        results = [json.loads(m.message) for m in agent.session.history if m.role == 'tool']
        assert results[0]['exhausted'] is True
        assert results[1]['content'] == 'Complete original' and results[1]['exhausted'] is True
    finally:
        await group.close()
