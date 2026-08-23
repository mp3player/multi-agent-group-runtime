"""SSE parsing helpers for OpenAI-compatible streaming responses."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Iterator

from models import Chunk, ToolCall


class ToolCallAccumulator:
    """Accumulate streaming tool call deltas."""

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, str]] = {}
        self.finish_reason: str | None = None

    def add_delta(self, delta: dict[str, Any]) -> None:
        tool_calls = delta.get("tool_calls")
        if tool_calls:
            for tool_call in tool_calls:
                idx = tool_call.get("index", 0)
                if idx not in self._calls:
                    self._calls[idx] = {"id": "", "name": "", "arguments": ""}
                entry = self._calls[idx]
                if tool_call.get("id"):
                    entry["id"] = tool_call["id"]
                fn = tool_call.get("function", {})
                if fn.get("name"):
                    entry["name"] = fn["name"]
                if fn.get("arguments"):
                    entry["arguments"] += fn["arguments"]

    def set_finish_reason(self, reason: str) -> None:
        self.finish_reason = reason

    def build_tool_calls(self) -> list[ToolCall] | None:
        if not self._calls:
            return None
        result: list[ToolCall] = []
        for idx in sorted(self._calls.keys()):
            entry = self._calls[idx]
            result.append(ToolCall(
                id=entry["id"],
                name=entry["name"],
                arguments=entry["arguments"],
            ))
        return result


def iter_sse_data(lines: Iterator[str]) -> Iterator[str]:
    data_parts: list[str] = []
    for raw_line in lines:
        line = raw_line.rstrip("\r")
        if not line:
            if data_parts:
                yield "\n".join(data_parts)
                data_parts.clear()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_parts.append(line[5:].lstrip())
    if data_parts:
        yield "\n".join(data_parts)


async def aiter_sse_data(lines: AsyncIterator[str]) -> AsyncIterator[str]:
    data_parts: list[str] = []
    async for raw_line in lines:
        line = raw_line.rstrip("\r")
        if not line:
            if data_parts:
                yield "\n".join(data_parts)
                data_parts.clear()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_parts.append(line[5:].lstrip())
    if data_parts:
        yield "\n".join(data_parts)


def parse_sse_data(data: str, acc: ToolCallAccumulator) -> Chunk | None:
    """Parse one complete SSE data payload."""
    data = data.strip()
    if not data or data == "[DONE]":
        return None
    try:
        obj = json.loads(data)
    except ValueError:
        return None
    try:
        choice = obj["choices"][0]
    except (KeyError, IndexError):
        return None
    delta = choice.get("delta", {})
    if delta.get("tool_calls"):
        acc.add_delta(delta)
    finish_reason = choice.get("finish_reason")
    if finish_reason:
        acc.set_finish_reason(finish_reason)
    content = delta.get("content") or ""
    reasoning = delta.get("reasoning_content") or ""
    if content or reasoning:
        return Chunk(message=content, reasoning=reasoning)
    if finish_reason and not delta.get("tool_calls"):
        return Chunk(finish_reason=finish_reason)
    return None


def parse_sse_chunk(line: str, acc: ToolCallAccumulator) -> Chunk | None:
    """Parse one single-line SSE chunk."""
    if not line:
        return None
    line = line.strip()
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    return parse_sse_data(data, acc)


__all__ = [
    "ToolCallAccumulator",
    "aiter_sse_data",
    "iter_sse_data",
    "parse_sse_chunk",
    "parse_sse_data",
]
