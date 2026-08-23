"""Group prompt context construction.

``GroupContextBuilder`` will build member-facing unread prompts, recent group
context, and memory/status snippets.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.group import GroupMessage


@dataclass(slots=True)
class GroupContextBuilder:
    """Build member-facing group prompt text."""

    recent_context_limit: int | None = 8

    def build_member_prompt(
        self,
        messages: list[GroupMessage],
        unread: list[GroupMessage],
    ) -> str:
        """Build the prompt for one member turn."""
        unread_text = (
            "\n".join(msg.render() for msg in unread)
            if unread else "(没有新的可传播消息)"
        )
        context = self.recent_context_messages(messages, unread)
        if not context:
            return f"Unread group messages:\n{unread_text}"
        context_text = "\n".join(msg.render() for msg in context)
        return (
            "Recent group context (for orientation only; respond to unread messages):\n"
            f"{context_text}\n\n"
            f"Unread group messages:\n{unread_text}"
        )

    def recent_context_messages(
        self,
        messages: list[GroupMessage],
        unread: list[GroupMessage],
    ) -> list[GroupMessage]:
        """Return recent propagating context excluding current unread messages."""
        limit = self.recent_context_limit
        if limit is None or limit <= 0:
            return []
        unread_ids = {msg.id for msg in unread}
        selected = [
            msg for msg in messages
            if msg.propagate and msg.id not in unread_ids
        ]
        return selected[-limit:]
