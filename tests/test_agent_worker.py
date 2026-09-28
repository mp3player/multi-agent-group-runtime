"""Exclusive synchronous Agent execution on a dedicated worker."""

import asyncio
from contextvars import ContextVar, copy_context
from threading import Event
from types import SimpleNamespace

import pytest

from core.agent import Agent
from core.agent_runtime.run_state import AgentBusyError
from core.agent_runtime.worker import AgentWorker
from models import AI, Message, ToolCall
from tests.runtime_fakes import ScriptedModel
from tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_worker_owns_agent_and_runs_with_copied_context():
    marker = ContextVar('worker_marker', default='absent')
    class ContextModel(ScriptedModel):
        def invoke(self, *args, **kwargs):
            assert marker.get() == 'bound'
            return super().invoke(*args, **kwargs)

    registry = ToolRegistry()
    def inspect_context():
        assert marker.get() == 'bound'
        return 'seen'
    registry.register(inspect_context)
    agent = Agent(ContextModel(AI(tool_calls=[ToolCall('c1', 'inspect_context', {})]), AI('done')),
                  registry=registry)
    worker = AgentWorker(agent, worker_safe=True)
    try:
        with pytest.raises(AgentBusyError):
            agent.run('direct')
        with pytest.raises(AgentBusyError):
            agent.system_prompt = 'changed'
        marker.set('bound')
        execution = worker.submit('work', context=copy_context())
        assert await execution.wait() == execution.outcome
        assert execution.outcome.status == 'completed'
        assert execution.outcome.result == 'done'
        assert execution.settled
        assert [m.role for m in agent.session.history] == ['user', 'assistant', 'tool', 'assistant']
        assert agent.session.history[2].message == 'seen'
    finally:
        await worker.close()
    assert agent.run_state.active is False


@pytest.mark.asyncio
async def test_worker_rejects_second_submission_before_it_mutates_input():
    entered, release = Event(), Event()
    class BlockingModel(ScriptedModel):
        def invoke(self, *args, **kwargs):
            entered.set()
            release.wait(3)
            return super().invoke(*args, **kwargs)

    agent = Agent(BlockingModel(AI('done')))
    worker = AgentWorker(agent, worker_safe=True)
    try:
        first = worker.submit('first')
        assert await asyncio.to_thread(entered.wait, 2)
        with pytest.raises(AgentBusyError):
            worker.submit('second')
        assert [m.message for m in agent.session.history] == ['first']
        release.set()
        assert (await first.wait()).status == 'completed'
    finally:
        release.set()
        await worker.close()


@pytest.mark.asyncio
async def test_worker_waiter_cancellation_and_stop_keep_lease_until_blocking_work_finishes():
    entered, release = Event(), Event()
    effects = []
    registry = ToolRegistry()
    registry.register(lambda: effects.append('ran'), name='effect')
    class BlockingModel(ScriptedModel):
        def invoke(self, *args, **kwargs):
            entered.set()
            release.wait(3)
            return super().invoke(*args, **kwargs)

    agent = Agent(BlockingModel(AI(tool_calls=[ToolCall('c1', 'effect', {})])), registry=registry)
    worker = AgentWorker(agent, worker_safe=True)
    try:
        execution = worker.submit('first')
        assert await asyncio.to_thread(entered.wait, 2)
        waiter = asyncio.create_task(execution.wait())
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        execution.request_stop()
        execution.request_stop()
        assert not execution.settled
        with pytest.raises(AgentBusyError):
            worker.submit('second')
        release.set()
        assert (await execution.wait()).status == 'cancelled'
        assert effects == []
        assert [m.role for m in agent.session.history] == ['user', 'assistant', 'tool']
    finally:
        release.set()
        await worker.close()


