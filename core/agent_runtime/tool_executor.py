"""Tool execution helpers for single-agent runs."""

from __future__ import annotations

from models import Message, ToolCall
from tools.registry import ToolRegistry
from tools.runtime import ToolRuntime, should_stop_after_tool_calls


class AgentToolExecutor:
    """Customizable execution adapter; ToolRuntime owns policy, workspace, and audit."""

    def __init__(self, registry: ToolRegistry, *, runtime: ToolRuntime | None = None) -> None:
        if runtime is not None and runtime.registry is not registry:
            raise ValueError('registry must match the injected tool runtime')
        self.runtime = runtime if runtime is not None else ToolRuntime(registry)

    @property
    def registry(self) -> ToolRegistry:
        return self.runtime.registry

    @registry.setter
    def registry(self, registry: ToolRegistry) -> None:
        self.runtime.set_registry(registry)

    def execute(self, tool_calls: list[ToolCall]) -> list[Message]:
        return self.runtime.execute(tool_calls)

    def should_stop(self, results: list[Message]) -> bool:
        return should_stop_after_tool_calls(results)


def execute_tool_calls(
    registry: ToolRegistry,
    tool_calls: list[ToolCall],
) -> list[Message]:
    """Execute tool calls and return role=tool result messages."""
    return ToolRuntime(registry).execute(tool_calls)
