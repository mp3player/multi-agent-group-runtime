"""Multi-agent group chat runtime.

User messages are routed to a first responder to avoid duplicate execution.
Agent group messages then drive an event-based dispatch loop, so idle members
can continue, coordinate, or assist while each Agent keeps its own session,
system prompt, tools, and permissions.
"""

import asyncio
from typing import Callable, Iterable

from core.agent import Agent
from core.config import MASConfig
from domain.group_events import GroupEventLog
from domain.group_memory import GroupMemory
from core.group_policy.policies import DefaultGroupDispatchPolicy
from core.group_policy.types import GroupDispatchPolicy
from domain.group import (
    DispatchMode,
    GroupMember,
    GroupMessage,
    GroupTurn,
    MessageKind,
)
from domain.group_stats import GroupStats
from core.group_runtime import (
    GroupContextBuilder,
    GroupDispatchLoop,
    GroupDispatchRuntimeAdapter,
    GroupMemberLifecycle,
    GroupMemberStore,
    GroupMemoryRuntime,
    GroupMessageRuntime,
    GroupMessageStore,
    GroupRunState,
    GroupSyntheticPassRuntimeAdapter,
    GroupSyntheticPassService,
    GroupTurnRuntimeAdapter,
)
from core.group_turns import (
    run_member_turn,
)


class GroupChatError(RuntimeError):
    """GroupChat related error."""


class GroupTimeoutError(TimeoutError):
    """Raised when one group run exceeds its configured wall-clock timeout."""


