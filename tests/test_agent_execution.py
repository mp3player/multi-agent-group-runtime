"""Public execution contracts shared by all execution transports."""

import asyncio
from contextvars import ContextVar
from copy import deepcopy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.agent import Agent, AgentTimeoutError
from models import AI, Chunk, Message, ToolCall, User
from tools.registry import ToolRegistry
from runtime_fakes import ScriptedModel, execute

MODES = ["run", "arun", "run_stream", "arun_stream"]


@pytest.mark.parametrize("mode", MODES)
async def test_events_describe_committed_tool_work_and_final_result(mode):
    registry = ToolRegistry()
    def echo(value: str) -> str:
        return f"tool:{value}"

    registry.register(echo)
    model = ScriptedModel(
        AI(tool_calls=[ToolCall("echo-1", "echo", {"value": "x"})]),
        AI("done", reasoning="checked"),
    )
    agent = Agent(model, registry=registry)
    events = []
    committed = []

    def observe(event):
        events.append(event)
        if event.type == "tool_start":
            committed.append(agent.session.history[-1].tool_calls[0].id)
        if event.type == "tool_end":
            committed.append(agent.session.history[-1].message)

    agent.subscribe(observe)
    assert await execute(agent, mode) == "done"
    assert committed == ["echo-1", "tool:x"]
    assert [e.type for e in events if e.type != "message_delta"] == [
        "run_start", "turn_start", "message_end", "tool_start", "tool_end",
        "message_end", "turn_end", "turn_start", "message_end", "turn_end", "run_end",
    ]
    assert len({e.run_id for e in events}) == 1
    assert [e.turn for e in events if e.type == "turn_start"] == [1, 2]
    assert events[-1].status == "completed"
    assert events[-1].result == "done"
    assert agent.session.history[-1].reasoning == "checked"
    assert [m.role for m in agent.session.history] == ["user", "assistant", "tool", "assistant"]
    assert registry.tool_audit_log.records()[0].result == "tool:x"


@pytest.mark.parametrize("mode", MODES)
async def test_pass_stops_after_all_tool_results_without_another_model_call(mode):
    registry = ToolRegistry()
    performed = []
    registry.register(lambda: "PASS", name="finish_task", ends_run=True)
    registry.register(lambda: performed.append("work"), name="work")
    model = ScriptedModel(AI(tool_calls=[
        ToolCall("pass-1", "finish_task", {}), ToolCall("work-1", "work", {}),
    ]))
    agent = Agent(model, registry=registry)
    events = []
    agent.subscribe(events.append)
    assert await execute(agent, mode) == ""
    assert len(model.requests) == 1
    assert performed == ["work"]
    assert [m.tool_call_id for m in agent.session.history if m.role == "tool"] == ["pass-1", "work-1"]
    assert events[-1].status == "tool_stop"


@pytest.mark.parametrize("mode", MODES)
async def test_context_projection_changes_requests_without_rewriting_session(mode):
    seen = []

    def transform(messages):
        seen.append(deepcopy(messages))
        messages[0].message = "projected user"
        messages.append(User("temporary context"))
        return messages

    model = ScriptedModel(AI("done"))
    agent = Agent(model, context_transform=transform)
    assert await execute(agent, mode, "original user") == "done"
    assert [m.message for m in model.requests[0]] == ["projected user", "temporary context"]
    assert [m.message for m in agent.session.history] == ["original user", "done"]
    assert [m.message for m in agent.session.active] == ["original user", "done"]
    assert seen[0][0].message == "original user"


@pytest.mark.parametrize("mode", MODES)
async def test_next_request_uses_current_tools_and_projected_tool_results(mode):
    registry = ToolRegistry()

    def new_tool():
        return "installed"

    def enable_tool():
        registry.register(new_tool)
        return "enabled"

    registry.register(enable_tool)
    model = ScriptedModel(
        AI(tool_calls=[ToolCall("enable", "enable_tool", {})]),
        AI(tool_calls=[ToolCall("use", "new_tool", {})]),
        AI("done"),
    )
    projections = []

    def project(messages):
        projections.append([m.role for m in messages])
        return messages

    agent = Agent(model, registry=registry, context_transform=project)
    assert await execute(agent, mode) == "done"
    assert [t["function"]["name"] for t in model.tool_schemas[0]] == ["enable_tool"]
    assert [t["function"]["name"] for t in model.tool_schemas[1]] == ["enable_tool", "new_tool"]
    assert projections == [
        ["user"], ["user", "assistant", "tool"],
        ["user", "assistant", "tool", "assistant", "tool"],
    ]
    assert [m.message for m in agent.session.history if m.role == "tool"] == ["enabled", "installed"]


def test_observers_are_isolated_from_history_and_each_other(caplog):
    agent = Agent(ScriptedModel(AI("done"), AI("again")))
    events = []

    def corrupt(event):
        if event.message is not None:
            event.message.message = "corrupted"
        raise ValueError("observer failed")

    unsubscribe = agent.subscribe(corrupt)
    agent.subscribe(events.append)
    assert agent.run("go") == "done"
    assert [e.message.message for e in events if e.type == "message_end"] == ["done"]
    assert agent.session.history[-1].message == "done"
    assert "observer failed" in caplog.text
    unsubscribe()
    unsubscribe()
    caplog.clear()
    assert agent.run("again") == "again"
    assert not caplog.records
    assert len({e.run_id for e in events}) == 2


