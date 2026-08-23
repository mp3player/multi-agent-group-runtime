"""Group tool declarations and side-effect classification."""

from __future__ import annotations

from typing import Literal

GroupToolEffect = Literal[
    "read_only",
    "memory_only",
    "propagating",
    "non_propagating",
]

GROUP_TOOL_EFFECTS: dict[str, GroupToolEffect] = {
    "group_status": "read_only",
    "group_memory_get": "read_only",
    "group_memory_update": "memory_only",
    "group_memory_append": "memory_only",
    "group_memory_clear": "memory_only",
    "group_send": "propagating",
    "group_broadcast": "propagating",
    "group_direct": "propagating",
    "group_handoff": "propagating",
    "group_note_scope": "propagating",
    "group_done": "propagating",
    "group_report": "propagating",
    "group_decision": "propagating",
    "group_pass": "non_propagating",
}

GROUP_TOOL_ORDER = [
    "group_status",
    "group_send",
    "group_broadcast",
    "group_direct",
    "group_handoff",
    "group_pass",
    "group_memory_get",
    "group_memory_update",
    "group_memory_append",
    "group_memory_clear",
    "group_note_scope",
    "group_done",
    "group_report",
    "group_decision",
]

GROUP_TOOL_NAMES = set(GROUP_TOOL_EFFECTS)


def tool_effect(name: str) -> GroupToolEffect | None:
    """Return the registered group tool side-effect classification."""
    return GROUP_TOOL_EFFECTS.get(name)


def group_tool_names() -> set[str]:
    """Return declared group tool names."""
    return set(GROUP_TOOL_EFFECTS)


def group_tool_effects() -> dict[str, GroupToolEffect]:
    """Return a shallow copy of group tool side-effect metadata."""
    return dict(GROUP_TOOL_EFFECTS)


def group_tools_by_effect(side_effect: GroupToolEffect) -> tuple[str, ...]:
    """Return group tool names with a matching side-effect class in stable order."""
    return tuple(
        name for name in GROUP_TOOL_ORDER
        if GROUP_TOOL_EFFECTS[name] == side_effect
    )
