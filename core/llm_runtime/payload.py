"""LLM request payload helpers."""

from __future__ import annotations

from typing import Any

from models import AI, Message, Reasoning


def to_dict_list(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert local messages to OpenAI-compatible message dictionaries."""
    result: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, Reasoning):
            continue
        data = message.to_dict()
        if isinstance(message, AI):
            data = {
                key: value for key, value in data.items()
                if key in ("role", "content", "tool_calls")
            }
        if message.role == "tool":
            tool_call_id = getattr(message, "tool_call_id", None)
            if tool_call_id:
                data["tool_call_id"] = tool_call_id
        result.append(data)
    return result


__all__ = ["to_dict_list"]
