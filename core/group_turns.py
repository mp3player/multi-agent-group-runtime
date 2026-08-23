"""Member turn execution for group chat scheduling."""

from __future__ import annotations

from domain.group import GroupMember, GroupMessage, GroupTurn
from core.group_tools import GROUP_TOOL_NAMES
from core.group_runtime.ports import GroupTurnRuntimePort
from models import AI, Message, ToolCall, User
from tools.registry import ToolCallError


def run_member_turn(
    group: GroupTurnRuntimePort,
    member: GroupMember,
    assigned_unread: list[GroupMessage] | None,
) -> GroupTurn:
    unread = _assigned_or_dispatchable(group, member, assigned_unread)
    was_directed = group.turn_was_directed_to_member(member, unread)
    read_through = _read_through(member, unread)
    before_message_id = group.turn_next_message_id() - 1
    before_history_len = member_history_len(member)
    group.turn_begin(member, before_message_id)
    member.status = "running"
    try:
        prompt = group.turn_build_prompt(member, unread)
        response = member.agent.run(prompt).strip()
        member.last_read_message_id = max(member.last_read_message_id, read_through)
    finally:
        group.turn_end(member)
        member.status = "idle"
        member.agent.reset_active_to_system()
    return _turn_from_response(
        group,
        member,
        unread,
        was_directed,
        before_message_id,
        before_history_len,
        response,
    )


async def arun_member_turn(
    group: GroupTurnRuntimePort,
    member: GroupMember,
    assigned_unread: list[GroupMessage] | None = None,
) -> GroupTurn:
    """Run one async member turn.

    The async dispatcher owns member.status cleanup so a completed task is not
    considered idle before adispatch consumes its result.
    """
    unread = _assigned_or_dispatchable(group, member, assigned_unread)
    was_directed = group.turn_was_directed_to_member(member, unread)
    read_through = _read_through(member, unread)
    before_message_id = group.turn_next_message_id() - 1
    before_history_len = member_history_len(member)
    group.turn_begin(member, before_message_id)
    member.status = "running"
    try:
        prompt = group.turn_build_prompt(member, unread)
        response = (await member.agent.arun(prompt)).strip()
        member.last_read_message_id = max(member.last_read_message_id, read_through)
    finally:
        group.turn_end(member)
    return _turn_from_response(
        group,
        member,
        unread,
        was_directed,
        before_message_id,
        before_history_len,
        response,
    )


def synthetic_pass_member_turn(
    group: GroupTurnRuntimePort,
    member: GroupMember,
    unread: list[GroupMessage],
) -> list[GroupMessage]:
    """Record a scheduler-generated pass using the real group_pass tool path."""
    if not unread:
        return []
    read_through = _read_through(member, unread)
    before_message_id = group.turn_next_message_id() - 1
    prompt = group.turn_build_prompt(member, unread)
    tool_call = ToolCall(
        id=f"synthetic_group_pass_{member.name}_{read_through}",
        name="group_pass",
        arguments={},
    )
    session = getattr(member.agent, "session", None)
    if session is not None:
        session.add(User(prompt))
        session.add(AI(message="", tool_calls=[tool_call]))
    group.turn_begin(member, before_message_id)
    try:
        result = member.agent.registry.call_tool_call(tool_call)
    finally:
        group.turn_end(member)
    result_str = str(result) if isinstance(result, ToolCallError) else (
        str(result) if result is not None else "(无返回值)"
    )
    if session is not None:
        tool_msg = Message(role="tool", message=result_str)
        tool_msg.tool_call_id = tool_call.id  # type: ignore[attr-defined]
        session.add(tool_msg)
    member.last_read_message_id = max(member.last_read_message_id, read_through)
    member.agent.reset_active_to_system()
    return [
        msg for msg in group.messages
        if msg.sender == member.name and msg.id > before_message_id
    ]


