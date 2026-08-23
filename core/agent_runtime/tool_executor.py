"""Tool execution helpers for single-agent runs."""

from __future__ import annotations

from models import Message, ToolCall
from tools.registry import ToolRegistry
from tools.runtime import ToolRuntime, should_stop_after_tool_calls


class AgentToolExecutor:
    """Execute tool calls through an agent registry."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self.runtime = ToolRuntime(registry)

    def execute(self, tool_calls: list[ToolCall]) -> list[Message]:
        return self.runtime.execute(tool_calls)

    def should_stop(self, tool_calls: list[ToolCall]) -> bool:
        return should_stop_after_tool_calls(tool_calls)


def execute_tool_calls(
    registry: ToolRegistry,
    tool_calls: list[ToolCall],
) -> list[Message]:
    """Execute tool calls and return role=tool result messages."""
    return ToolRuntime(registry).execute(tool_calls)
