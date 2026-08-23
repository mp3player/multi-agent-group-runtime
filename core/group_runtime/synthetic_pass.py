"""Synthetic PASS orchestration for group dispatch."""

from __future__ import annotations

from dataclasses import dataclass

from domain.group import GroupMember
from core.group_runtime.ports import GroupSyntheticPassRuntimePort


@dataclass(slots=True)
class GroupSyntheticPassService:
    """Synthetic-pass unread messages that should not run a model turn."""

    def pass_unread(
        self,
        group: GroupSyntheticPassRuntimePort,
        members: list[GroupMember],
    ) -> None:
        """Record synthetic PASS turns for eligible idle members."""
        for member in members:
            if not member.enabled or member.status != "idle":
                continue
            unread = group.synthetic_pass_messages(member)
            if not unread:
                continue
            messages = group.synthetic_pass_run_member_turn(member, unread)
            group.synthetic_pass_record(member, unread, messages)