@pytest.mark.asyncio
async def test_worker_uses_custom_executor_and_preserves_terminal_status():
    registry = ToolRegistry()
    registry.register(lambda: 'unused', name='custom')
    agent = Agent(ScriptedModel(AI(tool_calls=[ToolCall('c1', 'custom', {})]), AI('done')),
                  registry=registry)
    def custom_execute(calls):
        result = Message(role='tool', message='custom result')
        result.tool_call_id = calls[0].id
        return [result]
    agent.tool_executor.execute = custom_execute
    worker = AgentWorker(agent, worker_safe=True)
    try:
        outcome = await worker.submit('work').wait()
        assert outcome.status == 'completed' and outcome.result == 'done'
        assert agent.session.history[2].message == 'custom result'
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_worker_reports_failure_and_rejects_unsafe_bindings():
    class BrokenModel(ScriptedModel):
        def invoke(self, *args, **kwargs):
            raise ValueError('provider failed')

    agent = Agent(BrokenModel())
    with pytest.raises(ValueError):
        AgentWorker(agent, worker_safe=False)
    worker = AgentWorker(agent, worker_safe=True)
    try:
        outcome = await worker.submit('work').wait()
        assert outcome.status == 'error'
        assert 'provider failed' in outcome.error
    finally:
        await worker.close()

    class AsyncModel(ScriptedModel):
        async def invoke(self, *args, **kwargs):
            return {}
    with pytest.raises(TypeError):
        AgentWorker(Agent(AsyncModel()), worker_safe=True)

    registry = ToolRegistry()
    async def async_tool():
        return 'no'
    registry.register(async_tool)
    with pytest.raises(TypeError):
        AgentWorker(Agent(ScriptedModel(AI('done')), registry=registry), worker_safe=True)


@pytest.mark.asyncio
async def test_close_waiter_cancellation_keeps_ownership_until_real_settlement():
    entered, release = Event(), Event()
    class BlockingModel(ScriptedModel):
        def invoke(self, *args, **kwargs):
            entered.set()
            release.wait(3)
            return super().invoke(*args, **kwargs)

    agent = Agent(BlockingModel(AI('done')))
    worker = AgentWorker(agent, worker_safe=True)
    execution = worker.submit('work')
    assert await asyncio.to_thread(entered.wait, 2)
    closing = asyncio.create_task(worker.close())
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert not execution.settled
    with pytest.raises(AgentBusyError):
        agent.run('direct')
    release.set()
    await worker.close()
    assert execution.settled
    assert (await execution.wait()).status == 'completed'
    agent.system_prompt = 'after close'
    assert agent.system_prompt == 'after close'


@pytest.mark.asyncio
async def test_worker_keeps_terminal_tool_stop_and_timeout_statuses():
    registry = ToolRegistry()
    registry.register(lambda: 'finished', name='finish', ends_run=True)
    stopped = Agent(ScriptedModel(AI(tool_calls=[ToolCall('t1', 'finish', {})])), registry=registry)
    worker = AgentWorker(stopped, worker_safe=True)
    try:
        assert (await worker.submit('work').wait()).status == 'tool_stop'
    finally:
        await worker.close()

    endless_registry = ToolRegistry()
    endless_registry.register(lambda: 'again', name='again')
    limited = Agent(ScriptedModel(AI(tool_calls=[ToolCall('loop', 'again', {})])),
                    registry=endless_registry, max_turns=1)
    worker = AgentWorker(limited, worker_safe=True)
    try:
        assert (await worker.submit('work').wait()).status == 'max_turns'
    finally:
        await worker.close()

    class SlowModel(ScriptedModel):
        def invoke(self, *args, **kwargs):
            import time
            time.sleep(0.02)
            return super().invoke(*args, **kwargs)
    timed = Agent(SlowModel(AI('late')), run_timeout=0.001)
    worker = AgentWorker(timed, worker_safe=True)
    try:
        outcome = await worker.submit('work').wait()
        assert outcome.status == 'timeout'
        assert 'AgentTimeoutError' in outcome.error
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_stop_during_run_start_prevents_user_input_admission():
    entered, release = Event(), Event()
    agent = Agent(ScriptedModel(AI('unused')))
    def observe(event):
        if event.type == 'run_start':
            entered.set()
            release.wait(3)
    unsubscribe = agent.subscribe(observe)
    worker = AgentWorker(agent, worker_safe=True)
    try:
        execution = worker.submit('must not be added')
        assert await asyncio.to_thread(entered.wait, 2)
        execution.request_stop()
        release.set()
        assert (await execution.wait()).status == 'cancelled'
        assert agent.session.history == []
    finally:
        release.set()
        unsubscribe()
        await worker.close()


