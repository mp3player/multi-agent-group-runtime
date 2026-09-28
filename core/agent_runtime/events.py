"""Observational events for single-agent execution."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import logging
from typing import Literal

from models import Chunk, Message, ToolCall

EventType = Literal[
    "run_start", "turn_start", "message_delta", "message_end",
    "tool_start", "tool_end", "turn_end", "run_end",
    "context_compaction_start", "context_compaction_end", "context_compaction_failed",
]
RunStatus = Literal[
    "completed", "tool_stop", "max_turns", "error", "timeout", "cancelled", "closed",
]


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """A detached execution snapshot; only run_end has a terminal status."""

    type: EventType
    run_id: str
    turn: int = 0
    message: Message | None = None
    chunk: Chunk | None = None
    tool_call: ToolCall | None = None
    status: RunStatus | None = None
    result: str | None = None
    error: str | None = None
    context: dict | None = None


EventListener = Callable[[AgentEvent], None]


class AgentEventBus:
    """Synchronous observers cannot corrupt events or fail the execution loop."""

    def __init__(self) -> None:
        self._listeners: dict[object, EventListener] = {}

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        token = object()
        self._listeners[token] = listener

        def unsubscribe() -> None:
            self._listeners.pop(token, None)

        return unsubscribe

    def publish(self, event: AgentEvent) -> None:
        for listener in tuple(self._listeners.values()):
            try:
                listener(deepcopy(event))
            except Exception:
                logging.getLogger(__name__).exception("Agent event observer failed")