@pytest.mark.parametrize("mode", MODES)
async def test_iteration_limit_returns_feedback_and_reports_reason(mode):
    agent = Agent(ScriptedModel(AI(tool_calls=[ToolCall("x", "missing", {})])), max_turns=1)
    events = []
    agent.subscribe(events.append)
    result = await execute(agent, mode)
    assert events[-1].status == "max_turns"
    assert result == "(Maximum turns reached without a final response)"
    assert agent.session.history[-1].message == result


@pytest.mark.parametrize("mode", ["run_stream", "arun_stream"])
async def test_final_stream_chunk_can_carry_text_and_reasoning(mode):
    class FinalChunkModel(ScriptedModel):
        def stream(self, *args, **kwargs):
            yield Chunk(message="done", reasoning="checked", finish_reason="stop")

    agent = Agent(FinalChunkModel())
    assert await execute(agent, mode) == "done"
    assert agent.session.history[-1].reasoning == "checked"


def test_closing_stream_closes_provider_and_releases_single_flight():
    model = ScriptedModel(AI("partial"), AI("next"))
    agent = Agent(model)
    events = []
    agent.subscribe(events.append)
    stream = agent.run_stream("go")
    assert next(stream).message == "partial"
    stream.close()
    assert model.streams_closed == 1
    assert events[-1].status == "closed"
    assert not agent.run_state.active
    assert agent.run("continue") == "next"


async def test_closing_async_stream_closes_provider_and_releases_single_flight():
    model = ScriptedModel(AI("partial"), AI("next"))
    agent = Agent(model)
    events = []
    agent.subscribe(events.append)
    stream = agent.arun_stream("go")
    assert (await anext(stream)).message == "partial"
    await stream.aclose()
    assert model.streams_closed == 1
    assert events[-1].status == "closed"
    assert not agent.run_state.active
    assert await agent.arun("continue") == "next"


async def test_overlapping_runs_cannot_add_messages_and_cancelled_run_releases_guard():
    started = asyncio.Event()

    class WaitingModel(ScriptedModel):
        async def ainvoke(self, *args, **kwargs):
            started.set()
            await asyncio.Event().wait()

    agent = Agent(WaitingModel())
    events = []
    agent.subscribe(events.append)
    task = asyncio.create_task(agent.arun("first"))
    try:
        await asyncio.wait_for(started.wait(), 1)
        with pytest.raises(RuntimeError, match="already running"):
            await agent.arun("overlap")
        assert [m.message for m in agent.session.history] == ["first"]
        assert agent.run_state.active
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not agent.run_state.active
    assert events[-1].status == "cancelled"
    agent.llm = ScriptedModel(AI("next"))
    assert await agent.arun("continue") == "next"


async def test_stalled_async_stream_obeys_deadline_and_closes_provider():
    closed = []

    class StalledModel(ScriptedModel):
        async def astream(self, *args, **kwargs):
            try:
                await asyncio.Event().wait()
                yield Chunk(message="unreachable")
            finally:
                closed.append(True)

    agent = Agent(StalledModel(), run_timeout=0.01)
    events = []
    agent.subscribe(events.append)
    with pytest.raises(AgentTimeoutError):
        await asyncio.wait_for(execute(agent, "arun_stream"), 1)
    assert closed == [True]
    assert not agent.run_state.active
    assert events[-1].status == "timeout"


def test_context_failure_reports_error_and_agent_can_run_again():
    def broken(messages):
        raise ValueError("bad projection")

    agent = Agent(ScriptedModel(AI("next")), context_transform=broken)
    events = []
    agent.subscribe(events.append)
    with pytest.raises(ValueError, match="bad projection"):
        agent.run("go")
    assert events[-1].status == "error"
    assert not agent.run_state.active
    agent.context_transform = None
    assert agent.run("retry") == "next"


@pytest.mark.parametrize("is_async", [False, True])
async def test_event_iterator_is_a_detached_view_of_the_same_execution(is_async):
    agent = Agent(ScriptedModel(AI("done")))
    observed = []
    agent.subscribe(observed.append)
    if is_async:
        events = [event async for event in agent.arun_events("go")]
    else:
        events = list(agent.run_events("go"))
    assert [e.type for e in events] == [e.type for e in observed]
    assert events[-1].result == "done"
    next(e for e in events if e.message is not None).message.message = "changed"
    assert agent.session.history[-1].message == "done"
    assert next(e for e in observed if e.message is not None).message.message == "done"


async def test_stream_provider_keeps_context_across_chunks_and_cleanup():
    scope = ContextVar("provider_scope", default="caller")

    class ScopedModel(ScriptedModel):
        async def astream(self, *args, **kwargs):
            token = scope.set("provider")
            try:
                yield Chunk(message="first")
                await asyncio.sleep(0)
                assert scope.get() == "provider"
                yield Chunk(message="second")
            finally:
                scope.reset(token)

    agent = Agent(ScopedModel(), run_timeout=1)
    assert await execute(agent, "arun_stream") == "firstsecond"
    assert scope.get() == "caller"


