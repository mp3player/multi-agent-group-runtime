"""Group dispatch policy protocols."""

from __future__ import annotations

from collections import OrderedDict
from typing import Protocol

from domain.group import GroupMember, GroupMessage


class GroupDispatchContext(Protocol):
    """Read-only context required by dispatch policies."""

    messages: list[GroupMessage]
    members: OrderedDict[str, GroupMember]


class GroupDispatchPolicy(Protocol):
    """Strategy interface for deciding which group member may run next."""

    name: str

    def unread_messages(
        self,
        group: GroupDispatchContext,
        member: GroupMember,
    ) -> list[GroupMessage]:
        """Return propagating messages this member has not read."""
        ...

    def dispatchable_messages(
        self,
        group: GroupDispatchContext,
        member: GroupMember,
        candidates: list[GroupMember],
    ) -> list[GroupMessage]:
        """Return unread messages that should be passed into this turn."""
        ...

    def synthetic_pass_messages(
        self,
        group: GroupDispatchContext,
        member: GroupMember,
    ) -> list[GroupMessage]:
        """Return unread messages the runtime should synthetic-pass."""
        ...

    def next_member(
        self,
        group: GroupDispatchContext,
        candidates: list[GroupMember],
    ) -> GroupMember | None:
        """Return the next member to run, or ``None`` when no turn is ready."""
        ...

    def is_directed_to_member(
        self,
        group: GroupDispatchContext,
        message: GroupMessage,
        member: GroupMember,
    ) -> bool:
        """Return whether one message is directed to one member."""
        ...

    def was_directed_to_member(
        self,
        group: GroupDispatchContext,
        member: GroupMember,
        messages: list[GroupMessage],
    ) -> bool:
        """Return whether any message in this turn is directed to the member."""
        ...


__all__ = [
    "GroupDispatchContext",
    "GroupDispatchPolicy",
]
