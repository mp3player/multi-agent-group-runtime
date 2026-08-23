"""Protocol boundaries for group runtime components.

The protocols intentionally describe only the broad capabilities needed by the
runtime components. They remain small by design so components depend on narrow
capabilities instead of the full ``GroupChat`` facade.
"""

from __future__ import annotations

from collections.abc import Iterable
import asyncio
from typing import Protocol

from domain.group import GroupMember, GroupMessage, GroupTurn


class GroupMessagePort(Protocol):
    """Message operations needed by future runtime components."""

    messages: list[GroupMessage]

    def add_message(self, sender: str, content: str, **kwargs: object) -> GroupMessage:
        """Append a message to the shared transcript."""
        ...

    def transcript(self, *, limit: int | None = None) -> str:
        """Render group transcript text."""
        ...


class GroupMessageRuntimePort(Protocol):
    """Capabilities required by message side-effect runtime."""

    message_store: object
    stats: object
    events: object

    def validate_message_mentions(self, mentions: Iterable[str]) -> list[str]:
        """Validate and deduplicate message mentions."""
        ...

class GroupMemoryRuntimePort(Protocol):
    """Capabilities required by memory side-effect runtime."""

    memory: object
    events: object


class GroupMemberPort(Protocol):
    """Member operations needed by future runtime components."""

    members: dict[str, GroupMember]

    def get_member(self, name: str) -> GroupMember:
        """Return one member by name."""
        ...

    def enabled_members(self) -> list[GroupMember]:
        """Return enabled members in scheduling order."""
        ...


class GroupContextPort(Protocol):
    """Context operations needed by future turn execution."""

    def unread_messages(self, member_or_name: GroupMember | str) -> list[GroupMessage]:
        """Return unread propagating messages for one member."""
        ...

    def dispatchable_messages(
        self,
        member_or_name: GroupMember | str,
    ) -> list[GroupMessage]:
        """Return unread messages currently dispatchable to one member."""
        ...


class GroupDispatchPort(Protocol):
    """Dispatch operations needed by future runtime orchestration."""

    def dispatch(
        self,
        *,
        speakers: Iterable[str] | None = None,
        max_rounds: int | None = None,
    ) -> list[GroupTurn]:
        """Dispatch unread messages until idle or limited."""
        ...


class GroupDispatchRuntimePort(Protocol):
    """Capabilities required by ``GroupDispatchLoop``."""

    max_dispatch_rounds: int
    round_index: int

    def dispatch_enter_run(self) -> None:
        """Enter protected group run state."""
        ...

    def dispatch_exit_run(self) -> None:
        """Exit protected group run state."""
        ...

    def dispatch_deadline(self) -> float | None:
        """Return the current run deadline."""
        ...

    def dispatch_check_deadline(self, deadline: float | None) -> None:
        """Raise if the current run exceeded its deadline."""
        ...

    def dispatch_select_speakers(
        self,
        speakers: Iterable[str] | None,
    ) -> list[GroupMember]:
        """Resolve selected speakers into enabled group members."""
        ...

    def dispatch_synthetic_pass_unread(self, members: list[GroupMember]) -> None:
        """Synthetic-pass unread messages that should not run a model turn."""
        ...

    def dispatch_next_member(
        self,
        members: list[GroupMember],
    ) -> GroupMember | None:
        """Return the next member to run."""
        ...

    def dispatchable_unread_for(
        self,
        member: GroupMember,
        candidates: list[GroupMember],
    ) -> list[GroupMessage]:
        """Return unread messages assigned to one dispatch turn."""
        ...

    def dispatch_run_member_turn(
        self,
        member: GroupMember,
        assigned_unread: list[GroupMessage] | None,
    ) -> GroupTurn:
        """Run one sync member turn."""
        ...

    async def dispatch_arun_member_turn(
        self,
        member: GroupMember,
        assigned_unread: list[GroupMessage] | None,
    ) -> GroupTurn:
        """Run one async member turn."""
        ...

    def dispatch_record_member_error(
        self,
        member: GroupMember,
        error: Exception,
    ) -> GroupTurn:
        """Record a failed member turn."""
        ...

    def dispatch_record_turn_stats(self, turn: GroupTurn) -> None:
        """Record turn statistics."""
        ...

    def dispatch_record_user_steps(self, turns: list[GroupTurn]) -> None:
        """Record total dispatch steps for one user message."""
        ...

    def dispatch_record_limit(
        self,
        max_steps: int | None,
        members: list[GroupMember],
    ) -> GroupTurn:
        """Record dispatch limit exhaustion."""
        ...

    def dispatch_event(self) -> asyncio.Event | None:
        """Return the current async dispatch wake event, if any."""
        ...

    def dispatch_wake(self) -> None:
        """Wake async dispatch waiters when possible."""
        ...


