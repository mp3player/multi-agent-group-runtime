"""In-memory audit log for tool execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools.permissions import ToolPermission, ToolPermissionDecision


@dataclass(frozen=True, slots=True)
class ToolAuditRecord:
    """One tool execution audit record."""

    id: int
    tool: str
    caller: str
    side_effect: str
    arguments: str
    result: str
    error: bool
    timestamp: str
    permission: ToolPermission
    decision: ToolPermissionDecision = ToolPermissionDecision()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "caller": self.caller,
            "side_effect": self.side_effect,
            "arguments": self.arguments,
            "result": self.result,
            "error": self.error,
            "timestamp": self.timestamp,
            "permission": self.permission.to_dict(),
            "decision": {
                "allowed": self.decision.allowed,
                "requires_approval": self.decision.requires_approval,
                "reason": self.decision.reason,
                "mode": self.decision.mode,
            },
        }


@dataclass(slots=True)
class ToolAuditLog:
    """Append-only in-memory audit log."""

    records_list: list[ToolAuditRecord] = field(default_factory=list)
    next_id: int = 1
    sink: "ToolAuditJsonlSink | None" = None

    def append(
        self,
        *,
        tool: str,
        caller: str,
        permission: ToolPermission,
        decision: ToolPermissionDecision | None = None,
        arguments: object,
        result: str,
        error: bool,
    ) -> ToolAuditRecord:
        decision = decision or ToolPermissionDecision()
        record = ToolAuditRecord(
            id=self.next_id,
            tool=tool,
            caller=caller,
            side_effect=permission.side_effect,
            arguments=summarize_arguments(arguments),
            result=summarize_result(result),
            error=error,
            timestamp=datetime.now(timezone.utc).isoformat(),
            permission=permission,
            decision=decision,
        )
        self.records_list.append(record)
        self.next_id += 1
        if self.sink is not None:
            try:
                self.sink.write(record)
            except OSError:
                pass
        return record

    def records(self, limit: int | None = None) -> list[ToolAuditRecord]:
        if limit is None or limit <= 0:
            return list(self.records_list)
        return list(self.records_list[-limit:])

    def clear(self) -> None:
        self.records_list.clear()
        self.next_id = 1

    def set_sink(self, sink: "ToolAuditJsonlSink | None") -> None:
        self.sink = sink


@dataclass(frozen=True, slots=True)
class ToolAuditJsonlSink:
    """Append tool audit records to a local JSONL file."""

    path: Path

    def write(self, record: ToolAuditRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


def audit_sink_from_path(path: str | Path | None) -> ToolAuditJsonlSink | None:
    """Build an audit sink from config, or return None when disabled."""
    if path is None:
        return None
    text = str(path).strip()
    if not text:
        return None
    return ToolAuditJsonlSink(Path(text))


def summarize_arguments(arguments: object, *, limit: int = 240) -> str:
    text = repr(arguments)
    if len(text) > limit:
        return text[: max(0, limit - 3)] + "..."
    return text


def summarize_result(result: str, *, limit: int = 240) -> str:
    if len(result) > limit:
        return result[: max(0, limit - 3)] + "..."
    return result


__all__ = [
    "audit_sink_from_path",
    "ToolAuditJsonlSink",
    "ToolAuditLog",
    "ToolAuditRecord",
]
