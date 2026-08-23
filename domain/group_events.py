"""Internal group chat event log."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Literal

from domain.group import GroupMessage


GroupEventKind = Literal[
    "message",
    "memory_update",
    "tool_call",
    "synthetic_pass",
    "dispatch_error",
    "dispatch_limit",
]


@dataclass(slots=True)
class GroupEvent:
    id: int
    kind: GroupEventKind
    actor: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "actor": self.actor,
            "data": dict(self.data),
        }


class GroupEventLog:
    """In-memory event log for debugging and future persistence hooks."""

    def __init__(self, sink: "GroupEventJsonlSink | None" = None) -> None:
        self.events: list[GroupEvent] = []
        self._next_event_id = 1
        self.sink = sink

    def append(
        self,
        kind: GroupEventKind,
        *,
        actor: str,
        data: dict[str, Any] | None = None,
    ) -> GroupEvent:
        event = GroupEvent(
            id=self._next_event_id,
            kind=kind,
            actor=actor,
            data=data or {},
        )
        self._next_event_id += 1
        self.events.append(event)
        if self.sink is not None:
            try:
                self.sink.write(event)
            except OSError:
                pass
        return event

    def record_message(self, msg: GroupMessage) -> GroupEvent:
        return self.append(
            "message",
            actor=msg.sender,
            data={
                "message_id": msg.id,
                "kind": msg.kind,
                "propagate": msg.propagate,
                "dispatch_mode": msg.dispatch_mode,
                "mentions": list(msg.mentions or []),
            },
        )

    def recent(self, limit: int | None = None) -> list[GroupEvent]:
        """Return recent events as a list copy."""
        if limit is None or limit <= 0:
            return list(self.events)
        return list(self.events[-limit:])

    def to_dicts(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return recent events as serializable dictionaries."""
        return [
            event.to_dict()
            for event in self.recent(limit)
        ]

    def clear(self) -> None:
        self.events.clear()
        self._next_event_id = 1

    def set_sink(self, sink: "GroupEventJsonlSink | None") -> None:
        self.sink = sink


@dataclass(frozen=True, slots=True)
class GroupEventJsonlSink:
    """Append group events to a local JSONL file."""

    path: Path

    def write(self, event: GroupEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")


def group_event_sink_from_path(path: str | Path | None) -> GroupEventJsonlSink | None:
    """Build a group event JSONL sink from config, or return None."""
    if path is None:
        return None
    text = str(path).strip()
    if not text:
        return None
    return GroupEventJsonlSink(Path(text))
