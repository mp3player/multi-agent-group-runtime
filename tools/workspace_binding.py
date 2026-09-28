"""Workspace tool registry binding."""

from __future__ import annotations

from tools.registry import ToolRegistry
from tools.workspace_handlers import WorkspaceToolHandlers
from tools.workspace_specs import WorkspaceToolSpec, workspace_tool_specs


def bind_workspace_tools(
    registry: ToolRegistry,
    *,
    handlers: WorkspaceToolHandlers | None = None,
) -> None:
    """Register default workspace tools on a registry in canonical order."""
    for spec in workspace_tool_specs():
        handler = spec.tool if handlers is None else handlers.resolve(spec)
        registry.register(handler, permission=spec.side_effect)
