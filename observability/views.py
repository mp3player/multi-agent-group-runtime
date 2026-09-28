"""Read-only usage, tool audit and log views."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def usage_record_view(record: Any) -> dict[str, Any]:
    """Return a plain dict view of one usage record."""
    return {
        "label": record.label,
        "model": record.model,
        "prompt_tokens": record.prompt_tokens,
        "completion_tokens": record.completion_tokens,
        "total_tokens": record.total_tokens,
        "reasoning_tokens": record.reasoning_tokens,
        "cached_tokens": record.cached_tokens,
        "cache_hit_tokens": record.cache_hit_tokens,
        "cache_miss_tokens": record.cache_miss_tokens,
    }


def usage_summary_view(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return a plain dict view of usage summary records keyed by label."""
    return {
        label: usage_record_view(record)
        for label, record in sorted(summary.items())
    }


def usage_record_line(label: str, record: Any) -> str:
    """Return one CLI usage report line."""
    data = usage_record_view(record)
    return (
        f"- {label}: prompt={data['prompt_tokens']}, "
        f"cached={data['cached_tokens']}, hit={data['cache_hit_tokens']}, "
        f"miss={data['cache_miss_tokens']}, completion={data['completion_tokens']}, "
        f"reasoning={data['reasoning_tokens']}, total={data['total_tokens']}"
    )


def usage_report_lines(monitor: Any, *, recent_limit: int = 20) -> list[str]:
    """Return CLI usage report lines for a usage monitor."""
    records = monitor.records()
    if not records:
        return ["(No usage records yet)"]
    lines = ["Recent calls:"]
    start = max(1, len(records) - recent_limit + 1)
    for index, record in enumerate(records[-recent_limit:], start=start):
        lines.append(usage_record_line(f"#{index} {record.label}", record))
    lines.append("Summary:")
    for label, record in monitor.summary().items():
        lines.append(usage_record_line(label, record))
    return lines


def usage_payload_view(monitor: Any, *, recent_limit: int = 50) -> dict[str, Any]:
    """Return a serializable usage payload."""
    records = monitor.records()
    summary = monitor.summary()
    return {
        "records": [
            usage_record_view(record) for record in records[-recent_limit:]
        ],
        "summary": [
            usage_record_view(record) for record in summary.values()
        ],
    }


def tool_audit_record_view(record: Any) -> dict[str, Any]:
    """Accept a serialized dict or a record with its own to_dict converter."""
    if isinstance(record, dict):
        return record
    return record.to_dict()


def tool_audit_view(audit_log: Any, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Return serializable tool audit records."""
    records = audit_log.records(limit) if hasattr(audit_log, "records") else audit_log
    if limit is not None and limit > 0 and not hasattr(audit_log, "records"):
        records = records[-limit:]
    return [tool_audit_record_view(record) for record in records]


def tool_audit_record_line(record: Any) -> str:
    """Return one CLI tool audit report line."""
    data = tool_audit_record_view(record)
    status = "error" if data["error"] else "ok"
    decision = data.get("decision", {}).get("mode", "allow")
    return (
        f"- #{data['id']} {data['tool']} caller={data['caller'] or '-'} "
        f"side_effect={data['side_effect']} decision={decision} status={status} "
        f"result={data['result']} timestamp={data['timestamp']}"
    )


def tool_audit_report_lines(records: Any, *, limit: int = 20) -> list[str]:
    """Return CLI report lines for tool audit records."""
    if hasattr(records, "records"):
        selected = records.records(limit)
    else:
        selected = list(records)
        if limit > 0:
            selected = selected[-limit:]
    if not selected:
        return ["(No tool audit records yet)"]
    return ["Recent tool audit records:"] + [
        tool_audit_record_line(record)
        for record in selected
    ]


def debug_log_metadata_view(path: str | Path) -> dict[str, Any]:
    """Return read-only metadata for one debug log path."""
    log_path = Path(path)
    return {
        "path": str(log_path),
        "exists": log_path.exists(),
        "size": log_path.stat().st_size if log_path.exists() else 0,
    }
