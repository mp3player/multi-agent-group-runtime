"""Workspace tool specifications and side-effect metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tools.builtin import terminal
from tools.decorator import ToolFunction
from tools.file_ops import list_dir, read_file, str_replace, write_file


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
    """A decorated workspace tool and its permission classification."""

    tool: ToolFunction
    side_effect: ToolSideEffect

    @property
    def name(self) -> str:
        return self.tool.name

    @property
    def handler_module(self) -> str:
        """Expose the original handler location for compatibility."""
        return self.tool.func.__module__

    @property
    def handler_name(self) -> str:
        """Expose the original handler name for compatibility."""
        return self.tool.func.__name__


_WORKSPACE_TOOLS: tuple[WorkspaceToolSpec, ...] = (
    WorkspaceToolSpec(terminal, "external_effect"),
    WorkspaceToolSpec(read_file, "read_only"),
    WorkspaceToolSpec(write_file, "workspace_mutating"),
    WorkspaceToolSpec(str_replace, "workspace_mutating"),
    WorkspaceToolSpec(list_dir, "read_only"),
)

WORKSPACE_TOOL_SPECS: dict[str, WorkspaceToolSpec] = {
    spec.name: spec for spec in _WORKSPACE_TOOLS
}
DEFAULT_WORKSPACE_TOOL_ORDER: tuple[str, ...] = tuple(
    spec.name for spec in _WORKSPACE_TOOLS
)


def workspace_tool_names() -> set[str]:
    """Return target workspace tool names."""
    return set(WORKSPACE_TOOL_SPECS)


def workspace_tool_specs() -> tuple[WorkspaceToolSpec, ...]:
    """Return workspace tool specs in canonical registration order."""
    return _WORKSPACE_TOOLS


def workspace_tool_spec(name: str) -> WorkspaceToolSpec | None:
    """Return one workspace tool spec by name."""
    return WORKSPACE_TOOL_SPECS.get(name)


def workspace_tools_by_effect(side_effect: ToolSideEffect) -> tuple[WorkspaceToolSpec, ...]:
    """Return workspace tool specs with a matching side-effect class."""
    return tuple(
        spec for spec in workspace_tool_specs()
        if spec.side_effect == side_effect
    )
