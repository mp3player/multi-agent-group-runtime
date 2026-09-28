"""Cleanup failures must not strand a run or hide its original failure."""

import asyncio
from contextvars import ContextVar

import httpx
import pytest

from core.agent import Agent, AgentTimeoutError
from core.agent_runtime.options import AgentOptions
from core.llm import LLMClient, LLMError
from models import AI, Chunk
from tests.runtime_fakes import ScriptedModel


@pytest.mark.parametrize("http_transport", [False, True])
async def test_deadline_bounds_cleanup_inside_anext_and_preserves_caller_context(http_transport):
    cleanup_started = asyncio.Event()
    cleanup_finished = asyncio.Event()
    release = asyncio.Event()
    scope = ContextVar("stream_cleanup_scope", default="caller")
    caller = None

    async def cleanup(token):
        assert asyncio.current_task() is caller
        assert scope.get() == "provider"
        cleanup_started.set()
        try:
            await release.wait()
        finally:
            scope.reset(token)
            cleanup_finished.set()

    class StalledModel(ScriptedModel):
        async def astream(self, *args, **kwargs):
            token = scope.set("provider")
            try:
                assert asyncio.current_task() is caller
                await asyncio.Event().wait()
                yield Chunk(message="unreachable")
            finally:
                await cleanup(token)

    class StalledBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            self.token = scope.set("provider")
            assert asyncio.current_task() is caller
            await asyncio.Event().wait()
            yield b"unreachable"

        async def aclose(self):
            await cleanup(self.token)

    def respond(request):
        if not cleanup_finished.is_set():
            return httpx.Response(200, stream=StalledBody())
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"role": "assistant", "content": "recovered"},
        }]})

    if http_transport:
        model = LLMClient(base_url="http://unused.invalid", api_key="fixture", model="fixture")
        model._async_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    else:
        model = StalledModel(AI("recovered"))
    agent = Agent(model, run_timeout=0.01, options=AgentOptions(context_window=131072))
    events = []
    agent.subscribe(events.append)

    async def consume():
        nonlocal caller
        caller = asyncio.current_task()
        before = getattr(caller, "cancelling", lambda: 0)()
        try:
            with pytest.raises(AgentTimeoutError):
                async for _ in agent.arun_stream("go"):
                    pass
        finally:
            assert scope.get() == "caller"
            assert getattr(caller, "cancelling", lambda: 0)() == before

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(cleanup_started.wait(), 1)
        done, _ = await asyncio.wait({task}, timeout=1)
        assert task in done, "provider cleanup outlived the run deadline and cleanup grace"
        await task
        assert cleanup_finished.is_set()
        assert not agent.run_state.active
        assert events[-1].status == "timeout"
        assert await agent.arun("continue") == "recovered"
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        if http_transport:
            await model.aclose()


@pytest.mark.parametrize("cancel_before_deadline", [False, True])
async def test_external_cancellation_survives_deadline_during_generator_cleanup(cancel_before_deadline):
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_finished = asyncio.Event()
    release = asyncio.Event()
    reasons = []
    counts = []

    class Model(ScriptedModel):
        async def astream(self, *args, **kwargs):
            try:
                started.set()
                await asyncio.Event().wait()
                yield Chunk(message="unreachable")
            finally:
                cleanup_started.set()
                try:
                    await release.wait()
                finally:
                    cleanup_finished.set()

    agent = Agent(Model(AI("recovered")), run_timeout=0.05)
    events = []
    agent.subscribe(events.append)

    async def consume():
        try:
            async for _ in agent.arun_stream("go"):
                pass
        except asyncio.CancelledError as error:
            # Python 3.10 may drop the message when crossing a Task boundary.
            reasons.append(str(error))
            counts.append(getattr(asyncio.current_task(), "cancelling", lambda: 1)())
            raise

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for((started if cancel_before_deadline else cleanup_started).wait(), 1)
        task.cancel("external cancel")
        done, _ = await asyncio.wait({task}, timeout=1)
        assert task in done
        with pytest.raises(asyncio.CancelledError):
            await task
        assert reasons == ["external cancel"]
        assert counts == [1]
        assert cleanup_finished.is_set()
        assert not agent.run_state.active
        assert events[-1].status == "cancelled"
        assert await agent.arun("continue") == "recovered"
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.skipif(not hasattr(asyncio.Task, "cancelling"), reason="requires cancellation counts")
async def test_provider_replacing_internal_cancellation_still_reports_run_timeout():
    class Model(ScriptedModel):
        async def astream(self, *args, **kwargs):
            try:
                await asyncio.Event().wait()
                yield Chunk(message="unreachable")
            except asyncio.CancelledError:
                raise asyncio.CancelledError() from None

    agent = Agent(Model(AI("recovered")), run_timeout=0.01)
    events = []
    agent.subscribe(events.append)
    caller = asyncio.current_task()
    before = caller.cancelling()
    with pytest.raises(AgentTimeoutError):
        async for _ in agent.arun_stream("go"):
            pass
    assert caller.cancelling() == before
    assert events[-1].status == "timeout"
    assert not agent.run_state.active
    assert await agent.arun("continue") == "recovered"


