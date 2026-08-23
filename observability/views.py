"""Stable read-only views for runtime diagnostics."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _message_to_dict(msg: Any) -> dict[str, Any]:
    return {
        "id": msg.id,
        "sender": msg.sender,
        "content": msg.content,
        "kind": msg.kind,
        "round_index": msg.round_index,
        "mentions": list(msg.mentions or []),
        "propagate": msg.propagate,
        "dispatch_mode": msg.dispatch_mode,
    }


def group_message_view(msg: Any) -> dict[str, Any]:
    """Return one serializable group message view."""
    return _message_to_dict(msg)


def transcript_view(messages: list[Any], *, limit: int | None = None) -> list[dict[str, Any]]:
    """Return a serializable transcript view."""
    selected = messages if limit is None or limit <= 0 else messages[-limit:]
    return [_message_to_dict(msg) for msg in selected]


def group_member_view(group: Any, member: Any) -> dict[str, Any]:
    """Return one serializable group member view."""
    snapshot = member_status_view(group.member_status_snapshot(member))
    return {
        **snapshot,
        "tools": member.agent.registry.names(),
        "model": member.agent.llm.model,
        "url": member.agent.llm.base_url,
    }


def member_status_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Normalize one member status snapshot."""
    return {
        "name": snapshot.get("name", ""),
        "description": snapshot.get("description", ""),
        "enabled": bool(snapshot.get("enabled", False)),
        "status": snapshot.get("status", ""),
        "unread": int(snapshot.get("unread", 0)),
        "dispatchable": int(snapshot.get("dispatchable", 0)),
    }