@pytest.mark.asyncio
async def test_late_stop_after_run_exit_does_not_cancel_next_submission():
    entered, release = Event(), Event()
    agent = Agent(ScriptedModel(AI('first'), AI('second')))
    original_exit = agent.run_state.exit_run
    def paused_exit():
        original_exit()
        entered.set()
        release.wait(3)
    agent.run_state.exit_run = paused_exit
    worker = AgentWorker(agent, worker_safe=True)
    try:
        first = worker.submit('one')
        assert await asyncio.to_thread(entered.wait, 2)
        first.request_stop()
        release.set()
        assert (await first.wait()).status == 'completed'
        agent.run_state.exit_run = original_exit
        second = await worker.submit('two').wait()
        assert second.status == 'completed' and second.result == 'second'
    finally:
        release.set()
        agent.run_state.exit_run = original_exit
        await worker.close()


@pytest.mark.asyncio
async def test_stale_execution_stop_cannot_interrupt_active_successor():
    entered, release = Event(), Event()
    class SecondBlocks(ScriptedModel):
        def invoke(self, *args, **kwargs):
            if len(self.requests) == 1:
                entered.set()
                release.wait(3)
            return super().invoke(*args, **kwargs)
    agent = Agent(SecondBlocks(AI('first'), AI('second')))
    worker = AgentWorker(agent, worker_safe=True)
    try:
        first = worker.submit('one')
        assert (await first.wait()).status == 'completed'
        second = worker.submit('two')
        assert await asyncio.to_thread(entered.wait, 2)
        first.request_stop()
        release.set()
        assert (await second.wait()).status == 'completed'
    finally:
        release.set()
        await worker.close()


@pytest.mark.asyncio
async def test_worker_rejects_coroutine_hooks_hidden_behind_sync_dispatch():
    async def asynchronous(*args, **kwargs):
        return []
    paths = (
        lambda agent: setattr(agent.runtime.tool_runtime, 'execute', asynchronous),
        lambda agent: setattr(agent.runtime.tool_runtime, 'permission_policy',
                              SimpleNamespace(check=asynchronous)),
        lambda agent: setattr(agent.runtime.tool_runtime, 'audit_log',
                              SimpleNamespace(append=asynchronous, sink=None)),
    )
    for install in paths:
        agent = Agent(ScriptedModel(AI('unused')))
        install(agent)
        with pytest.raises(TypeError, match='async'):
            AgentWorker(agent, worker_safe=True)
        assert agent.run_state.active is False
        assert agent.run('direct') == 'unused'


def test_worker_rejects_async_configured_audit_sink_before_ownership():
    async def write(record):
        return None
    registry = ToolRegistry()
    registry.register(lambda: 'done', name='finish')
    agent = Agent(ScriptedModel(AI('direct')), registry=registry)
    agent.runtime.tool_runtime.audit_log.set_sink(SimpleNamespace(write=write))

    with pytest.raises(TypeError, match='async audit sink write'):
        AgentWorker(agent, worker_safe=True)

    assert agent.run('after rejection') == 'direct'


def test_worker_rejects_async_configured_archive_store_before_ownership():
    class ArchiveStore:
        async def write(self, *args, **kwargs):
            return 'archive'
        def load(self, *args, **kwargs):
            return None
        def read(self, *args, **kwargs):
            return None

    agent = Agent(ScriptedModel(AI('unused')))
    agent.session.archive_store = ArchiveStore()

    with pytest.raises(TypeError, match='async archive write'):
        AgentWorker(agent, worker_safe=True)


def test_public_sync_run_events_raises_without_yielding_error_terminal():
    class BrokenModel(ScriptedModel):
        def invoke(self, *args, **kwargs):
            raise ValueError('failed')
    agent = Agent(BrokenModel())
    yielded, observed = [], []
    agent.subscribe(observed.append)
    with pytest.raises(ValueError, match='failed'):
        for event in agent.run_events('work'):
            yielded.append(event)
    assert not any(event.type == 'run_end' for event in yielded)
    assert observed[-1].type == 'run_end' and observed[-1].status == 'error'