class GroupTurnRuntimePort(Protocol):
    """Capabilities required by member turn execution helpers."""

    messages: list[GroupMessage]

    def turn_was_directed_to_member(
        self,
        member: GroupMember,
        messages: list[GroupMessage],
    ) -> bool:
        """Return whether this turn was explicitly directed to the member."""
        ...

    def turn_next_message_id(self) -> int:
        """Return the next message id that will be assigned."""
        ...

    def turn_begin(self, member: GroupMember, before_message_id: int) -> None:
        """Mark the beginning of one member turn."""
        ...

    def turn_end(self, member: GroupMember) -> None:
        """Mark the end of one member turn."""
        ...

    def turn_build_prompt(
        self,
        member: GroupMember,
        unread: list[GroupMessage],
    ) -> str:
        """Build the prompt for one member turn."""
        ...

    def turn_dispatchable_unread(self, member: GroupMember) -> list[GroupMessage]:
        """Return default dispatchable unread messages for one member."""
        ...

    def turn_add_message(
        self,
        sender: str,
        content: str,
        **kwargs: object,
    ) -> GroupMessage:
        """Append a message during turn processing."""
        ...

    def turn_record_stale_response(self) -> None:
        """Record one stale response converted to PASS."""
        ...


class GroupSyntheticPassRuntimePort(Protocol):
    """Capabilities required by synthetic PASS execution."""

    def synthetic_pass_messages(
        self,
        member: GroupMember,
    ) -> list[GroupMessage]:
        """Return unread messages that should be synthetic-passed."""
        ...

    def synthetic_pass_run_member_turn(
        self,
        member: GroupMember,
        unread: list[GroupMessage],
    ) -> list[GroupMessage]:
        """Record one synthetic PASS through the normal group_pass tool path."""
        ...

    def synthetic_pass_record(
        self,
        member: GroupMember,
        unread: list[GroupMessage],
        messages: list[GroupMessage],
    ) -> None:
        """Record synthetic PASS stats and events."""
        ...


class GroupToolRuntimePort(Protocol):
    """Capabilities required by group tool handlers."""

    name: str

    def tool_record_call(self, actor: str, name: str, effect: str) -> None:
        """Record one group tool call."""
        ...

    def tool_member_status_lines(self) -> list[str]:
        """Return rendered member status lines."""
        ...

    def tool_recent_messages(self, limit: int) -> list[GroupMessage]:
        """Return recent group messages."""
        ...

    def tool_member_messages_after(
        self,
        member_name: str,
        message_id: int,
    ) -> list[GroupMessage]:
        """Return messages from one member after a message id."""
        ...

    def tool_memory_summary(self) -> str:
        """Return compact group memory summary."""
        ...

    def tool_memory_full(self, *, include_empty: bool = False) -> str:
        """Return full group memory text."""
        ...

    def tool_add_message(
        self,
        sender: str,
        content: str,
        **kwargs: object,
    ) -> GroupMessage:
        """Append a group message from a tool."""
        ...

    def tool_has_member(self, name: str) -> bool:
        """Return whether a member exists."""
        ...

    def tool_member_enabled(self, name: str) -> bool:
        """Return whether a member is enabled."""
        ...

    def tool_update_memory(
        self,
        section: str,
        content: str,
        *,
        append: bool = False,
        actor: str = "system",
    ) -> str:
        """Update group memory."""
        ...

    def tool_clear_memory(self, section: str = "", *, actor: str = "system") -> str:
        """Clear group memory."""
        ...

    def tool_turn_start_message_id(self, member_name: str) -> int | None:
        """Return the current turn start message id for one member."""
        ...


class GroupRuntimePort(
    GroupMessagePort,
    GroupMemberPort,
    GroupContextPort,
    GroupDispatchPort,
    Protocol,
):
    """Combined future runtime surface."""
