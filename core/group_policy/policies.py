"""Built-in group dispatch policies."""

from __future__ import annotations

from domain.group import GroupMember, GroupMessage
from core.group_policy.rules import (
    directed_auto_pass_messages,
    dispatchable_unread_messages,
    first_responder_message_claimed,
    is_directed_to_member,
    next_member,
    unread_messages,
)
from core.group_policy.types import GroupDispatchContext


class DefaultGroupDispatchPolicy:
    """Adapter for the existing group scheduling semantics."""

    name = "default"

    def unread_messages(
        self,
        group: GroupDispatchContext,
        member: GroupMember,
    ) -> list[GroupMessage]:
        return unread_messages(group, member)

    def dispatchable_messages(
        self,
        group: GroupDispatchContext,
        member: GroupMember,
        candidates: list[GroupMember],
    ) -> list[GroupMessage]:
        return dispatchable_unread_messages(group, member, candidates)

    def synthetic_pass_messages(
        self,
        group: GroupDispatchContext,
        member: GroupMember,
    ) -> list[GroupMessage]:
        return directed_auto_pass_messages(group, member)

    def next_member(
        self,
        group: GroupDispatchContext,
        candidates: list[GroupMember],
    ) -> GroupMember | None:
        return next_member(group, candidates)

    def is_directed_to_member(
        self,
        group: GroupDispatchContext,
        message: GroupMessage,
        member: GroupMember,
    ) -> bool:
        return is_directed_to_member(group, message, member)

    def was_directed_to_member(
        self,
        group: GroupDispatchContext,
        member: GroupMember,
        messages: list[GroupMessage],
    ) -> bool:
        return any(
            self.is_directed_to_member(group, message, member)
            for message in messages
        )


class OnDemandGroupDispatchPolicy(DefaultGroupDispatchPolicy):
    """Dispatch only explicit directed work after the initial responder."""

    name = "on_demand"

    def dispatchable_messages(
        self,
        group: GroupDispatchContext,
        member: GroupMember,
        candidates: list[GroupMember],
    ) -> list[GroupMessage]:
        return [
            message for message in super().dispatchable_messages(
                group,
                member,
                candidates,
            )
            if (
                message.dispatch_mode == "first_responder_only"
                or message.dispatch_mode == "broadcast"
                or self.is_directed_to_member(group, message, member)
            )
        ]

    def synthetic_pass_messages(
        self,
        group: GroupDispatchContext,
        member: GroupMember,
    ) -> list[GroupMessage]:
        selected: list[GroupMessage] = []
        for message in self.unread_messages(group, member):
            if self.is_directed_to_member(group, message, member):
                break
            if message.dispatch_mode == "broadcast":
                break
            if (
                message.dispatch_mode == "first_responder_only"
                and not first_responder_message_claimed(group, message)
            ):
                break
            selected.append(message)
        return selected

    def next_member(
        self,
        group: GroupDispatchContext,
        candidates: list[GroupMember],
    ) -> GroupMember | None:
        idle_members = [
            member for member in candidates
            if member.enabled and member.status == "idle"
        ]
        directed: list[GroupMember] = []
        first_responder: list[GroupMember] = []
        for member in idle_members:
            unread = self.dispatchable_messages(group, member, idle_members)
            if not unread:
                continue
            if any(
                self.is_directed_to_member(group, message, member)
                for message in unread
            ):
                directed.append(member)
            else:
                first_responder.append(member)
        return (directed or first_responder or [None])[0]


class BroadcastFeedbackGroupDispatchPolicy(OnDemandGroupDispatchPolicy):
    """On-demand dispatch with feedback routed to the broadcast originator."""

    name = "broadcast_feedback"

    def dispatchable_messages(
        self,
        group: GroupDispatchContext,
        member: GroupMember,
        candidates: list[GroupMember],
    ) -> list[GroupMessage]:
        return [
            message for message in DefaultGroupDispatchPolicy.dispatchable_messages(
                self,
                group,
                member,
                candidates,
            )
            if (
                message.dispatch_mode == "first_responder_only"
                or message.dispatch_mode == "broadcast"
                or self.is_directed_to_member(group, message, member)
                or (
                    not self._has_running_members(group)
                    and self.is_feedback_to_broadcast_origin(group, message, member)
                )
            )
        ]

    def synthetic_pass_messages(
        self,
        group: GroupDispatchContext,
        member: GroupMember,
    ) -> list[GroupMessage]:
        selected: list[GroupMessage] = []
        for message in self.unread_messages(group, member):
            if self.is_directed_to_member(group, message, member):
                break
            if message.dispatch_mode == "broadcast":
                break
            if self.is_feedback_to_broadcast_origin(group, message, member):
                break
            if (
                message.dispatch_mode == "first_responder_only"
                and not first_responder_message_claimed(group, message)
            ):
                break
            selected.append(message)
        return selected

    def next_member(
        self,
        group: GroupDispatchContext,
        candidates: list[GroupMember],
    ) -> GroupMember | None:
        idle_members = [
            member for member in candidates
            if member.enabled and member.status == "idle"
        ]
        directed: list[GroupMember] = []
        feedback: list[GroupMember] = []
        first_responder: list[GroupMember] = []
        for member in idle_members:
            unread = self.dispatchable_messages(group, member, idle_members)
            if not unread:
                continue
            if any(
                self.is_directed_to_member(group, message, member)
                for message in unread
            ):
                directed.append(member)
            elif any(
                self.is_feedback_to_broadcast_origin(group, message, member)
                for message in unread
            ):
                feedback.append(member)
            else:
                first_responder.append(member)
        return (directed or feedback or first_responder or [None])[0]

    def is_feedback_to_broadcast_origin(
        self,
        group: GroupDispatchContext,
        message: GroupMessage,
        member: GroupMember,
    ) -> bool:
        if message.dispatch_mode != "feedback":
            return False
        if message.sender == member.name:
            return False
        origin = self._latest_broadcast_origin_before(group, message.id)
        return origin == member.name

    def _latest_broadcast_origin_before(
        self,
        group: GroupDispatchContext,
        message_id: int,
    ) -> str | None:
        for message in reversed(group.messages):
            if message.id >= message_id:
                continue
            if message.dispatch_mode == "broadcast" and message.propagate:
                return message.sender
        return None

    def _has_running_members(self, group: GroupDispatchContext) -> bool:
        return any(member.status == "running" for member in group.members.values())


__all__ = [
    "BroadcastFeedbackGroupDispatchPolicy",
    "DefaultGroupDispatchPolicy",
    "OnDemandGroupDispatchPolicy",
]
