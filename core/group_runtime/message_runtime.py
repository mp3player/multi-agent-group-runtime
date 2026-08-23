"""Group message side-effect runtime."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from domain.group import DispatchMode, GroupMessage, MessageKind
from core.group_turns import is_pass


@dataclass(slots=True)
class GroupMessageRuntime:
    """Append group messages and run related side effects."""

    def add_message(
        self,
        group: object,
        sender: str,
        content: str,
        *,
        kind: MessageKind = "agent",
        round_index: int,
        mentions: Iterable[str] | None = None,
        propagate: bool | None = None,
        dispatch_mode: DispatchMode = "normal",
        on_message: Callable[[GroupMessage], None] | None = None,
        wake: Callable[[], None] | None = None,
    ) -> GroupMessage:
        """Append a message and record stats/events/callbacks."""
        content = content.strip()
        if propagate is None:
            propagate = not is_pass(content)
        message_mentions = group.validate_message_mentions(mentions or [])
        msg = group.message_store.add(
            sender,
            content,
            kind=kind,
            round_index=round_index,
            mentions=message_mentions,
            propagate=propagate,
            dispatch_mode=dispatch_mode,
        )
        group.stats.record_message(msg, is_pass=is_pass(msg.content))
        group.events.record_message(msg)
        if on_message is not None:
            on_message(msg)
        if wake is not None and msg.propagate:
            wake()
        return msg
