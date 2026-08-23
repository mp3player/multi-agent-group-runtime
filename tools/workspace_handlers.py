"""Workspace tool handler lookup."""

from __future__ import annotations

from importlib import import_module

from tools.decorator import ToolFunction
from tools.workspace_specs import WorkspaceToolSpec


class WorkspaceToolHandlers:
    """Resolve declared workspace tool specs to callable tool functions."""

    def resolve(self, spec: WorkspaceToolSpec) -> ToolFunction:
        """Return the decorated tool function declared by one spec."""
        module = import_module(spec.handler_module)
        handler = getattr(module, spec.handler_name)
        if not isinstance(handler, ToolFunction):
            raise TypeError(
                f"workspace tool handler is not a ToolFunction: "
                f"{spec.handler_module}.{spec.handler_name}"
            )
        return handler
