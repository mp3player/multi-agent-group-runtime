"""Shared state types for multi-agent group chat."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


MessageKind = Literal["user", "agent", "system"]
MemberStatus = Literal["idle", "running"]
DispatchMode = Literal["first_responder_only", "normal", "broadcast", "feedback"]


class AgentLike(Protocol):
    """Minimum agent surface required by group runtime code."""

    registry: Any
    system_builder: Any

    def rebuild_system_prompt(self) -> None:
        ...

    def reset_active_to_system(self) -> None:
        ...


@dataclass(slots=True)
class GroupMessage:
    """A message in the shared group transcript."""

    id: int
    sender: str
    content: str
    kind: MessageKind = "agent"
    round_index: int = 0
    mentions: list[str] | None = None
    propagate: bool = True
    dispatch_mode: DispatchMode = "normal"

    def render(self) -> str:
        """Render one message for another agent to read."""
        label = {
            "user": "用户",
            "agent": self.sender,
            "system": "群组",
        }.get(self.kind, self.sender)
        suffix = "" if self.propagate else " (non-propagating)"
        return f"#{self.id} [{self.round_index}] {label}: {self.content}{suffix}"


@dataclass(slots=True)
class GroupMember:
    """A participant in a group chat."""

    name: str
    agent: AgentLike
    description: str = ""
    enabled: bool = True
    last_read_message_id: int = 0
    status: MemberStatus = "idle"


@dataclass(slots=True)
class GroupTurn:
    """Result of one member speaking in the group."""

    member: str
    message: GroupMessage
    events: list[GroupMessage] | None = None