async def test_cancellation_suppressing_cleanup_keeps_guard_until_provider_finishes():
    grace_cancelled = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    class Model(ScriptedModel):
        async def astream(self, *args, **kwargs):
            try:
                await asyncio.Event().wait()
                yield Chunk(message="unreachable")
            finally:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    grace_cancelled.set()
                    await release.wait()
                finally:
                    finished.set()

    agent = Agent(Model(AI("recovered")), run_timeout=0.01)

    async def consume():
        with pytest.raises(AgentTimeoutError):
            async for _ in agent.arun_stream("go"):
                pass

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(grace_cancelled.wait(), 1)
        assert not task.done()
        assert agent.run_state.active
        assert not finished.is_set()
        with pytest.raises(RuntimeError, match="already running"):
            await agent.arun("overlap")
        release.set()
        await task
        assert finished.is_set()
        assert not agent.run_state.active
        assert await agent.arun("continue") == "recovered"
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_finished_cleanup_does_not_leave_a_timer_cancelling_the_next_run():
    class Model(ScriptedModel):
        async def astream(self, *args, **kwargs):
            try:
                await asyncio.Event().wait()
                yield Chunk(message="unreachable")
            finally:
                await asyncio.sleep(0)

        async def ainvoke(self, *args, **kwargs):
            await asyncio.sleep(0.35)
            return self.invoke(*args, **kwargs)

    async def run():
        caller = asyncio.current_task()
        caller.cancel("earlier cancellation already handled by caller")
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass
        prior_count = getattr(caller, "cancelling", lambda: 0)()
        agent = Agent(Model(AI("recovered")), run_timeout=0.01)
        with pytest.raises(AgentTimeoutError):
            async for _ in agent.arun_stream("go"):
                pass
        assert getattr(caller, "cancelling", lambda: 0)() == prior_count
        agent.run_timeout = 1
        assert await agent.arun("continue") == "recovered"
        assert getattr(caller, "cancelling", lambda: 0)() == prior_count

    await asyncio.wait_for(asyncio.create_task(run()), 2)


@pytest.mark.parametrize("original", ["provider", "close", "none"])
def test_sync_stream_cleanup_preserves_primary_failure_and_releases_agent(original):
    primary = LLMError("primary provider failure")
    secondary = OSError("secondary cleanup failure")

    class FailingStream:
        def __iter__(self):
            return self

        def __next__(self):
            if original == "provider":
                raise primary
            if original == "close":
                return Chunk(message="partial")
            raise StopIteration

        def close(self):
            raise secondary

    class Model(ScriptedModel):
        def stream(self, *args, **kwargs):
            return FailingStream()

    agent = Agent(Model(AI("recovered")))
    events = []
    agent.subscribe(events.append)
    stream = agent.run_stream("go")
    if original == "provider":
        with pytest.raises(LLMError) as raised:
            list(stream)
        assert raised.value is primary
        assert events[-1].error == "LLMError: primary provider failure"
    elif original == "close":
        assert next(stream).message == "partial"
        stream.close()
        assert events[-1].status == "closed"
    else:
        with pytest.raises(OSError) as raised:
            list(stream)
        assert raised.value is secondary
        assert events[-1].error == "OSError: secondary cleanup failure"
    assert not agent.run_state.active
    assert agent.run("continue") == "recovered"
