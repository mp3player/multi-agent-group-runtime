"""Message base class and subclasses.

Message is the base class and stores role + message text.
Subclasses:
    System     system message
    User       user message
    AI         assistant response with optional reasoning
    Reasoning  standalone reasoning message
    Chunk      streaming output chunk
    ToolCall   tool call message
"""

from __future__ import annotations

import json
from typing import Any


class Message:
    """Base conversation message.

    Args:
        role: Message role such as system, user, assistant, reasoning, or tool.
        message: Message text content.
    """

    def __init__(self, role: str, message: str = "") -> None:
        self.role = role
        self.message = message
        # Execution-only outcome metadata; provider/session payloads need only content.
        self.tool_success = False
        self.ends_run = False

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.message}

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(role={self.role!r}, message={self.message!r})"

    def __eq__(self, other: Any) -> bool:
        return (
            isinstance(other, Message)
            and self.__class__ == other.__class__
            and self.role == other.role
            and self.message == other.message
        )


class System(Message):
    """System message."""

    def __init__(self, message: str) -> None:
        super().__init__("system", message)


class User(Message):
    """User message."""

    def __init__(self, message: str) -> None:
        super().__init__("user", message)


class AI(Message):
    """Assistant response with optional reasoning and tool calls.

    Args:
        message: Final response content.
        reasoning: Optional reasoning content.
        tool_calls: Optional list of ToolCall objects.
    """

    def __init__(
        self,
        message: str = "",
        reasoning: str = "",
        tool_calls: list[ToolCall] | None = None,
    ) -> None:
        super().__init__("assistant", message)
        self.reasoning = reasoning
        self.tool_calls = tool_calls

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": "assistant", "content": self.message}
        if self.reasoning:
            d["reasoning_content"] = self.reasoning
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments_str,
                    },
                }
                for tc in self.tool_calls
            ]
        return d

    def __repr__(self) -> str:
        return (
            f"AI(message={self.message!r}, reasoning={self.reasoning!r}, "
            f"tool_calls={self.tool_calls!r})"
        )


class Reasoning(Message):
    """Standalone reasoning message."""

    def __init__(self, message: str) -> None:
        super().__init__("reasoning", message)


class Chunk(Message):
    """Single streaming output chunk.

    May carry one or more of:
        message: delta text
        reasoning: reasoning delta
        tool_calls: complete ToolCall list, yielded once at stream end
        finish_reason: finish reason such as stop, tool_calls, or length
        usage: final request usage, or None when the provider did not report it
        response_model: provider model identity, with the request model as fallback
    """

    def __init__(
        self,
        message: str = "",
        reasoning: str = "",
        tool_calls: list[ToolCall] | None = None,
        finish_reason: str | None = None,
        usage: dict[str, Any] | None = None,
        response_model: str | None = None,
    ) -> None:
        super().__init__("assistant", message)
        self.reasoning = reasoning
        self.tool_calls = tool_calls
        self.finish_reason = finish_reason
        self.usage = usage
        self.response_model = response_model

    def __repr__(self) -> str:
        parts = []
        if self.message:
            parts.append(f"message={self.message!r}")
        if self.reasoning:
            parts.append(f"reasoning={self.reasoning!r}")
        if self.tool_calls:
            parts.append(f"tool_calls={self.tool_calls!r}")
        if self.finish_reason:
            parts.append(f"finish_reason={self.finish_reason!r}")
        if self.usage is not None:
            parts.append(f"usage={self.usage!r}")
        if self.response_model is not None:
            parts.append(f"response_model={self.response_model!r}")
        return f"Chunk({', '.join(parts)})"


class ToolCall(Message):
    """Tool call.

    Args:
        id: Tool call id.
        name: Function name.
        arguments: Function arguments as a dict or serialized JSON string.
    """

    def __init__(
        self,
        id: str,
        name: str,
        arguments: dict[str, Any] | str,
    ) -> None:
        super().__init__("assistant", "")
        self.id = id
        self.name = name
        self.arguments = arguments

    @property
    def arguments_str(self) -> str:
        """Serialized argument string used when sending to the provider."""
        if isinstance(self.arguments, str):
            return self.arguments
        return json.dumps(self.arguments, ensure_ascii=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": self.id,
                    "type": "function",
                    "function": {
                        "name": self.name,
                        "arguments": self.arguments_str,
                    },
                }
            ],
        }

    def __repr__(self) -> str:
        return (
            f"ToolCall(id={self.id!r}, name={self.name!r}, "
            f"arguments={self.arguments!r})"
        )
