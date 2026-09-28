"""LLM runtime errors and provider completion validation."""

from __future__ import annotations

import json
from typing import Any


class LLMError(RuntimeError):
    """LLM call error."""


class RateLimitError(LLMError):
    """LLM provider returned HTTP 429."""


class ServerError(LLMError):
    """LLM provider returned HTTP 5xx."""


class ContextWindowExceeded(LLMError):
    """The provider explicitly rejected the request's context size."""


def is_context_overflow(error: Any) -> bool:
    """Recognize structured codes only; prose and output limits are ambiguous."""
    if not isinstance(error, dict):
        return False
    codes = {"context_length_exceeded", "context_window_exceeded"}
    return any(isinstance(error.get(key), str) and error[key] in codes
               for key in ("code", "type"))


def raise_provider_error(error: Any, message: str) -> None:
    """Classify an error envelope from a successful HTTP response or SSE."""
    if isinstance(error, dict):
        status = error.get("status", error.get("status_code"))
        if type(status) is int and status >= 400:
            raise_for_status(status, json.dumps({"error": error}))
    if is_context_overflow(error):
        raise ContextWindowExceeded(message)
    raise LLMError(message)


def validate_finish_reason(reason: str | None) -> None:
    """Incomplete or rejected output cannot be committed or used for tools."""
    if reason in {"length", "content_filter", "error", "aborted"}:
        raise LLMError(f"Model response did not complete successfully: finish_reason={reason}")


def validate_invoke_completion(data: Any) -> None:
    """Require completion metadata at the built-in non-streaming HTTP boundary."""
    if not isinstance(data, dict):
        raise LLMError("Model response must be a JSON object")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError("Response is missing valid choices")
    for choice in choices:
        if not isinstance(choice, dict):
            raise LLMError("Response contains an invalid choice")
        reason = choice.get("finish_reason")
        if not isinstance(reason, str) or not reason.strip():
            raise LLMError("Non-streaming HTTP response is missing a valid finish_reason")
        validate_finish_reason(reason)


def raise_for_status(status_code: int, body: str | None) -> None:
    if status_code < 400:
        return
    message = f"HTTP {status_code}: {body or ''}"
    if status_code == 429:
        raise RateLimitError(message)
    if status_code >= 500:
        raise ServerError(message)
    if status_code in {400, 413, 422}:
        try:
            payload = json.loads(body or "")
        except ValueError:
            payload = None
        if isinstance(payload, dict) and is_context_overflow(payload.get("error")):
            raise ContextWindowExceeded(message)
    raise LLMError(message)


__all__ = [
    "LLMError",
    "ContextWindowExceeded",
    "RateLimitError",
    "ServerError",
    "raise_for_status",
]
