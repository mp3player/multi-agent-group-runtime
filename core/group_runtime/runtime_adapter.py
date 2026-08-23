"""Runtime adapters that keep ``GroupChat`` as a thin public facade."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.group import GroupMember, GroupMessage, GroupTurn
from core.logger import get_logger

if TYPE_CHECKING:
    from core.group import GroupChat

logger = get_logger(__name__)


@dataclass(slots=True)
class GroupDispatchRuntimeAdapter:
    """Adapter implementing the dispatch-loop port for ``GroupChat``."""

    group: "GroupChat"

    @property
    def max_dispatch_rounds(self) -> int:
        return self.group.max_dispatch_rounds

    @property
    def round_index(self) -> int:
        return self.group.round_index

    @round_index.setter
    def round_index(self, value: int) -> None:
        self.group.round_index = value

    def dispatch_enter_run(self) -> None:
        self.group._enter_run()

    def dispatch_exit_run(self) -> None:
        self.group._exit_run()

    def dispatch_deadline(self) -> float | None:
        return self.group._deadline()

    def dispatch_check_deadline(self, deadline: float | None) -> None:
        self.group._check_deadline(deadline)

    def dispatch_select_speakers(
        self,
        speakers: Iterable[str] | None,
    ) -> list[GroupMember]:
        return self.group.member_store.select(
            speakers,
            error_type=self.group.error_type,
        )

    def dispatch_synthetic_pass_unread(self, members: list[GroupMember]) -> None:
        self.group.synthetic_pass_service.pass_unread(
            self.group.synthetic_pass_runtime,
            members,
        )

    def dispatch_next_member(
        self,
        members: list[GroupMember],
    ) -> GroupMember | None:
        return self.group.dispatch_policy.next_member(self.group, members)

    def dispatchable_unread_for(
        self,
        member: GroupMember,
        candidates: list[GroupMember],
    ) -> list[GroupMessage]:
        return self.group.dispatch_policy.dispatchable_messages(
            self.group,
            member,
            candidates,
        )

    def dispatch_run_member_turn(
        self,
        member: GroupMember,
        assigned_unread: list[GroupMessage] | None,
    ) -> GroupTurn:
        from core.group_turns import run_member_turn

        return run_member_turn(self.group.turn_runtime, member, assigned_unread)

    async def dispatch_arun_member_turn(
        self,
        member: GroupMember,
        assigned_unread: list[GroupMessage] | None,
    ) -> GroupTurn:
        from core.group_turns import arun_member_turn

        return await arun_member_turn(
            self.group.turn_runtime,
            member,
            assigned_unread,
        )

    def dispatch_record_member_error(
        self,
        member: GroupMember,
        error: Exception,
    ) -> GroupTurn:
        member.status = "idle"
        msg = self.group.add_message(
            sender="system",
            content=(
                f"[调度错误] {member.name} 本轮处理失败: "
                f"{type(error).__name__}: {error}"
            ),
            kind="system",
            propagate=False,
        )
        self.group.events.append(
            "dispatch_error",
            actor=member.name,
            data={"error_type": type(error).__name__, "error": str(error)},
        )
        return GroupTurn(member=member.name, message=msg, events=[msg])

    def dispatch_record_turn_stats(self, turn: GroupTurn) -> None:
        self.group.stats.record_turn(turn)

    def dispatch_record_user_steps(self, turns: list[GroupTurn]) -> None:
        self.group.stats.record_user_dispatch_steps(turns)

    def dispatch_record_limit(
        self,
        max_steps: int | None,
        members: list[GroupMember],
    ) -> GroupTurn:
        propagated_ids = [msg.id for msg in self.group.messages if msg.propagate]
        if propagated_ids:
            read_through = max(propagated_ids)
            for member in members:
                member.last_read_message_id = max(
                    member.last_read_message_id,
                    read_through,
                )
        msg = self.group.add_message(
            sender="system",
            content=(
                f"[调度停止] 已达到最大调度步数 {max_steps}，"
                "可能仍有可调度的未读群组消息。"
            ),
            kind="system",
            propagate=False,
        )
        self.group.stats.record_dispatch_limit_hit()
        self.group.events.append(
            "dispatch_limit",
            actor="system",
            data={"max_steps": max_steps},
        )
        return GroupTurn(member="system", message=msg, events=[msg])

    def dispatch_event(self) -> asyncio.Event | None:
        return self.group._dispatch_event

    def dispatch_wake(self) -> None:
        if self.group._dispatch_event is not None:
            self.group._dispatch_event.set()


@dataclass(slots=True)
class GroupTurnRuntimeAdapter:
    """Adapter implementing the turn-runtime port for ``GroupChat``."""

    group: "GroupChat"

    @property
    def messages(self) -> list[GroupMessage]:
        return self.group.messages

    def turn_was_directed_to_member(
        self,
        member: GroupMember,
        messages: list[GroupMessage],
    ) -> bool:
        return self.group.dispatch_policy.was_directed_to_member(
            self.group,
            member,
            messages,
        )

    def turn_next_message_id(self) -> int:
        return self.group.message_store.next_message_id

    def turn_begin(self, member: GroupMember, before_message_id: int) -> None:
        self.group._turn_start_message_id_by_member[member.name] = before_message_id

    def turn_end(self, member: GroupMember) -> None:
        self.group._turn_start_message_id_by_member.pop(member.name, None)

    def turn_build_prompt(
        self,
        member: GroupMember,
        unread: list[GroupMessage],
    ) -> str:
        return self.group.context_builder.build_member_prompt(
            self.group.messages,
            unread,
        )

    def turn_dispatchable_unread(self, member: GroupMember) -> list[GroupMessage]:
        return self.group.dispatch_policy.dispatchable_messages(
            self.group,
            member,
            self.group.enabled_members(),
        )

    def turn_add_message(
        self,
        sender: str,
        content: str,
        **kwargs: object,
    ) -> GroupMessage:
        return self.group.add_message(sender=sender, content=content, **kwargs)

    def turn_record_stale_response(self) -> None:
        self.group.stats.record_stale_response()


@dataclass(slots=True)
class GroupSyntheticPassRuntimeAdapter:
    """Adapter implementing the synthetic-pass runtime port for ``GroupChat``."""

    group: "GroupChat"

    def synthetic_pass_messages(
        self,
        member: GroupMember,
    ) -> list[GroupMessage]:
        return self.group.dispatch_policy.synthetic_pass_messages(self.group, member)

    def synthetic_pass_run_member_turn(
        self,
        member: GroupMember,
        unread: list[GroupMessage],
    ) -> list[GroupMessage]:
        from core.group_turns import synthetic_pass_member_turn

        return synthetic_pass_member_turn(self.group.turn_runtime, member, unread)

    def synthetic_pass_record(
        self,
        member: GroupMember,
        unread: list[GroupMessage],
        messages: list[GroupMessage],
    ) -> None:
        self.group.stats.record_synthetic_pass()
        logger.info(
            "synthetic_pass member=%s unread=%s pass=%s",
            member.name,
            [msg.id for msg in unread],
            [msg.id for msg in messages],
        )
        self.group.events.append(
            "synthetic_pass",
            actor=member.name,
            data={
                "unread_message_ids": [msg.id for msg in unread],
                "pass_message_ids": [msg.id for msg in messages],
            },
        )


@dataclass(slots=True)
class GroupToolRuntimeAdapter:
    """Adapter implementing the group-tool runtime port for ``GroupChat``."""

    group: "GroupChat"

    @property
    def name(self) -> str:
        return self.group.name

    def tool_record_call(self, actor: str, name: str, effect: str) -> None:
        self.group.events.append(
            "tool_call",
            actor=actor,
            data={"tool": name, "effect": effect},
        )

    def tool_member_status_lines(self) -> list[str]:
        lines: list[str] = []
        for member in self.group.members.values():
            snapshot = self.group.member_status_snapshot(member)
            lines.append(
                f"- {snapshot['name']}: status={snapshot['status']}, "
                f"enabled={snapshot['enabled']}, unread={snapshot['unread']}, "
                f"dispatchable={snapshot['dispatchable']}, "
                f"description={snapshot['description'] or '无'}"
            )
        return lines

    def tool_recent_messages(self, limit: int) -> list[GroupMessage]:
        return self.group.messages[-limit:]

    def tool_member_messages_after(
        self,
        member_name: str,
        message_id: int,
    ) -> list[GroupMessage]:
        return [
            msg for msg in self.group.messages
            if msg.sender == member_name and msg.id > message_id
        ]

    def tool_memory_summary(self) -> str:
        return self.group.memory.render_summary()

    def tool_memory_full(self, *, include_empty: bool = False) -> str:
        return self.group.memory.render_full(include_empty=include_empty)

    def tool_add_message(
        self,
        sender: str,
        content: str,
        **kwargs: object,
    ) -> GroupMessage:
        return self.group.add_message(sender=sender, content=content, **kwargs)

    def tool_has_member(self, name: str) -> bool:
        return name in self.group.members

    def tool_member_enabled(self, name: str) -> bool:
        member = self.group.members.get(name)
        return bool(member and member.enabled)

    def tool_update_memory(
        self,
        section: str,
        content: str,
        *,
        append: bool = False,
        actor: str = "system",
    ) -> str:
        return self.group.update_memory(section, content, append=append, actor=actor)

    def tool_clear_memory(self, section: str = "", *, actor: str = "system") -> str:
        return self.group.clear_memory(section, actor=actor)

    def tool_turn_start_message_id(self, member_name: str) -> int | None:
        return self.group._turn_start_message_id_by_member.get(member_name)
