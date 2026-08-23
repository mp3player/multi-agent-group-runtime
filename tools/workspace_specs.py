"""Workspace tool specifications and side-effect metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ToolSideEffect = Literal[
    "read_only",
    "memory_only",
    "propagating",
    "non_propagating",
    "workspace_mutating",
    "external_effect",
]


@dataclass(frozen=True, slots=True)
class WorkspaceToolSpec:
    """Target declaration for one workspace tool."""

    name: str
    side_effect: ToolSideEffect
    handler_module: str
    handler_name: str


WORKSPACE_TOOL_SPECS: dict[str, WorkspaceToolSpec] = {
    "terminal": WorkspaceToolSpec(
        name="terminal",
        side_effect="external_effect",
        handler_module="tools.builtin",
        handler_name="terminal",
    ),
    "read_file": WorkspaceToolSpec(
        name="read_file",
        side_effect="read_only",
        handler_module="tools.file_ops",
        handler_name="read_file",
    ),
    "list_dir": WorkspaceToolSpec(
        name="list_dir",
        side_effect="read_only",
        handler_module="tools.file_ops",
        handler_name="list_dir",
    ),
    "write_file": WorkspaceToolSpec(
        name="write_file",
        side_effect="workspace_mutating",
        handler_module="tools.file_ops",
        handler_name="write_file",
    ),
    "str_replace": WorkspaceToolSpec(
        name="str_replace",
        side_effect="workspace_mutating",
        handler_module="tools.file_ops",
        handler_name="str_replace",
    ),
}

DEFAULT_WORKSPACE_TOOL_ORDER: tuple[str, ...] = (
    "terminal",
    "read_file",
    "write_file",
    "str_replace",
    "list_dir",
)


def workspace_tool_names() -> set[str]:
    """Return target workspace tool names."""
    return set(WORKSPACE_TOOL_SPECS)


def workspace_tool_specs() -> tuple[WorkspaceToolSpec, ...]:
    """Return workspace tool specs in canonical registration order."""
    return tuple(WORKSPACE_TOOL_SPECS[name] for name in DEFAULT_WORKSPACE_TOOL_ORDER)


def workspace_tool_spec(name: str) -> WorkspaceToolSpec | None:
    """Return one workspace tool spec by name."""
    return WORKSPACE_TOOL_SPECS.get(name)


def workspace_tools_by_effect(side_effect: ToolSideEffect) -> tuple[WorkspaceToolSpec, ...]:
    """Return workspace tool specs with a matching side-effect class."""
    return tuple(
        spec for spec in workspace_tool_specs()
        if spec.side_effect == side_effect
    )
