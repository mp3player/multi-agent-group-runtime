"""One ReAct policy for sync, async, streaming and non-streaming execution."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from models import AI, Chunk, Message, ToolCall, User
from core.agent_runtime.context import validate_tool_pairs
from core.agent_runtime.events import AgentEvent, EventType, RunStatus
from core.agent_runtime.ports import AgentRuntimePort
from core.agent_runtime.context_management.policy import CompactionRequest, ContextIORequest
from core.llm_runtime.errors import ContextWindowExceeded

FALLBACK_MESSAGE = "(Maximum turns reached without a final response)"


@dataclass(frozen=True, slots=True)
class ModelRequest:
    messages: list[Message]
    tools: list[dict[str, Any]] | None
    max_tokens: int
    temperature: float
    input_tokens: int = 0

    def options(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "tools": self.tools,
            "tool_choice": "auto" if self.tools else None,
        }


@dataclass(frozen=True, slots=True)
class ToolRequest:
    call: ToolCall


Step = AgentEvent | ModelRequest | ToolRequest | CompactionRequest | ContextIORequest
TurnSteps = Generator[Step, Any, None]


@dataclass
class AgentTurnMachine:
    runtime: AgentRuntimePort
    deadline: float | None
    run_id: str = field(default_factory=lambda: uuid4().hex)
    turn: int = 0
    status: RunStatus = "closed"
    result: str = ""
    error: str | None = None
    pending_calls: list[ToolCall] = field(default_factory=list)

    def event(self, event_type: EventType, **kwargs: Any) -> AgentEvent:
        return AgentEvent(event_type, self.run_id, self.turn, **kwargs)

    def end_event(self) -> AgentEvent:
        return self.event("run_end", status=self.status, result=self.result, error=self.error)

    def steps(self, message: str | None, *, stream: bool, compact: bool = False, focus: str = '', prepare_only: bool = False) -> TurnSteps:
        yield self.event("run_start")
        self.runtime.context_manager.begin_run()
        if compact or prepare_only:
            yield from self.runtime.context_manager.prepare(self, force=compact, focus=focus)
            self.result = (self.runtime.session.checkpoint or {}).get('summary', '')
            self.status = 'completed'
            return
        if message is not None:
            self.runtime.add_session_message(User(message))
        for self.turn in range(1, self.runtime.max_turns + 1):
            self.runtime.check_deadline(self.deadline)
            yield self.event("turn_start")
            prepared = yield from self.runtime.context_manager.prepare(self)
            try:
                response = yield ModelRequest(prepared.messages, prepared.tools, self.runtime.max_tokens,
                                              self.runtime.temperature, prepared.input_tokens)
            except ContextWindowExceeded:
                prepared = yield from self.runtime.context_manager.prepare(self, overflow=True)
                # A second failure escapes: only the failed inference is retried.
                response = yield ModelRequest(prepared.messages, prepared.tools, self.runtime.max_tokens,
                                              self.runtime.temperature, prepared.input_tokens)
            if not isinstance(response, AI):
                raise TypeError("Model requests must return an AI message")
            # Reject malformed provider batches before committing an unusable
            # transcript or executing effects. Results are not expected yet.
            validate_tool_pairs([response], allow_pending=True)
            self.runtime.add_session_message(response)
            self.pending_calls = list(response.tool_calls or [])
            yield self.event("message_end", message=response)
            calls = response.tool_calls or []
            if not calls:
                self.result = response.message
                self.status = "completed"
                yield from self.runtime.context_manager.maintain_history(self)
                yield self.event("turn_end")
                return

            results = []
            for call in calls:
                yield self.event("tool_start", tool_call=call)
                result = yield ToolRequest(call)
                if not isinstance(result, Message):
                    raise TypeError("Tool requests must return a Message")
                # Record completed side effects even if their execution used
                # the remaining deadline.
                self.runtime.add_session_message(result)
                results.append(result)
                self.pending_calls.pop(0)
                yield self.event("tool_end", tool_call=call, message=result)
                yield self.event("message_end", message=result)
                self.runtime.check_deadline(self.deadline)

            stop = self.runtime.should_stop_after_tool_calls(results)
            yield self.event("turn_end")
            if stop:
                self.status = "tool_stop"
                yield from self.runtime.context_manager.maintain_history(self)
                return

        self.status = "max_turns"
        fallback = AI(FALLBACK_MESSAGE)
        self.runtime.add_session_message(fallback)
        self.result = fallback.message
        if stream:
            yield self.event("message_delta", chunk=Chunk(message=fallback.message))
        yield self.event("message_end", message=fallback)
        yield from self.runtime.context_manager.maintain_history(self)

    def interrupt_pending_calls(self) -> list[AgentEvent]:
        """Balance committed tool requests without replaying unknown side effects."""
        events = []
        for call in self.pending_calls:
            result = Message(
                role="tool",
                message=(
                    f"[ToolCallInterrupted] {call.name}: run {self.status}; "
                    "no result was recorded. Check external state before retrying."
                ),
            )
            result.tool_call_id = call.id
            self.runtime.add_session_message(result)
            events.append(self.event("message_end", message=result))
        self.pending_calls.clear()
        return events


def advance(steps: TurnSteps, response: Any = None, *, error: Exception | None = None) -> Step | None:
    """Finish only when the machine ends, not on an I/O iterator exception."""
    try:
        return steps.throw(error) if error is not None else steps.send(response)
    except StopIteration:
        return None
