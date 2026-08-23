"""Workspace tool registry binding."""

from __future__ import annotations

from tools.registry import ToolRegistry
from tools.workspace_handlers import WorkspaceToolHandlers
from tools.workspace_specs import (
    WorkspaceToolSpec,
    workspace_tool_specs as declared_workspace_tool_specs,
)


def workspace_tool_specs() -> tuple[WorkspaceToolSpec, ...]:
    """Return target workspace tool specs in a stable order."""
    return declared_workspace_tool_specs()


def bind_workspace_tools(
    registry: ToolRegistry,
    *,
    handlers: WorkspaceToolHandlers | None = None,
) -> None:
    """Register default workspace tools on a registry in canonical order."""
    resolver = handlers or WorkspaceToolHandlers()
    for spec in workspace_tool_specs():
        registry.register(resolver.resolve(spec), permission=spec.side_effect)
