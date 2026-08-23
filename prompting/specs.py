"""Prompt module declarations for the target MAS architecture."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


PromptScope = Literal["common", "group", "member", "policy", "tools", "memory"]


@dataclass(frozen=True, slots=True)
class PromptModuleSpec:
    """Static prompt module declaration."""

    name: str
    scope: PromptScope
    template_path: str
    required_variables: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DispatchPolicyPromptHint:
    """Model-facing hint for one dispatch policy."""

    policy_name: str
    text: str


@dataclass(frozen=True, slots=True)
class GroupToolPromptEntry:
    """Model-facing group tool usage entry."""

    name: str
    purpose: str


COMMON_PROMPT_MODULES: tuple[PromptModuleSpec, ...] = (
    PromptModuleSpec("base", "common", "prompts/base.md"),
    PromptModuleSpec("workflow", "common", "prompts/workflow.md"),
    PromptModuleSpec("tool_use", "common", "prompts/tool_use.md"),
    PromptModuleSpec("skills", "common", "prompts/skills.md"),
    PromptModuleSpec("communication", "common", "prompts/communication.md"),
)

GROUP_PROMPT_MODULES: tuple[PromptModuleSpec, ...] = (
    PromptModuleSpec("group_basics", "group", "prompts/group/basics.md"),
    PromptModuleSpec("group_collaboration", "group", "prompts/group/collaboration.md"),
    PromptModuleSpec("group_tools", "tools", "prompts/group/tools.md"),
    PromptModuleSpec("group_memory", "memory", "prompts/group/memory.md"),
    PromptModuleSpec(
        "dispatch_policy_hint",
        "policy",
        "prompts/group/dispatch_policy.md",
        required_variables=("dispatch_policy",),
    ),
    PromptModuleSpec(
        "member_identity",
        "member",
        "prompts/group/member_identity.md",
        required_variables=("group_name", "member_name", "member_description"),
    ),
)

DISPATCH_POLICY_HINTS: dict[str, DispatchPolicyPromptHint] = {
    "default": DispatchPolicyPromptHint(
        "default",
        "Unread propagating messages may wake eligible members unless they pass.",
    ),
    "on_demand": DispatchPolicyPromptHint(
        "on_demand",
        "After the first responder, use explicit broadcast or directed tools to wake others.",
    ),
    "broadcast_feedback": DispatchPolicyPromptHint(
        "broadcast_feedback",
        "Broadcast responses should report back through feedback tools for coordinator summary.",
    ),
}

GROUP_TOOL_PROMPT_ENTRIES: tuple[GroupToolPromptEntry, ...] = (
    GroupToolPromptEntry("group_status", "Inspect group members and recent state."),
    GroupToolPromptEntry("group_memory_get", "Read complete shared group memory."),
    GroupToolPromptEntry("group_memory_update", "Replace one shared memory section."),
    GroupToolPromptEntry("group_memory_append", "Append to one shared memory section."),
    GroupToolPromptEntry("group_memory_clear", "Clear one or all shared memory sections."),
    GroupToolPromptEntry("group_send", "Send a normal propagating group message."),
    GroupToolPromptEntry("group_broadcast", "Explicitly ask all members to consider responding."),
    GroupToolPromptEntry("group_direct", "Send a directed message to exact member names."),
    GroupToolPromptEntry("group_handoff", "Transfer work to exact member names with context."),
    GroupToolPromptEntry("group_note_scope", "Record current scope and notify the group."),
    GroupToolPromptEntry("group_done", "Record completed work and send feedback."),
    GroupToolPromptEntry("group_report", "Submit role-specific findings or report."),
    GroupToolPromptEntry("group_decision", "Record a decision and notify the group."),
    GroupToolPromptEntry("group_pass", "Record no material contribution without propagation."),
)


def common_module_names() -> set[str]:
    """Return common prompt module names."""
    return {module.name for module in COMMON_PROMPT_MODULES}


def group_module_names() -> set[str]:
    """Return group-only prompt module names."""
    return {module.name for module in GROUP_PROMPT_MODULES}


def group_tool_prompt_names() -> set[str]:
    """Return group tools covered by the target prompt registry."""
    return {entry.name for entry in GROUP_TOOL_PROMPT_ENTRIES}
