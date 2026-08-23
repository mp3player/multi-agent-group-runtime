"""LLM response parsing for single-agent runs."""

from __future__ import annotations

from typing import Any

from core.llm_runtime.errors import LLMError
from models import AI, ToolCall


class AgentResponseParser:
    """Parse provider responses for the ReAct loop."""

    def parse(self, data: dict[str, Any]) -> tuple[AI, list[ToolCall]]:
        return parse_invoke_response(data)


def parse_invoke_response(data: dict[str, Any]) -> tuple[AI, list[ToolCall]]:
    """Parse an OpenAI-compatible response into an AI message and tool calls."""
    choices = data.get("choices")
    if not choices or not isinstance(choices, list):
        raise LLMError(f"响应缺少 choices: {data}")
    choice = choices[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""

    tool_calls: list[ToolCall] = []
    for tc in msg.get("tool_calls", []) or []:
        tool_calls.append(ToolCall(
            id=tc.get("id", ""),
            name=tc["function"]["name"],
            arguments=tc["function"].get("arguments", ""),
        ))
    ai = AI(
        message=content,
        reasoning=reasoning,
        tool_calls=tool_calls or None,
    )
    return ai, tool_calls
