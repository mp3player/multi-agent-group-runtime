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
    list as a JSON array.
    """

    def save(self, message: Message) -> dict[str, Any]:
        """Serialize one Message into a JSON-serializable dict."""
        if isinstance(message, System):
            return {"type": "System", "role": "system", "message": message.message}
        if isinstance(message, User):
            return {"type": "User", "role": "user", "message": message.message}
        if isinstance(message, Reasoning):
            return {"type": "Reasoning", "role": "reasoning", "message": message.message}
        if isinstance(message, AI):
            tool_calls_data: list[dict[str, Any]] = []
            if message.tool_calls:
                for tc in message.tool_calls:
                    args = tc.arguments
                    if not isinstance(args, str):
                        args = json.dumps(args, ensure_ascii=False)
                    tool_calls_data.append({
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": args,
                    })
            return {
                "type": "AI",
                "role": "assistant",
                "message": message.message,
                "reasoning": message.reasoning,
                "tool_calls": tool_calls_data,
            }
        if isinstance(message, Chunk):
            # Chunk may carry tool_calls and finish_reason.
            tool_calls_data: list[dict[str, Any]] = []
            if message.tool_calls:
                for tc in message.tool_calls:
                    args = tc.arguments
                    if not isinstance(args, str):
                        args = json.dumps(args, ensure_ascii=False)
                    tool_calls_data.append({
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": args,
                    })
            return {
                "type": "Chunk",
                "role": "assistant",
                "message": message.message,
                "reasoning": message.reasoning,
                "tool_calls": tool_calls_data,
                "finish_reason": message.finish_reason,
            }
        if isinstance(message, ToolCall):
            args = message.arguments
            if not isinstance(args, str):
                args = json.dumps(args, ensure_ascii=False)
            return {
                "type": "ToolCall",
                "role": "assistant",
                "id": message.id,
                "name": message.name,
                "arguments": args,
            }
        # Unknown subclasses fall back to base Message data.
        return {"type": "Message", "role": message.role, "message": message.message}

    def load(self, data: dict[str, Any]) -> Message:
        """Restore the matching Message subclass from a dict."""
        t = data.get("type")
        if t == "System":
            return System(data["message"])
        if t == "User":
            return User(data["message"])
        if t == "Reasoning":
            return Reasoning(data["message"])
        if t == "AI":
            tool_calls: list[ToolCall] | None = None
            tc_list = data.get("tool_calls") or []
            if tc_list:
                tool_calls = [
                    ToolCall(tc["id"], tc["name"], tc.get("arguments", ""))
                    for tc in tc_list
                ]
            return AI(
                data.get("message", ""),
                data.get("reasoning", ""),
                tool_calls=tool_calls,
            )
        if t == "Chunk":
            # Restore Chunk with tool_calls and finish_reason.
            tool_calls: list[ToolCall] | None = None
            tc_list = data.get("tool_calls") or []
            if tc_list:
                tool_calls = [
                    ToolCall(tc["id"], tc["name"], tc.get("arguments", ""))
                    for tc in tc_list
                ]
            return Chunk(
                message=data.get("message", ""),
                reasoning=data.get("reasoning", ""),
                tool_calls=tool_calls,
                finish_reason=data.get("finish_reason"),
            )
        if t == "ToolCall":
            return ToolCall(data["id"], data["name"], data.get("arguments", ""))
        # Fall back to base Message.
        return Message(data.get("role", ""), data.get("message", ""))

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
