"""LLM response parsing for single-agent runs."""

from __future__ import annotations

from typing import Any

from core.llm_runtime.errors import LLMError, validate_finish_reason
from models import AI, Chunk, ToolCall


class StreamResponseAccumulator:
    """Preserve all fields of a chunk while delivering it at most once."""

    def __init__(self) -> None:
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.tool_calls: list[ToolCall] | None = None
        self.finish_reason: str | None = None

    def add(self, chunk: Chunk) -> bool:
        if chunk.message:
            self.content.append(chunk.message)
        if chunk.reasoning:
            self.reasoning.append(chunk.reasoning)
        if chunk.tool_calls:
            self.tool_calls = chunk.tool_calls
        if chunk.finish_reason:
            self.finish_reason = chunk.finish_reason
        return bool(chunk.message or chunk.reasoning or chunk.tool_calls or chunk.finish_reason)

    def response(self) -> AI:
        validate_finish_reason(self.finish_reason)
        return AI("".join(self.content), "".join(self.reasoning), self.tool_calls)


class AgentResponseParser:
    """Compatibility adapter for the stateless parse_invoke_response function."""

    def parse(self, data: dict[str, Any]) -> tuple[AI, list[ToolCall]]:
        return parse_invoke_response(data)


def parse_invoke_response(data: dict[str, Any]) -> tuple[AI, list[ToolCall]]:
    """Parse an OpenAI-compatible response into an AI message and tool calls."""
    if not isinstance(data, dict):
        raise LLMError("Model response must be a JSON object")
    if data.get("error") is not None:
        raise LLMError(f"Model response error: {data['error']}")
    choices = data.get("choices")
    if not choices or not isinstance(choices, list):
        raise LLMError("Response is missing valid choices")
    choice = choices[0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        raise LLMError("Response is missing a valid assistant message")
    reason = choice.get("finish_reason")
    if reason is not None and not isinstance(reason, str):
        raise LLMError("finish_reason must be a string or null")
    validate_finish_reason(reason)
    msg = choice["message"]
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""
    if not isinstance(content, str) or not isinstance(reasoning, str):
        raise LLMError("Response content/reasoning_content must be text")

    tool_calls: list[ToolCall] = []
    calls = msg.get("tool_calls") or []
    if not isinstance(calls, list):
        raise LLMError("Response tool_calls must be a list")
    for tc in calls:
        if not isinstance(tc, dict) or not isinstance(tc.get("function"), dict):
            raise LLMError("Invalid tool call")
        function = tc["function"]
        if not isinstance(function.get("name"), str) or not function["name"]:
            raise LLMError("Tool call is missing a name")
        arguments = function.get("arguments", "")
        if not isinstance(arguments, (str, dict)):
            raise LLMError("Tool arguments must be a JSON string or object")
        tool_calls.append(ToolCall(
            id=tc.get("id", ""),
            name=function["name"],
            arguments=arguments,
        ))
    ai = AI(
        message=content,
        reasoning=reasoning,
        tool_calls=tool_calls or None,
    )
    return ai, tool_calls