def _assigned_or_dispatchable(
    group: GroupTurnRuntimePort,
    member: GroupMember,
    assigned_unread: list[GroupMessage] | None,
) -> list[GroupMessage]:
    if assigned_unread is not None:
        return assigned_unread
    return group.turn_dispatchable_unread(member)


def _read_through(member: GroupMember, unread: list[GroupMessage]) -> int:
    if unread:
        return max(msg.id for msg in unread)
    return member.last_read_message_id


def _turn_from_response(
    group: GroupTurnRuntimePort,
    member: GroupMember,
    unread: list[GroupMessage],
    was_directed: bool,
    before_message_id: int,
    before_history_len: int,
    response: str,
) -> GroupTurn:
    new_tool_messages = [
        msg for msg in group.messages
        if msg.sender == member.name and msg.id > before_message_id
    ]
    if new_tool_messages:
        visible_tool_messages = visible_turn_events(new_tool_messages)
        if has_nonpropagating_pass(new_tool_messages):
            return GroupTurn(
                member=member.name,
                message=visible_tool_messages[-1],
                events=visible_tool_messages,
            )
        if response and should_propagate_final_response(member, before_history_len):
            msg = group.turn_add_message(
                sender=member.name,
                content=response,
                kind="agent",
            )
            return GroupTurn(
                member=member.name,
                message=msg,
                events=[*visible_tool_messages, msg],
            )
        return GroupTurn(
            member=member.name,
            message=visible_tool_messages[-1],
            events=visible_tool_messages,
        )
    if not response:
        response = (
            "[空回复：收到定向请求但未给出有效响应]"
            if was_directed else "[空回复]"
        )
        msg = group.turn_add_message(
            sender=member.name,
            content=response,
            kind="agent",
            propagate=False,
        )
        return GroupTurn(member=member.name, message=msg, events=[msg])
    if (
        has_newer_propagating_messages(group, member, before_message_id, unread)
        and not should_propagate_final_response(member, before_history_len)
    ):
        group.turn_record_stale_response()
        msg = group.turn_add_message(
            sender=member.name,
            content="PASS",
            kind="agent",
            propagate=False,
        )
        return GroupTurn(member=member.name, message=msg, events=[msg])
    msg = group.turn_add_message(
        sender=member.name,
        content=response,
        kind="agent",
    )
    events = [*new_tool_messages, msg]
    return GroupTurn(member=member.name, message=msg, events=events)


def member_history_len(member: GroupMember) -> int:
    session = getattr(member.agent, "session", None)
    history = getattr(session, "history", None)
    return len(history) if history is not None else 0


def should_propagate_final_response(
    member: GroupMember,
    before_history_len: int,
) -> bool:
    """Allow final text after group tools only if real work also happened."""
    session = getattr(member.agent, "session", None)
    history = getattr(session, "history", None)
    if history is None:
        return False
    for message in history[before_history_len:]:
        tool_calls = getattr(message, "tool_calls", None) or []
        for tool_call in tool_calls:
            if tool_call.name not in GROUP_TOOL_NAMES:
                return True
    return False


def has_newer_propagating_messages(
    group: GroupTurnRuntimePort,
    member: GroupMember,
    before_message_id: int,
    unread: list[GroupMessage],
) -> bool:
    unread_senders = {msg.sender for msg in unread}
    return any(
        msg.propagate
        and msg.sender != member.name
        and msg.sender not in unread_senders
        and msg.id > before_message_id
        for msg in group.messages
    )


def is_pass(content: str) -> bool:
    normalized = content.strip().strip("`").strip()
    return normalized.upper() == "PASS" or normalized == "group_pass()"


def has_nonpropagating_pass(messages: list[GroupMessage]) -> bool:
    return any(is_pass(msg.content) and not msg.propagate for msg in messages)


def visible_turn_events(messages: list[GroupMessage]) -> list[GroupMessage]:
    if any(msg.propagate for msg in messages):
        visible = [
            msg for msg in messages
            if msg.propagate or not is_pass(msg.content)
        ]
        return visible or messages
    return messages
