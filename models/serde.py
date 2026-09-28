"""Message serialization and deserialization.

MessageSerde is the base class. JsonMessageSerde stores Message lists as JSON.
"""

from __future__ import annotations

import json
import os
from typing import Any

from models.message import (
    AI,
    Chunk,
    Message,
    Reasoning,
    System,
    ToolCall,
    User,
)
from models.message_codec import encode_tool_call


class MessageSerde:
    """Base class for Message serialization and deserialization.

    Subclasses implement save/load to map Message objects to a storage shape
    such as JSON strings or database rows, and back again.
    """

    def save(self, message: Message) -> Any:
        """Serialize one Message into a storage shape."""
        raise NotImplementedError

    def load(self, data: Any) -> Message:
        """Deserialize one Message from a storage shape."""
        raise NotImplementedError


class JsonMessageSerde(MessageSerde):
    """Serializer for storing Message lists in JSON files.

    save maps one Message into a JSON-serializable dict.
    load maps a dict back into the matching Message subclass.

    File IO is provided by save_to_file/load_from_file and stores a Message
    list as a JSON array. This compatibility format retains the legacy
    ``message`` key and text tool arguments; it is not a strict session snapshot.
    """

    def save(self, message: Message) -> dict[str, Any]:
        """Serialize one Message into a JSON-serializable dict."""
        if isinstance(message, System):
            return {"type": "System", "role": "system", "message": message.message}
        if isinstance(message, User):
            return {"type": "User", "role": "user", "message": message.message}
        if isinstance(message, Reasoning):
            return {"type": "Reasoning", "role": "reasoning", "message": message.message}
        if isinstance(message, (AI, Chunk)):
            data = {
                "type": "AI" if isinstance(message, AI) else "Chunk",
                "role": "assistant",
                "message": message.message,
                "reasoning": message.reasoning,
                "tool_calls": [
                    encode_tool_call(call, arguments_as_text=True)
                    for call in message.tool_calls or []
                ],
            }
            if isinstance(message, Chunk):
                data.update(
                    finish_reason=message.finish_reason,
                    usage=message.usage,
                    response_model=message.response_model,
                )
            return data
        if isinstance(message, ToolCall):
            return {
                "type": "ToolCall",
                "role": "assistant",
                **encode_tool_call(message, arguments_as_text=True),
            }
        # Unknown subclasses fall back to base Message data.
        data = {
            "type": "Message",
            "role": message.role,
            "message": message.message,
            "tool_success": message.tool_success,
            "ends_run": message.ends_run,
        }
        if message.role == "tool" and hasattr(message, "tool_call_id"):
            data["tool_call_id"] = message.tool_call_id
        return data

    def load(self, data: dict[str, Any]) -> Message:
        """Restore the matching Message subclass from a dict."""
        t = data.get("type")
        if t == "System":
            return System(data["message"])
        if t == "User":
            return User(data["message"])
        if t == "Reasoning":
            return Reasoning(data["message"])
        if t in ("AI", "Chunk"):
            tool_calls: list[ToolCall] | None = None
            tc_list = data.get("tool_calls") or []
            if tc_list:
                tool_calls = [
                    ToolCall(tc["id"], tc["name"], tc.get("arguments", ""))
                    for tc in tc_list
                ]
            if t == "AI":
                return AI(
                    data.get("message", ""),
                    data.get("reasoning", ""),
                    tool_calls=tool_calls,
                )
            return Chunk(
                message=data.get("message", ""),
                reasoning=data.get("reasoning", ""),
                tool_calls=tool_calls,
                finish_reason=data.get("finish_reason"),
                usage=data.get("usage"),
                response_model=data.get("response_model"),
            )
        if t == "ToolCall":
            return ToolCall(data["id"], data["name"], data.get("arguments", ""))
        # Fall back to base Message.
        message = Message(data.get("role", ""), data.get("message", ""))
        message.tool_success = data.get("tool_success", False)
        message.ends_run = data.get("ends_run", False)
        if message.role == "tool" and "tool_call_id" in data:
            message.tool_call_id = data["tool_call_id"]
        return message

    def save_to_file(self, messages: list[Message], path: str | os.PathLike[str]) -> None:
        """Save a Message list to a JSON file."""
        data = [self.save(m) for m in messages]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_from_file(self, path: str | os.PathLike[str]) -> list[Message]:
        """Load a Message list from a JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [self.load(d) for d in data]
