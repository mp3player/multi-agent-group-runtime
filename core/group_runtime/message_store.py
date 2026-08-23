"""Group transcript storage.

``GroupMessageStore`` will own message id assignment, transcript pruning,
message rendering, and transcript reset. Runtime side effects such as events,
stats, callbacks, and dispatch wakeups remain owned by ``core.group.GroupChat``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Iterable

from domain.group import DispatchMode, GroupMessage, MessageKind


@dataclass(slots=True)
class GroupMessageStore:
    """Shared group message storage with stable id assignment."""

    transcript_limit: int | None = 40
    message_store_limit: int | None = 1000
    messages: list[GroupMessage] = field(default_factory=list)
    next_message_id: int = 1

    def add(
        self,
        sender: str,
        content: str,
        *,
        kind: MessageKind = "agent",
        round_index: int = 0,
        mentions: Iterable[str] | None = None,
        propagate: bool = True,
        dispatch_mode: DispatchMode = "normal",
    ) -> GroupMessage:
        """Append and return a message."""
        msg = GroupMessage(
            id=self.next_message_id,
            sender=sender,
            content=content,
            kind=kind,
            round_index=round_index,
            mentions=list(mentions or []),
            propagate=propagate,
            dispatch_mode=dispatch_mode,
        )
        self.next_message_id += 1
        self.messages.append(msg)
        self.prune()
        return msg

    def clear(self) -> None:
        """Clear messages and reset id assignment."""
        self.messages.clear()
        self.next_message_id = 1

    def transcript(self, *, limit: int | None = None) -> str:
        """Render shared transcript text."""
        selected = self.messages
        effective_limit = self.transcript_limit if limit is None else limit
        if effective_limit is not None and effective_limit > 0:
            selected = selected[-effective_limit:]
        if not selected:
            return "(暂无群聊消息)"
        return "\n".join(msg.render() for msg in selected)

    def prune(self) -> None:
        """Keep the in-memory transcript bounded."""
        limit = self.message_store_limit
        if limit is None or limit <= 0 or len(self.messages) <= limit:
            return
        self.messages[:] = self.messages[-limit:]
