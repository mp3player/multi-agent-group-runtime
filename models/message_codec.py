"""Shared field conversion; storage formats own validation and compatibility."""

from __future__ import annotations

from typing import Any

from models.message import ToolCall


def encode_tool_call(call: ToolCall, *, arguments_as_text: bool = False) -> dict[str, Any]:
    """Expose call fields, retaining raw arguments unless the format needs text."""
    return {
        "id": call.id,
        "name": call.name,
        "arguments": call.arguments_str if arguments_as_text else call.arguments,
    }