@pytest.mark.parametrize("after_first_tool", [False, True])
@pytest.mark.parametrize("is_async", [False, True])
async def test_closing_after_tool_request_finalizes_unanswered_calls_without_executing_them(after_first_tool, is_async):
    performed = []
    registry = ToolRegistry()

    def work(value: str):
        performed.append(value)
        return value

    registry.register(work)
    model = ScriptedModel(AI(tool_calls=[
        ToolCall("one", "work", {"value": "first"}),
        ToolCall("two", "work", {"value": "second"}),
    ]), AI("retry done"))
    agent = Agent(model, registry=registry)
    events = []
    agent.subscribe(events.append)
    stream = agent.arun_events("go") if is_async else agent.run_events("go")
    while True:
        event = await anext(stream) if is_async else next(stream)
        if after_first_tool and event.type == "tool_end":
            break
        if not after_first_tool and event.type == "message_end" and event.message.role == "assistant":
            break
    if is_async:
        await stream.aclose()
    else:
        stream.close()
    assert performed == (["first"] if after_first_tool else [])
    assert len(registry.tool_audit_log.records()) == (1 if after_first_tool else 0)
    results = [m for m in agent.session.active if m.role == "tool"]
    assert [m.tool_call_id for m in results] == ["one", "two"]
    assert "interrupted" in results[-1].message.lower()
    assert events[-1].status == "closed"
    assert agent.run("retry") == "retry done"
    sent_results = [m for m in model.requests[-1] if m.role == "tool"]
    assert [m.tool_call_id for m in sent_results] == ["one", "two"]


@pytest.mark.parametrize("explicit_close", [False, True])
async def test_slow_stream_cleanup_is_bounded_and_releases_run(explicit_close):
    closed = []

    class SlowClosingStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if explicit_close:
                return Chunk(message="first")
            await asyncio.Event().wait()

        async def aclose(self):
            try:
                await asyncio.Event().wait()
            finally:
                closed.append(True)

    class SlowClosingModel(ScriptedModel):
        def astream(self, *args, **kwargs):
            return SlowClosingStream()

    # Explicit closure has its own cleanup deadline; a run deadline here could
    # expire during worker startup before the stream under test is even opened.
    agent = Agent(SlowClosingModel(), run_timeout=None if explicit_close else 0.01)
    events = []
    agent.subscribe(events.append)
    if explicit_close:
        stream = agent.arun_stream("go")
        assert (await anext(stream)).message == "first"
        await asyncio.wait_for(stream.aclose(), 1)
    else:
        with pytest.raises(AgentTimeoutError):
            await asyncio.wait_for(execute(agent, "arun_stream"), 1)
    assert closed == [True]
    assert not agent.run_state.active
    assert events[-1].status == ("closed" if explicit_close else "timeout")


def test_partial_tool_batch_timeout_preserves_completed_effect_and_closes_pending_calls():
    from time import sleep

    registry = ToolRegistry()
    performed = []

    def work(value: str):
        performed.append(value)
        sleep(0.02)
        return value

    registry.register(work)
    agent = Agent(ScriptedModel(AI(tool_calls=[
        ToolCall("one", "work", {"value": "first"}),
        ToolCall("two", "work", {"value": "second"}),
    ])), registry=registry, run_timeout=0.01)
    with pytest.raises(AgentTimeoutError):
        agent.run("go")
    results = [m for m in agent.session.active if m.role == "tool"]
    assert performed == ["first"]
    assert results[0].message == "first"
    assert results[0].tool_call_id == "one"
    assert results[1].tool_call_id == "two"
    assert "interrupted" in results[1].message.lower()
    assert len(registry.tool_audit_log.records()) == 1


async def test_external_cancellation_is_preserved_when_deadline_fires_in_same_loop_tick():
    from time import sleep

    class CancelledModel(ScriptedModel):
        async def ainvoke(self, *args, **kwargs):
            loop = asyncio.get_running_loop()
            # Hold the loop so the external cancellation and run deadline are
            # both ready before the suspended model task can resume.
            loop.call_later(0.005, asyncio.current_task().cancel, "external cancel")
            loop.call_later(0.001, sleep, 0.02)
            await asyncio.Event().wait()

    agent = Agent(CancelledModel(), run_timeout=0.01)
    events = []
    agent.subscribe(events.append)
    reasons = []

    async def run():
        try:
            await agent.arun("go")
        except asyncio.CancelledError as error:
            # Python 3.10 can drop the message when cancellation crosses a
            # Task boundary; verify its origin inside the cancelled task.
            reasons.append(str(error))
            raise

    task = asyncio.create_task(run())
    with pytest.raises(asyncio.CancelledError):
        await task
    assert reasons == ["external cancel"]
    assert task.cancelled()
    assert events[-1].status == "cancelled"
    assert not agent.run_state.active
