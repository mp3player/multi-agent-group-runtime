"""I/O drivers and public adapters for the shared Agent turn machine."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import aclosing, asynccontextmanager, closing, contextmanager
from copy import deepcopy
from dataclasses import dataclass
import logging
import time
from typing import TypeVar

from models import Chunk
from core.agent_runtime.events import AgentEvent
from core.agent_runtime.deadlines import DeadlineExceeded, await_before
from core.agent_runtime.ports import AgentRuntimePort
from core.agent_runtime.response_parser import StreamResponseAccumulator
from core.agent_runtime.run_state import AgentTimeoutError
from core.agent_runtime.context_management.policy import CompactionRequest, ContextIORequest
from core.agent_runtime.context_management.errors import CompactionError
from core.llm_runtime.errors import ContextWindowExceeded
from core.agent_runtime.turn_machine import (
    AgentTurnMachine,
    ModelRequest,
    ToolRequest,
    advance,
)

T = TypeVar("T")
STREAM_CLEANUP_TIMEOUT = 0.25


@dataclass(slots=True)
class AgentReactLoop:
    """Sync/async differ only in how model I/O is driven, never in turn policy."""

    runtime: AgentRuntimePort

    def run(self, message: str) -> str:
        result = ""
        with closing(self.run_events(message)) as events:
            for event in events:
                if event.type == "run_end":
                    result = event.result or ""
        return result

    def run_stream(self, message: str) -> Iterator[Chunk]:
        with closing(self.run_events(message, stream=True)) as events:
            for event in events:
                if event.chunk is not None:
                    yield event.chunk

    async def arun(self, message: str) -> str:
        result = ""
        async with aclosing(self.arun_events(message)) as events:
            async for event in events:
                if event.type == "run_end":
                    result = event.result or ""
        return result

    async def arun_stream(self, message: str) -> AsyncIterator[Chunk]:
        async with aclosing(self.arun_events(message, stream=True)) as events:
            async for event in events:
                if event.chunk is not None:
                    yield event.chunk

    def run_events(self, message: str | None, *, stream: bool = False,
                   compact: bool = False, focus: str = '', prepare_only: bool = False) -> Iterator[AgentEvent]:
        with self._execution() as machine, closing(machine.steps(message, stream=stream, compact=compact, focus=focus, prepare_only=prepare_only)) as steps:
            step = advance(steps)
            while step is not None:
                if isinstance(step, AgentEvent):
                    yield self._deliver(step)
                    if step.type == 'run_start':
                        self.runtime.run_state.check_stop()
                    step = advance(steps)
                elif isinstance(step, ModelRequest):
                    self.runtime.run_state.check_stop()
                    self.runtime.check_deadline(machine.deadline)
                    self.runtime.run_state.before_main_inference()
                    self.runtime.check_deadline(machine.deadline)
                    self.runtime.run_state.mark_main_inference()
                    visible = False
                    try:
                        if stream:
                            accumulator = StreamResponseAccumulator()
                            source = self.runtime.llm.stream(step.messages, **step.options())
                            with _close_sync_stream(source):
                                for chunk in source:
                                    self.runtime.check_deadline(machine.deadline)
                                    self.runtime.context_manager.observe_usage(getattr(chunk, 'usage', None),
                                                                              phase='main', estimate=step.input_tokens)
                                    if accumulator.add(chunk):
                                        visible = True
                                        yield self._deliver(machine.event("message_delta", chunk=chunk))
                            response = accumulator.response()
                        else:
                            data = self.runtime.llm.invoke(step.messages, **step.options())
                            self.runtime.context_manager.observe_usage(data.get('usage') if isinstance(data, dict) else None,
                                                                      phase='main', estimate=step.input_tokens)
                            response, _ = self.runtime.parse_invoke_response(data)
                    except ContextWindowExceeded as error:
                        if visible:
                            raise
                        step = advance(steps, error=error)
                        continue
                    self.runtime.check_deadline(machine.deadline)
                    step = advance(steps, response)
                elif isinstance(step, CompactionRequest):
                    try:
                        self.runtime.run_state.check_stop()
                        self.runtime.check_deadline(machine.deadline)
                        data = self.runtime.llm.invoke(step.messages, **step.options())
                        self.runtime.check_deadline(machine.deadline)
                        response = self._summary_response(data)
                    except Exception as error:
                        step = advance(steps, error=error)
                    else:
                        step = advance(steps, response)
                elif isinstance(step, ContextIORequest):
                    try:
                        if machine.status == 'closed':
                            self.runtime.run_state.check_stop()
                        self.runtime.check_deadline(machine.deadline)
                        result = step.operation()
                        self.runtime.check_deadline(machine.deadline)
                    except Exception as error:
                        step = advance(steps, error=error)
                    else:
                        step = advance(steps, result)
                elif isinstance(step, ToolRequest):
                    self.runtime.run_state.check_stop()
                    self.runtime.check_deadline(machine.deadline)
                    result = self.runtime.execute_tool_calls([step.call])[0]
                    step = advance(steps, result)
        # On failure/close, subscribers still receive run_end, while the caller
        # receives the original exception instead of a false successful result.
        yield deepcopy(machine.end_event())

    async def arun_events(self, message: str | None, *, stream: bool = False,
                         compact: bool = False, focus: str = '') -> AsyncIterator[AgentEvent]:
        with self._execution() as machine, closing(machine.steps(message, stream=stream, compact=compact, focus=focus)) as steps:
            step = advance(steps)
            while step is not None:
                if isinstance(step, AgentEvent):
                    yield self._deliver(step)
                    step = advance(steps)
                elif isinstance(step, ModelRequest):
                    self.runtime.check_deadline(machine.deadline)
                    visible = False
                    try:
                        if stream:
                            accumulator = StreamResponseAccumulator()
                            source = self.runtime.llm.astream(step.messages, **step.options())
                            async with _close_async_stream(source):
                                while True:
                                    try:
                                        chunk = await self._await_io(source.__anext__, machine.deadline)
                                    except StopAsyncIteration:
                                        break
                                    self.runtime.context_manager.observe_usage(getattr(chunk, 'usage', None),
                                                                              phase='main', estimate=step.input_tokens)
                                    if accumulator.add(chunk):
                                        visible = True
                                        yield self._deliver(machine.event("message_delta", chunk=chunk))
                            response = accumulator.response()
                        else:
                            data = await self._await_io(
                                lambda: self.runtime.llm.ainvoke(step.messages, **step.options()),
                                machine.deadline,
                            )
                            self.runtime.context_manager.observe_usage(data.get('usage') if isinstance(data, dict) else None,
                                                                      phase='main', estimate=step.input_tokens)
                            response, _ = self.runtime.parse_invoke_response(data)
                    except ContextWindowExceeded as error:
                        if visible:
                            raise
                        step = advance(steps, error=error)
                        continue
                    self.runtime.check_deadline(machine.deadline)
                    step = advance(steps, response)
                elif isinstance(step, CompactionRequest):
                    try:
                        data = await self._await_io(
                            lambda: self.runtime.llm.ainvoke(step.messages, **step.options()), machine.deadline)
                        self.runtime.check_deadline(machine.deadline)
                        response = self._summary_response(data)
                    except Exception as error:
                        step = advance(steps, error=error)
                    else:
                        step = advance(steps, response)
                elif isinstance(step, ContextIORequest):
                    operation = step.operation
                    try:
                        result = await self._await_io(lambda: asyncio.to_thread(operation), machine.deadline)
                    except Exception as error:
                        step = advance(steps, error=error)
                    else:
                        step = advance(steps, result)
                elif isinstance(step, ToolRequest):
                    # Synchronous handlers run serially on this thread;
                    # moving them to a worker would change effect ordering.
                    self.runtime.check_deadline(machine.deadline)
                    result = self.runtime.execute_tool_calls([step.call])[0]
                    step = advance(steps, result)
        yield deepcopy(machine.end_event())

    def _summary_response(self, data):
        self.runtime.context_manager.observe_usage(data.get('usage') if isinstance(data, dict) else None,
                                                  phase='summary')
        response, _ = self.runtime.parse_invoke_response(data)
        if data['choices'][0].get('finish_reason') not in ('stop', 'end_turn'):
            raise CompactionError('Summary response has no successful completion signal')
        return response

    @contextmanager
    def _execution(self) -> Iterator[AgentTurnMachine]:
        machine = AgentTurnMachine(self.runtime, None)
        self.runtime.enter_run()
        try:
            self.runtime.run_state.check_stop()
            machine.deadline = self.runtime.deadline()
            yield machine
        except GeneratorExit:
            machine.status = "closed"
            raise
        except (asyncio.CancelledError, KeyboardInterrupt):
            machine.status = "cancelled"
            raise
        except BaseException as error:
            machine.status = "timeout" if isinstance(error, AgentTimeoutError) else "error"
            machine.error = f"{type(error).__name__}: {error}"
            raise
        finally:
            try:
                for event in machine.interrupt_pending_calls():
                    self.runtime.publish(event)
                terminal = machine.end_event()
                self.runtime.run_state.capture_terminal(terminal)
                self.runtime.publish(terminal)
            finally:
                self.runtime.exit_run()

    def _deliver(self, event: AgentEvent) -> AgentEvent:
        self.runtime.publish(event)
        return deepcopy(event)

    async def _await_io(self, operation: Callable[[], Awaitable[T]], deadline: float | None) -> T:
        # Create the awaitable only after checking the deadline, avoiding leaked
        # coroutines when request preparation already exhausted the time budget.
        self.runtime.check_deadline(deadline)
        try:
            return await await_before(operation, deadline, cleanup_grace=STREAM_CLEANUP_TIMEOUT)
        except DeadlineExceeded:
            self.runtime.check_deadline(deadline)
            raise


@contextmanager
def _close_sync_stream(source: Iterator[Chunk]):
    interrupted = False
    try:
        yield source
    except BaseException:
        interrupted = True
        raise
    finally:
        close = getattr(source, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                if not interrupted:
                    raise
                logging.getLogger(__name__).exception("Agent stream cleanup failed")


@asynccontextmanager
async def _close_async_stream(source: AsyncIterator[Chunk]):
    interrupted = False
    try:
        yield source
    except BaseException:
        interrupted = True
        raise
    finally:
        close = getattr(source, "aclose", None)
        if close is not None:
            try:
                await await_before(close, time.monotonic() + STREAM_CLEANUP_TIMEOUT)
            except Exception:
                if not interrupted:
                    raise
                # Preserve the original timeout/cancellation/close and release
                # the guard even when cooperative provider cleanup is faulty.
                logging.getLogger(__name__).exception("Agent stream cleanup failed")
