"""Build provider context without changing canonical session messages."""

from __future__ import annotations

from copy import deepcopy
from typing import Protocol

from models import Message, Reasoning, ToolCall


class ContextTransform(Protocol):
    """Synchronous per-request projection over a detached active-message view."""

    def __call__(self, messages: list[Message]) -> list[Message]: ...


def project_context(
    messages: list[Message],
    transform: ContextTransform | None = None,
) -> list[Message]:
    projected = deepcopy(messages)
    if transform is not None:
        projected = transform(projected)
        if not isinstance(projected, list) or not all(isinstance(m, Message) for m in projected):
            raise TypeError("context_transform must return a list of Message objects")
        # A transform may retain its output; providers receive their own view.
        projected = deepcopy(projected)
    validate_tool_pairs(projected)
    return projected


def validate_tool_pairs(messages: list[Message], *, allow_pending: bool = False) -> None:
    """Reject orphan, repeated or interleaved tool results.

    ``allow_pending`` permits only a trailing unfinished batch for callers
    inspecting a session while it is being committed. Provider context never
    uses that exception.
    """
    pending: set[str] = set()
    for message in messages:
        if isinstance(message, Reasoning):
            continue
        if message.role == 'tool':
            call_id = getattr(message, 'tool_call_id', None)
            if not isinstance(call_id, str) or call_id not in pending:
                raise ValueError('tool result has no matching pending call')
            pending.remove(call_id)
            continue
        if pending:
            raise ValueError('tool batch is incomplete or interleaved')
        calls = [message] if isinstance(message, ToolCall) else getattr(message, 'tool_calls', None) or []
        for call in calls:
            if not isinstance(call.id, str) or not call.id or call.id in pending:
                raise ValueError('tool call ids must be nonempty and unique')
            pending.add(call.id)
    if pending and not allow_pending:
        raise ValueError('tool batch is incomplete')