class GroupChat:
    """Coordinate several persistent Agents through a shared group transcript.

    The group owns only shared messages and scheduling. Each member owns its
    memory through its Agent.session and owns its permissions through its
    Agent.registry.
    """

    def __init__(
        self,
        name: str = "group",
        *,
        members: Iterable[GroupMember] | None = None,
        transcript_limit: int | None = 40,
        recent_context_limit: int | None = 8,
        message_store_limit: int | None = 1000,
        max_dispatch_rounds: int = 100,
        run_timeout: float | None = MASConfig.GROUP_RUN_TIMEOUT,
        dispatch_policy: GroupDispatchPolicy | None = None,
    ) -> None:
        self.name = name
        self.message_store = GroupMessageStore(
            transcript_limit=transcript_limit,
            message_store_limit=message_store_limit,
        )
        self.context_builder = GroupContextBuilder(
            recent_context_limit=recent_context_limit,
        )
        self.dispatch_loop = GroupDispatchLoop(max_dispatch_rounds=max_dispatch_rounds)
        self.dispatch_runtime = GroupDispatchRuntimeAdapter(self)
        self.turn_runtime = GroupTurnRuntimeAdapter(self)
        self.message_runtime = GroupMessageRuntime()
        self.memory_runtime = GroupMemoryRuntime()
        self.synthetic_pass_service = GroupSyntheticPassService()
        self.synthetic_pass_runtime = GroupSyntheticPassRuntimeAdapter(self)
        self.transcript_limit = transcript_limit
        self.recent_context_limit = recent_context_limit
        self.message_store_limit = message_store_limit
        self.max_dispatch_rounds = max_dispatch_rounds
        self.run_state = GroupRunState(run_timeout=run_timeout)
        self.run_timeout = self.run_state.run_timeout
        self.member_store = GroupMemberStore()
        self.member_lifecycle = GroupMemberLifecycle()
        self.memory = GroupMemory()
        self.events = GroupEventLog()
        self.stats = GroupStats()
        self.dispatch_policy: GroupDispatchPolicy = (
            dispatch_policy or DefaultGroupDispatchPolicy()
        )
        self.round_index = 0
        self._dispatch_event: asyncio.Event | None = None
        self._message_callback: Callable[[GroupMessage], None] | None = None
        self._turn_start_message_id_by_member: dict[str, int] = {}
        if members:
            for member in members:
                self.add_member(member)

    @property
    def error_type(self) -> type[GroupChatError]:
        """Error type used by runtime components for facade-compatible errors."""
        return GroupChatError

    @property
    def messages(self) -> list[GroupMessage]:
        """Shared group transcript messages."""
        return self.message_store.messages

    @property
    def members(self):
        """Group members keyed by name in scheduling order."""
        return self.member_store.members

    # ----- Member management -----

    def add_member(
        self,
        member: GroupMember | None = None,
        *,
        name: str | None = None,
        agent: Agent | None = None,
        description: str = "",
        enabled: bool = True,
    ) -> GroupMember:
        """Add a member and return the stored GroupMember."""
        self._ensure_not_running("添加成员")
        if member is None:
            if name is None or agent is None:
                raise GroupChatError("添加成员需要提供 member，或同时提供 name 和 agent")
            member = GroupMember(
                name=name,
                agent=agent,
                description=description,
                enabled=enabled,
            )
        error = self.member_store.validate_new_member(member)
        if error is not None:
            raise GroupChatError(error)
        binding_error = self.member_lifecycle.validate_available(self, member)
        if binding_error is not None:
            raise GroupChatError(binding_error)
        self.member_store.add(member)
        try:
            self.member_lifecycle.activate(self, self.name, member)
        except Exception:
            self.member_store.remove(member.name)
            raise
        return member

    def remove_member(self, name: str) -> GroupMember:
        """Remove and return a member."""
        self._ensure_not_running("移除成员")
        member = self.member_store.remove(name)
        if member is None:
            raise GroupChatError(f"成员不存在: {name}")
        self.member_lifecycle.deactivate(self, member)
        return member

    def get_member(self, name: str) -> GroupMember:
        """Return a member by name."""
        return self.member_store.require(name, error_type=GroupChatError)

    def enabled_members(self) -> list[GroupMember]:
        """Return enabled members in scheduling order."""
        return self.member_store.enabled()

    def unread_messages(self, member_or_name: GroupMember | str) -> list[GroupMessage]:
        """Return propagating messages unread by one member."""
        member = self._resolve_member(member_or_name)
        return self._unread_messages(member)

    def dispatchable_messages(
        self,
        member_or_name: GroupMember | str,
    ) -> list[GroupMessage]:
        """Return unread messages currently dispatchable to one member."""
        member = self._resolve_member(member_or_name)
        return self._dispatchable_unread_messages(member, self.enabled_members())

    def member_unread_count(self, member_or_name: GroupMember | str) -> int:
        """Return unread propagating message count for one member."""
        return len(self.unread_messages(member_or_name))

    def member_dispatchable_count(self, member_or_name: GroupMember | str) -> int:
        """Return currently dispatchable message count for one member."""
        return len(self.dispatchable_messages(member_or_name))

    def member_status_snapshot(
        self,
        member_or_name: GroupMember | str,
    ) -> dict[str, object]:
        """Return a stable status snapshot for tools, CLI, and Web views."""
        member = self._resolve_member(member_or_name)
        return self.member_store.status_snapshot(
            member,
            unread=self.member_unread_count(member),
            dispatchable=self.member_dispatchable_count(member),
        )

    # ----- Transcript management -----

    def add_message(
        self,
        sender: str,
        content: str,
        *,
        kind: MessageKind = "agent",
        round_index: int | None = None,
        mentions: Iterable[str] | None = None,
        propagate: bool | None = None,
        dispatch_mode: DispatchMode = "normal",
    ) -> GroupMessage:
        """Append a message to the shared transcript."""
        return self.message_runtime.add_message(
            self,
            sender,
            content,
            kind=kind,
            round_index=self.round_index if round_index is None else round_index,
            mentions=mentions,
            propagate=propagate,
            dispatch_mode=dispatch_mode,
            on_message=self._message_callback,
            wake=self._wake_dispatch,
        )

    def add_user_message(self, content: str, *, sender: str = "user") -> GroupMessage:
        """Append a user message to the shared transcript."""
        self.round_index += 1
        return self.add_message(
            sender,
            content,
            kind="user",
            dispatch_mode="first_responder_only",
        )

    def clear(self) -> None:
        """Clear shared transcript, group memory, and internal events.

        Member Agent sessions and aggregate stats are intentionally preserved.
        Use each member.agent.new_session() when a full reset is required.
        """
        self._ensure_not_running("清空群聊")
        self.message_store.clear()
        self.memory.clear()
        self.events.clear()
        self.round_index = 0
        for member in self.members.values():
            member.last_read_message_id = 0
            member.status = "idle"

    def transcript(self, *, limit: int | None = None) -> str:
        """Render the shared transcript."""
        return self.message_store.transcript(limit=limit)

    def memory_text(self, *, include_empty: bool = False) -> str:
        """Render shared group memory."""
        return self.memory.render_full(include_empty=include_empty)

    def memory_summary(self, *, limit: int = 120) -> str:
        """Render a compact group memory summary for status output."""
        return self.memory.render_summary(limit=limit)

    def update_memory(
        self,
        section: str,
        content: str,
        *,
        append: bool = False,
        actor: str = "system",
    ) -> str:
        """Update one shared memory section."""
        return self.memory_runtime.update(
            self,
            section,
            content,
            append=append,
            actor=actor,
        )

    def clear_memory(self, section: str = "", *, actor: str = "system") -> str:
        """Clear one memory section, or all sections when section is empty."""
        return self.memory_runtime.clear(self, section, actor=actor)

    def stats_report(self) -> str:
        """Render lightweight group scheduling statistics."""
        return self.stats.report()

    def record_tool_call(self, actor: str, name: str, effect: str) -> None:
        """Record a group tool call in the internal event log."""
        GroupToolRuntimeAdapter(self).tool_record_call(actor, name, effect)

    # ----- Scheduling -----

    def run(
        self,
        user_message: str,
        *,
        speakers: Iterable[str] | None = None,
        max_rounds: int | None = None,
        on_turn: Callable[[GroupTurn], None] | None = None,
        on_member_start: Callable[[GroupMember], None] | None = None,
    ) -> list[GroupTurn]:
        """Add a user message, then dispatch unread messages until idle.

        Args:
            user_message: New user message entering the group.
            speakers: Optional member names. Defaults to all enabled members in
                registration order.
            max_rounds: Maximum group dispatch rounds.
        """
        self._enter_run()
        deadline = self._deadline()
        try:
            self._select_speakers(speakers)
            self.add_user_message(user_message)
            return self.dispatch(
                speakers=speakers,
                max_rounds=max_rounds,
                on_turn=on_turn,
                on_member_start=on_member_start,
                _skip_run_guard=True,
                _deadline=deadline,
            )
        finally:
            self._exit_run()

    def dispatch(
        self,
        *,
        speakers: Iterable[str] | None = None,
        max_rounds: int | None = None,
        on_turn: Callable[[GroupTurn], None] | None = None,
        on_member_start: Callable[[GroupMember], None] | None = None,
        _skip_run_guard: bool = False,
        _deadline: float | None = None,
    ) -> list[GroupTurn]:
        """Dispatch unread propagating messages until all members are caught up."""
        return self.dispatch_loop.dispatch(
            self.dispatch_runtime,
            speakers=speakers,
            max_rounds=max_rounds,
            on_turn=on_turn,
            on_member_start=on_member_start,
            skip_run_guard=_skip_run_guard,
            deadline=_deadline,
        )

    async def arun(
        self,
        user_message: str,
        *,
        speakers: Iterable[str] | None = None,
        max_rounds: int | None = None,
        on_turn: Callable[[GroupTurn], None] | None = None,
        on_member_start: Callable[[GroupMember], None] | None = None,
        on_message: Callable[[GroupMessage], None] | None = None,
    ) -> list[GroupTurn]:
        """Async non-blocking group run.

        Multiple members may run concurrently. A member remains single-flight:
        it will not be scheduled again while its current Agent run is active.
        """
        timeout = self.run_timeout
        if timeout is not None and timeout > 0:
            try:
                return await asyncio.wait_for(
                    self._arun_unbounded(
                        user_message,
                        speakers=speakers,
                        max_rounds=max_rounds,
                        on_turn=on_turn,
                        on_member_start=on_member_start,
                        on_message=on_message,
                    ),
                    timeout,
                )
            except TimeoutError as e:
                raise GroupTimeoutError(
                    f"Group run exceeded timeout {timeout:g}s"
                ) from e
        return await self._arun_unbounded(
            user_message,
            speakers=speakers,
            max_rounds=max_rounds,
            on_turn=on_turn,
            on_member_start=on_member_start,
            on_message=on_message,
        )

    async def _arun_unbounded(
        self,
        user_message: str,
        *,
        speakers: Iterable[str] | None = None,
        max_rounds: int | None = None,
        on_turn: Callable[[GroupTurn], None] | None = None,
        on_member_start: Callable[[GroupMember], None] | None = None,
        on_message: Callable[[GroupMessage], None] | None = None,
    ) -> list[GroupTurn]:
        self._enter_run()
        previous_event = self._dispatch_event
        previous_callback = self._message_callback
        try:
            self._select_speakers(speakers)
            self._dispatch_event = asyncio.Event()
            self._message_callback = on_message
            self.add_user_message(user_message)
            return await self.adispatch(
                speakers=speakers,
                max_rounds=max_rounds,
                on_turn=on_turn,
                on_member_start=on_member_start,
                _skip_run_guard=True,
            )
        finally:
            self._dispatch_event = previous_event
            self._message_callback = previous_callback
            self._exit_run()

    async def adispatch(
        self,
        *,
        speakers: Iterable[str] | None = None,
        max_rounds: int | None = None,
        on_turn: Callable[[GroupTurn], None] | None = None,
        on_member_start: Callable[[GroupMember], None] | None = None,
        _skip_run_guard: bool = False,
    ) -> list[GroupTurn]:
        """Async dispatch unread messages while allowing concurrent members."""
        return await self.dispatch_loop.adispatch(
            self.dispatch_runtime,
            speakers=speakers,
            max_rounds=max_rounds,
            on_turn=on_turn,
            on_member_start=on_member_start,
            skip_run_guard=_skip_run_guard,
        )

    def step(self, speaker: str) -> GroupTurn:
        """Let exactly one member speak once."""
        self._enter_run()
        try:
            return self._step_unlocked(speaker)
        finally:
            self._exit_run()

    def _step_unlocked(self, speaker: str) -> GroupTurn:
        member = self.get_member(speaker)
        if not member.enabled:
            raise GroupChatError(f"成员已禁用: {speaker}")
        self.round_index += 1
        turn = run_member_turn(self.turn_runtime, member, None)
        self.stats.record_turn(turn)
        return turn

    # ----- Internals -----

    def _ensure_not_running(self, action: str) -> None:
        self.run_state.ensure_not_running(action, GroupChatError)

    def _enter_run(self) -> None:
        self.run_state.enter(GroupChatError)

    def _exit_run(self) -> None:
        self.run_state.exit()

    def _deadline(self) -> float | None:
        return self.run_state.deadline()

    def _check_deadline(self, deadline: float | None) -> None:
        self.run_state.check_deadline(deadline, GroupTimeoutError)

    def _select_speakers(self, speakers: Iterable[str] | None) -> list[GroupMember]:
        return self.member_store.select(speakers, error_type=GroupChatError)

    def _resolve_member(self, member_or_name: GroupMember | str) -> GroupMember:
        return self.member_store.resolve(member_or_name, error_type=GroupChatError)

    async def aclose_members(self) -> None:
        """Close async resources owned by member agents."""
        await self.member_lifecycle.close_members(self.members.values())

    def _unread_messages(self, member: GroupMember) -> list[GroupMessage]:
        return self.dispatch_policy.unread_messages(self, member)

    def _dispatchable_unread_messages(
        self,
        member: GroupMember,
        candidates: list[GroupMember],
    ) -> list[GroupMessage]:
        return self.dispatch_policy.dispatchable_messages(self, member, candidates)

    def _validate_mentions(self, mentions: Iterable[str]) -> list[str]:
        return self.member_store.validate_mentions(mentions)

    def validate_message_mentions(self, mentions: Iterable[str]) -> list[str]:
        """Validate and deduplicate mentions for message runtime."""
        return self._validate_mentions(mentions)

    def _wake_dispatch(self) -> None:
        if self._dispatch_event is not None:
            self._dispatch_event.set()
