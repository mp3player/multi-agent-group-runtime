"""Pure group dispatch helper rules."""

from __future__ import annotations

from domain.group import GroupMember, GroupMessage
from core.group_policy.types import GroupDispatchContext


def next_member(
    group: GroupDispatchContext,
    members: list[GroupMember],
) -> GroupMember | None:
    """Return the next member according to default group dispatch rules."""
    idle_members = [m for m in members if m.enabled and m.status == "idle"]
    mentioned: list[GroupMember] = []
    normal: list[GroupMember] = []
    for member in idle_members:
        unread = dispatchable_unread_messages(group, member, idle_members)
        if not unread:
            continue
        if any(member.name in (msg.mentions or []) for msg in unread):
            mentioned.append(member)
        else:
            normal.append(member)
    return (mentioned or normal or [None])[0]


def unread_messages(
    group: GroupDispatchContext,
    member: GroupMember,
) -> list[GroupMessage]:
    """Return propagating messages this member has not read."""
    return [
        msg for msg in group.messages
        if (
            msg.propagate
            and msg.id > member.last_read_message_id
            and msg.sender != member.name
        )
    ]


def effective_mentions(
    group: GroupDispatchContext,
    message: GroupMessage,
) -> list[str]:
    """Return mentions that still point at enabled known members."""
    names: list[str] = []
    for name in message.mentions or []:
        member = group.members.get(name)
        if member is not None and member.enabled:
            names.append(name)
    return names


def is_directed_message(
    group: GroupDispatchContext,
    message: GroupMessage,
) -> bool:
    """Return whether a message has any enabled known mention target."""
    return bool(effective_mentions(group, message))


def is_directed_to_member(
    group: GroupDispatchContext,
    message: GroupMessage,
    member: GroupMember,
) -> bool:
    """Return whether a directed message targets one member."""
    mentions = effective_mentions(group, message)
    return bool(mentions) and member.name in mentions


def directed_auto_pass_messages(
    group: GroupDispatchContext,
    member: GroupMember,
) -> list[GroupMessage]:
    """Return the unread prefix that should be synthetic-passed."""
    selected: list[GroupMessage] = []
    for msg in unread_messages(group, member):
        if not is_directed_message(group, msg):
            break
        if is_directed_to_member(group, msg, member):
            break
        selected.append(msg)
    return selected


def dispatchable_unread_messages(
    group: GroupDispatchContext,
    member: GroupMember,
    candidates: list[GroupMember],
) -> list[GroupMessage]:
    """Return unread messages that may be assigned to one member turn."""
    unread = unread_messages(group, member)
    if not unread:
        return []
    unread = [
        msg for msg in unread
        if (
            not is_directed_message(group, msg)
            or is_directed_to_member(group, msg, member)
        )
    ]
    if not unread:
        return []
    normal_messages = [
        msg for msg in unread
        if msg.dispatch_mode in ("normal", "broadcast", "feedback")
    ]
    first_only = [
        msg for msg in unread
        if msg.dispatch_mode == "first_responder_only"
    ]
    first_only = [
        msg for msg in first_only
        if (
            (
                is_directed_message(group, msg)
                and is_directed_to_member(group, msg, member)
            )
            or not first_responder_message_claimed(group, msg)
        )
    ]
    if normal_messages:
        return [*first_only, *normal_messages]
    if not first_only:
        return []
    if any(m.status == "running" for m in group.members.values()):
        return []
    mentioned_names = {
        name
        for msg in first_only
        for name in (msg.mentions or [])
    }
    if mentioned_names:
        mentioned_candidates = [
            c for c in candidates
            if c.name in mentioned_names and c.enabled and c.status == "idle"
        ]
        if mentioned_candidates:
            return first_only if member in mentioned_candidates else []
    first_pending = next(
        (
            c for c in candidates
            if any(
                msg.dispatch_mode == "first_responder_only"
                for msg in unread_messages(group, c)
            )
        ),
        None,
    )
    return first_only if member is first_pending else []


def first_responder_message_claimed(
    group: GroupDispatchContext,
    message: GroupMessage,
) -> bool:
    """Return whether a first-responder message already has a claimant."""
    mentioned = effective_mentions(group, message)
    if mentioned:
        return any(
            group.members[name].last_read_message_id >= message.id
            for name in mentioned
            if name in group.members
        )
    return any(
        member.last_read_message_id >= message.id
        for member in group.members.values()
    )


__all__ = [
    "directed_auto_pass_messages",
    "dispatchable_unread_messages",
    "effective_mentions",
    "first_responder_message_claimed",
    "is_directed_message",
    "is_directed_to_member",
    "next_member",
    "unread_messages",
]
