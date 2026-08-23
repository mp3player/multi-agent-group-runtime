"""LLM runtime error types and HTTP status handling."""

from __future__ import annotations


class LLMError(RuntimeError):
    """LLM call error."""


class RateLimitError(LLMError):
    """LLM provider returned HTTP 429."""


class ServerError(LLMError):
    """LLM provider returned HTTP 5xx."""


def raise_for_status(status_code: int, body: str | None) -> None:
    if status_code < 400:
        return
    message = f"HTTP {status_code}: {body or ''}"
    if status_code == 429:
        raise RateLimitError(message)
    if status_code >= 500:
        raise ServerError(message)
    raise LLMError(message)


__all__ = [
    "LLMError",
    "RateLimitError",
    "ServerError",
    "raise_for_status",
]
