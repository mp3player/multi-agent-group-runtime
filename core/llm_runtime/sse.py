"""SSE parsing helpers for OpenAI-compatible streaming responses."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, AsyncIterator, Iterator

from models import Chunk, ToolCall
from core.llm_runtime.errors import LLMError, raise_provider_error, validate_finish_reason


def normalize_usage(value: Any) -> dict[str, Any] | None:
    """Keep valid counters; absent or malformed telemetry is unknown, not zero."""
    if not isinstance(value, dict) or not value:
        return None
    counts = ("prompt_tokens", "completion_tokens", "total_tokens",
              "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
    if not any(key in value for key in counts):
        return None
    for key in counts:
        if key in value and (type(value[key]) is not int or value[key] < 0):
            return None
    for key in ("prompt_tokens_details", "completion_tokens_details"):
        details = value.get(key)
        if details is None:
            continue
        if not isinstance(details, dict):
            return None
        if any(count is not None and (type(count) is not int or count < 0)
               for count in details.values()):
            return None
    return deepcopy(value)


class ToolCallAccumulator:
    """Accumulate streaming tool call deltas."""

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, str]] = {}
        self.finish_reason: str | None = None
        self.done = False
        self.usage: dict[str, Any] | None = None
        self.response_model: str | None = None

    def add_delta(self, delta: dict[str, Any]) -> None:
        tool_calls = delta.get("tool_calls")
        if tool_calls:
            if not isinstance(tool_calls, list):
                raise LLMError("Streaming tool_calls must be a list")
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    raise LLMError("Invalid streaming tool call")
                idx = tool_call.get("index", 0)
                if type(idx) is not int or idx < 0:
                    raise LLMError("Streaming tool call index must be a nonnegative integer")
                if idx not in self._calls:
                    self._calls[idx] = {"id": "", "name": "", "arguments": ""}
                entry = self._calls[idx]
                fn = tool_call.get("function") or {}
                if not isinstance(fn, dict):
                    raise LLMError("Invalid streaming tool function")
                for value in (tool_call.get("id"), fn.get("name"), fn.get("arguments")):
                    if value is not None and not isinstance(value, str):
                        raise LLMError("Streaming tool id/name/arguments must be text")
                if tool_call.get("id"):
                    entry["id"] = tool_call["id"]
                if fn.get("name"):
                    entry["name"] = fn["name"]
                if fn.get("arguments"):
                    entry["arguments"] += fn["arguments"]

    def set_finish_reason(self, reason: str) -> None:
        if not isinstance(reason, str) or not reason:
            raise LLMError("Invalid streaming finish_reason")
        self.finish_reason = reason

    def validate_complete(self) -> None:
        if self.finish_reason is None:
            raise LLMError("Streaming response ended before finish_reason was received")
        validate_finish_reason(self.finish_reason)

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


def validate_stream_compatibility(value: str) -> None:
    if value not in ("standard", "vllm_gemma4"):
        raise ValueError("stream compatibility must be 'standard' or 'vllm_gemma4'")


def parse_sse_data(
    data: str, acc: ToolCallAccumulator, *, stream_compatibility: str = "standard",
) -> Chunk | None:
    """Parse one complete SSE data payload."""
    data = data.strip()
    if not data:
        return None
    if data == "[DONE]":
        acc.done = True
        return None
    try:
        obj = json.loads(data)
    except ValueError as error:
        raise LLMError("Streaming response contains invalid JSON") from error
    if not isinstance(obj, dict):
        raise LLMError("Streaming response must be a JSON object")
    # Capture billable metadata before validating the response body. A later
    # provider/protocol/cleanup failure does not undo the completed request.
    usage = normalize_usage(obj.get("usage"))
    if usage is not None:
        acc.usage = usage
    response_model = obj.get("model")
    if isinstance(response_model, str) and response_model:
        acc.response_model = response_model
    if obj.get("error") is not None:
        raise_provider_error(obj["error"], f"Streaming response error: {obj['error']}")
    choices = obj.get("choices")
    if not isinstance(choices, list):
        raise LLMError("Streaming response is missing valid choices")
    if not choices:
        # Usage-only events carry an empty choices array.
        return Chunk(usage=usage, response_model=acc.response_model) if usage is not None else None
    choice = choices[0]
    if not isinstance(choice, dict):
        raise LLMError("Invalid streaming choice")
    delta = choice.get("delta") or {}
    if not isinstance(delta, dict):
        raise LLMError("Invalid streaming delta")
    if delta.get("tool_calls"):
        acc.add_delta(delta)
    finish_reason = choice.get("finish_reason")
    if finish_reason:
        acc.set_finish_reason(finish_reason)
    content = delta.get("content") or ""
    reasoning = delta.get("reasoning_content") or ""
    if not isinstance(content, str) or not isinstance(reasoning, str):
        raise LLMError("Streaming content/reasoning_content must be text")
    # Opt-in workaround for a verified vLLM/Gemma 4 terminal SSE frame.
    # Never strip a substring or a normal content delta: these may be literal
    # user-requested text. Keep finish/usage/reasoning handling intact.
    if (
        stream_compatibility == "vllm_gemma4"
        and content == "<turn|>"
        and finish_reason == "stop"
        and type(choice.get("stop_reason")) is int
        and choice["stop_reason"] == 106
        and isinstance(response_model, str)
        and response_model.startswith("google/gemma-4-")
        and isinstance(obj.get("system_fingerprint"), str)
        and obj["system_fingerprint"].startswith("vllm-")
        and not delta.get("tool_calls")
    ):
        content = ""
    if content or reasoning:
        return Chunk(message=content, reasoning=reasoning, usage=usage,
                     response_model=acc.response_model)
    if finish_reason and not delta.get("tool_calls"):
        return Chunk(finish_reason=finish_reason, usage=usage, response_model=acc.response_model)
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
    "normalize_usage",
    "parse_sse_chunk",
    "parse_sse_data",
]
