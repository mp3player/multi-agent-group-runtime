"""Group dispatch orchestration.

``GroupDispatchLoop`` will own sync and async dispatch loops, including member
selection, synthetic PASS execution, dispatch limit handling, callbacks, and
turn statistics.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from domain.group import GroupMember, GroupMessage, GroupTurn
from core.group_runtime.ports import GroupDispatchRuntimePort


@dataclass(slots=True)
class GroupDispatchLoop:
    """Sync and async dispatch loops for one group runtime."""

    max_dispatch_rounds: int = 100

    def dispatch(
        self,
        group: GroupDispatchRuntimePort,
        *,
        speakers: Iterable[str] | None = None,
        max_rounds: int | None = None,
        on_turn: Callable[[GroupTurn], None] | None = None,
        on_member_start: Callable[[GroupMember], None] | None = None,
        skip_run_guard: bool = False,
        deadline: float | None = None,
    ) -> list[GroupTurn]:
        """Dispatch unread propagating messages until all members are caught up."""
        if not skip_run_guard:
            group.dispatch_enter_run()
            deadline = group.dispatch_deadline()
        try:
            selected = group.dispatch_select_speakers(speakers)
            max_steps = group.max_dispatch_rounds if max_rounds is None else max_rounds
            max_steps = max(0, max_steps)
            turns: list[GroupTurn] = []
            launched = 0
            failed_members: set[str] = set()
            while launched < max_steps:
                group.dispatch_check_deadline(deadline)
                candidates = [
                    member for member in selected
                    if member.name not in failed_members
                ]
                group.dispatch_synthetic_pass_unread(candidates)
                member = group.dispatch_next_member(candidates)
                if member is None:
                    break
                group.round_index += 1
                launched += 1
                if on_member_start is not None:
                    on_member_start(member)
                assigned_unread = group.dispatchable_unread_for(
                    member,
                    candidates,
                )
                try:
                    turn = group.dispatch_run_member_turn(member, assigned_unread)
                except Exception as e:
                    failed_members.add(member.name)
                    turn = group.dispatch_record_member_error(member, e)
                turns.append(turn)
                group.dispatch_record_turn_stats(turn)
                if on_turn is not None:
                    on_turn(turn)
                group.dispatch_check_deadline(deadline)
            remaining = [
                member for member in selected
                if member.name not in failed_members
            ]
            group.dispatch_synthetic_pass_unread(remaining)
            if launched >= max_steps and group.dispatch_next_member(remaining) is not None:
                turn = group.dispatch_record_limit(max_steps, remaining)
                turns.append(turn)
                if on_turn is not None:
                    on_turn(turn)
            group.dispatch_record_user_steps(turns)
            return turns
        finally:
            if not skip_run_guard:
                group.dispatch_exit_run()

    async def adispatch(
        self,
        group: GroupDispatchRuntimePort,
        *,
        speakers: Iterable[str] | None = None,
        max_rounds: int | None = None,
        on_turn: Callable[[GroupTurn], None] | None = None,
        on_member_start: Callable[[GroupMember], None] | None = None,
        skip_run_guard: bool = False,
    ) -> list[GroupTurn]:
        """Async dispatch unread messages while allowing concurrent members."""
        if not skip_run_guard:
            group.dispatch_enter_run()
        running: dict[asyncio.Task[GroupTurn], GroupMember] = {}
        try:
            selected = group.dispatch_select_speakers(speakers)
            max_steps = group.max_dispatch_rounds if max_rounds is None else max_rounds
            max_steps = max(0, max_steps)
            turns: list[GroupTurn] = []
            launched = 0
            failed_members: set[str] = set()
            event = group.dispatch_event() or asyncio.Event()
            event.clear()

            while running or launched < max_steps:
                candidates = [
                    member for member in selected
                    if member.name not in failed_members
                ]
                group.dispatch_synthetic_pass_unread(candidates)
                member = (
                    group.dispatch_next_member(candidates)
                    if launched < max_steps else None
                )
                if member is not None:
                    group.round_index += 1
                    if on_member_start is not None:
                        on_member_start(member)
                    assigned_unread = group.dispatchable_unread_for(
                        member,
                        candidates,
                    )
                    member.status = "running"
                    task = asyncio.create_task(
                        group.dispatch_arun_member_turn(member, assigned_unread)
                    )
                    running[task] = member
                    launched += 1

                if not running:
                    break

                wait_set: set[asyncio.Task | asyncio.Future] = set(running)
                event_task = asyncio.create_task(event.wait())
                wait_set.add(event_task)
                done, _ = await asyncio.wait(
                    wait_set,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if event_task in done:
                    event.clear()
                else:
                    event_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await event_task

                for task in [t for t in done if t in running]:
                    member = running.pop(task)
                    try:
                        turn = task.result()
                    except Exception as e:
                        failed_members.add(member.name)
                        turn = group.dispatch_record_member_error(member, e)
                    finally:
                        member.status = "idle"
                        member.agent.reset_active_to_system()
                        group.dispatch_wake()
                    turns.append(turn)
                    group.dispatch_record_turn_stats(turn)
                    if on_turn is not None:
                        on_turn(turn)

            if running:
                done, _ = await asyncio.wait(set(running))
                for task in done:
                    info = running.pop(task, None)
                    try:
                        turn = task.result()
                    except Exception as e:
                        if info is None:
                            raise
                        member = info
                        failed_members.add(member.name)
                        turn = group.dispatch_record_member_error(member, e)
                    finally:
                        if info is not None:
                            member.status = "idle"
                            member.agent.reset_active_to_system()
                            group.dispatch_wake()
                    turns.append(turn)
                    group.dispatch_record_turn_stats(turn)
                    if on_turn is not None:
                        on_turn(turn)
            remaining = [
                member for member in selected
                if member.name not in failed_members
            ]
            group.dispatch_synthetic_pass_unread(remaining)
            if launched >= max_steps and group.dispatch_next_member(remaining) is not None:
                turn = group.dispatch_record_limit(max_steps, remaining)
                turns.append(turn)
                if on_turn is not None:
                    on_turn(turn)
            group.dispatch_record_user_steps(turns)
            return turns
        finally:
            if running:
                for task in running:
                    task.cancel()
                await asyncio.gather(*running.keys(), return_exceptions=True)
                for member in running.values():
                    member.status = "idle"
                    member.agent.reset_active_to_system()
                running.clear()
            if not skip_run_guard:
                group.dispatch_exit_run()