def group_events_view(event_log: Any, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Return serializable group event dictionaries."""
    if hasattr(event_log, "to_dicts"):
        return list(event_log.to_dicts(limit))
    events = event_log if limit is None or limit <= 0 else event_log[-limit:]
    return [
        {
            "id": event.id,
            "kind": event.kind,
            "actor": event.actor,
            "data": dict(event.data),
        }
        for event in events
    ]


def group_stats_view(stats: Any) -> dict[str, Any]:
    """Return a plain dict view of group scheduling stats."""
    return {
        "propagating_messages": stats.propagating_messages,
        "pass_messages": stats.pass_messages,
        "synthetic_passes": stats.synthetic_passes,
        "stale_responses": stats.stale_responses,
        "dispatch_limit_hits": stats.dispatch_limit_hits,
        "member_turns": dict(stats.member_turns),
        "user_dispatch_steps": list(stats.user_dispatch_steps),
    }


def group_debug_snapshot_view(
    *,
    group: Any,
    app_config: Any,
    member_payloads: list[dict[str, Any]],
    audit_records: list[Any],
    usage_monitor: Any,
    limit: int = 10,
) -> dict[str, Any]:
    """Return a read-only debug snapshot for a group service."""
    return {
        "group": {
            "name": group.name,
            "dispatch_policy": group.dispatch_policy.name,
            "max_dispatch_rounds": group.max_dispatch_rounds,
            "run_timeout": group.run_timeout,
        },
        "config": group_config_view(app_config),
        "members": member_payloads,
        "memory": group.memory.render_summary(),
        "stats": group_stats_view(group.stats),
        "events": group_events_view(group.events, limit=limit),
        "audit": tool_audit_view(audit_records, limit=limit),
        "usage": usage_payload_view(usage_monitor),
    }


def group_config_view(app_config: Any) -> dict[str, Any]:
    """Return runtime config values useful for CLI diagnostics."""
    return {
        "llm": {
            "model": app_config.llm.model,
            "base_url": app_config.llm.base_url,
            "timeout": app_config.llm.timeout,
        },
        "group": {
            "name": app_config.group.name,
            "dispatch_policy": app_config.group.dispatch_policy,
            "max_dispatch_rounds": app_config.group.max_dispatch_rounds,
            "run_timeout": app_config.group.run_timeout,
            "events_jsonl": app_config.group.events_jsonl,
        },
        "tools": {
            "enable_workspace_tools": app_config.tools.enable_workspace_tools,
            "workspace_roots": app_config.tools.workspace_roots,
            "permission_dry_run": app_config.tools.permission_dry_run,
            "permission_enforce": app_config.tools.permission_enforce,
            "permission_rules": app_config.tools.permission_rules,
            "audit_jsonl": app_config.tools.audit_jsonl,
        },
        "usage": {
            "enabled": app_config.usage.enabled,
        },
        "skills": {
            "skills_dir": app_config.skills.skills_dir,
        },
    }


def group_config_report_lines(config: dict[str, Any], *, member_config_path: Any = None) -> list[str]:
    """Return CLI lines for group runtime configuration."""
    llm = config["llm"]
    group = config["group"]
    tools = config["tools"]
    usage = config["usage"]
    skills = config["skills"]
    return [
        "Runtime config:",
        f"- model={llm['model'] or '-'} url={llm['base_url'] or '-'} timeout={llm['timeout']}",
        (
            f"- group={group['name']} policy={group['dispatch_policy']} "
            f"max_rounds={group['max_dispatch_rounds']} run_timeout={group['run_timeout']}"
        ),
        f"- member_config={member_config_path or '-'}",
        (
            f"- tools_enabled={tools['enable_workspace_tools']} "
            f"workspace_roots={tools['workspace_roots'] or '-'}"
        ),
        (
            f"- dry_run={tools['permission_dry_run']} "
            f"enforce={tools['permission_enforce']} "
            f"rules={tools['permission_rules'] or '-'} usage_enabled={usage['enabled']}"
        ),
        f"- tool_audit_jsonl={tools['audit_jsonl'] or '-'}",
        f"- group_events_jsonl={group['events_jsonl'] or '-'}",
        f"- skills_dir={skills['skills_dir'] or '-'}",
    ]


def group_debug_report_lines(snapshot: dict[str, Any], *, limit: int = 10) -> list[str]:
    """Return concise CLI lines for a group debug snapshot."""
    group = snapshot["group"]
    stats = snapshot["stats"]
    lines = [
        "Debug snapshot:",
        (
            f"- group={group['name']} policy={group['dispatch_policy']} "
            f"max_rounds={group['max_dispatch_rounds']} run_timeout={group['run_timeout']}"
        ),
        (
            f"- stats propagating={stats['propagating_messages']} pass={stats['pass_messages']} "
            f"synthetic_pass={stats['synthetic_passes']} stale={stats['stale_responses']} "
            f"limit_hits={stats['dispatch_limit_hits']}"
        ),
        f"- memory={snapshot['memory']}",
        "- members:",
    ]
    for member in snapshot["members"]:
        lines.append(
            f"  - {member['name']}: status={member['status']} enabled={member['enabled']} "
            f"unread={member['unread']} dispatchable={member['dispatchable']}"
        )
    lines.append(f"- recent_events ({min(limit, len(snapshot['events']))}):")
    for event in snapshot["events"]:
        lines.append(
            f"  - #{event['id']} {event['kind']} actor={event['actor']} data={event['data']}"
        )
    lines.append(f"- recent_audit ({min(limit, len(snapshot['audit']))}):")
    if snapshot["audit"]:
        lines.extend(f"  {line}" for line in tool_audit_report_lines(snapshot["audit"], limit=limit)[1:])
    else:
        lines.append("  (none)")
    return lines


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
        return ["(暂无 usage 记录)"]
    lines = ["最近调用:"]
    start = max(1, len(records) - recent_limit + 1)
    for index, record in enumerate(records[-recent_limit:], start=start):
        lines.append(usage_record_line(f"#{index} {record.label}", record))
    lines.append("汇总:")
    for label, record in monitor.summary().items():
        lines.append(usage_record_line(label, record))
    return lines


def usage_payload_view(monitor: Any, *, recent_limit: int = 50) -> dict[str, Any]:
    """Return the Web API usage payload shape."""
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
    """Return a plain dict view of one tool audit record."""
    if isinstance(record, dict):
        return record
    if hasattr(record, "to_dict"):
        return record.to_dict()
    return {
        "id": record.id,
        "tool": record.tool,
        "caller": record.caller,
        "side_effect": record.side_effect,
        "arguments": record.arguments,
        "result": record.result,
        "error": record.error,
        "timestamp": record.timestamp,
        "permission": (
            record.permission.to_dict()
            if hasattr(record.permission, "to_dict") else record.permission
        ),
        "decision": (
            {
                "allowed": record.decision.allowed,
                "requires_approval": record.decision.requires_approval,
                "reason": record.decision.reason,
                "mode": record.decision.mode,
            }
            if hasattr(record, "decision") else {
                "allowed": True,
                "requires_approval": False,
                "reason": "",
                "mode": "allow",
            }
        ),
    }


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
        return ["(暂无 tool audit 记录)"]
    return ["最近 tool audit:"] + [
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
