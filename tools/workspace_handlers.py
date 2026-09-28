"""Compatibility adapter for workspace tool handler access."""

from __future__ import annotations

from tools.decorator import ToolFunction
from tools.workspace_specs import WorkspaceToolSpec


class WorkspaceToolHandlers:
    """Expose declared callables through the legacy resolver interface."""

    def resolve(self, spec: WorkspaceToolSpec) -> ToolFunction:
        """Return the decorated tool function declared by one spec."""
        return spec.tool
