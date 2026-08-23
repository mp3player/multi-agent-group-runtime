"""Lightweight group chat scheduling statistics."""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.group import GroupMessage, GroupTurn


@dataclass(slots=True)
class GroupStats:
    """Counters used to inspect scheduling quality."""

    propagating_messages: int = 0
    pass_messages: int = 0
    synthetic_passes: int = 0
    stale_responses: int = 0
    dispatch_limit_hits: int = 0
    member_turns: dict[str, int] = field(default_factory=dict)
    user_dispatch_steps: list[int] = field(default_factory=list)

    def record_message(self, msg: GroupMessage, *, is_pass: bool) -> None:
        if msg.propagate:
            self.propagating_messages += 1
        if is_pass:
            self.pass_messages += 1

    def record_turn(self, turn: GroupTurn) -> None:
        if turn.member == "system":
            return
        self.member_turns[turn.member] = self.member_turns.get(turn.member, 0) + 1

    def record_user_dispatch_steps(self, turns: list[GroupTurn]) -> None:
        count = sum(1 for turn in turns if turn.member != "system")
        self.user_dispatch_steps.append(count)

    def record_stale_response(self) -> None:
        self.stale_responses += 1

    def record_synthetic_pass(self) -> None:
        self.synthetic_passes += 1

    def record_dispatch_limit_hit(self) -> None:
        self.dispatch_limit_hits += 1

    def report(self) -> str:
        lines = [
            f"propagating_messages={self.propagating_messages}",
            f"pass_messages={self.pass_messages}",
            f"synthetic_passes={self.synthetic_passes}",
            f"stale_responses={self.stale_responses}",
            f"dispatch_limit_hits={self.dispatch_limit_hits}",
        ]
        if self.member_turns:
            turns = ", ".join(
                f"{name}={count}"
                for name, count in sorted(self.member_turns.items())
            )
            lines.append(f"member_turns: {turns}")
        if self.user_dispatch_steps:
            steps = ", ".join(str(value) for value in self.user_dispatch_steps[-20:])
            lines.append(f"user_dispatch_steps(last20): {steps}")
        return "\n".join(lines)
