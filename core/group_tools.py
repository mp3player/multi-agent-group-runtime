"""Group-scoped tool registration."""

from __future__ import annotations

from domain.group import GroupMember
from core.group_runtime.ports import GroupToolRuntimePort
from core.group_runtime.runtime_adapter import GroupToolRuntimeAdapter
from core.group_tools_runtime import GroupToolHandlers
from core.group_tool_specs import (
    GROUP_TOOL_EFFECTS,
    GROUP_TOOL_NAMES,
    GROUP_TOOL_ORDER,
    GroupToolEffect,
    tool_effect,
)
from tools.decorator import ToolFunction


def attach_group_tools(group: object, member: GroupMember) -> None:
    """Register group-scoped tools on a member's own registry."""
    registry = member.agent.registry
    runtime = GroupToolRuntimeAdapter(group)
    for tool_func in make_group_tools(runtime, member):
        if registry.has(tool_func.name):
            registry.unregister(tool_func.name)
        registry.register(tool_func, permission=GROUP_TOOL_EFFECTS[tool_func.name])


def detach_group_tools(member: GroupMember) -> None:
    """Unregister group-scoped tools from a removed member."""
    registry = member.agent.registry
    for name in GROUP_TOOL_NAMES:
        if registry.has(name):
            registry.unregister(name)


def make_group_tools(
    group: GroupToolRuntimePort,
    member: GroupMember,
) -> list[ToolFunction]:
    """Build group-scoped tool functions for one member."""
    handlers = GroupToolHandlers(group, member)
    return [
        ToolFunction(getattr(handlers, name))
        for name in GROUP_TOOL_ORDER
    ]


__all__ = [
    "GROUP_TOOL_EFFECTS",
    "GROUP_TOOL_NAMES",
    "GROUP_TOOL_ORDER",
    "GroupToolEffect",
    "attach_group_tools",
    "detach_group_tools",
    "make_group_tools",
    "tool_effect",
]
