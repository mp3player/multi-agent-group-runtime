"""Group member storage.

``GroupMemberStore`` will own member registration, removal, lookup, enabled
member ordering, and stable status snapshots. Agent binding side effects remain
owned by ``core.group.GroupChat``.
"""

from __future__ import annotations

from collections.abc import Iterable
from collections import OrderedDict
from dataclasses import dataclass, field

from domain.group import GroupMember


@dataclass(slots=True)
class GroupMemberStore:
    """Ordered storage for group members."""

    allow_duplicate_agents: bool = False
    members: "OrderedDict[str, GroupMember]" = field(default_factory=OrderedDict)

    def validate_new_member(self, member: GroupMember) -> str | None:
        """Return an error message when a member cannot be added."""
        if not member.name:
            return "成员 name 不能为空"
        if member.name in self.members:
            return f"成员已存在: {member.name}"
        if not self.allow_duplicate_agents and any(
            existing.agent is member.agent for existing in self.members.values()
        ):
            return f"同一个 Agent 不能作为多个成员重复加入: {member.name}"
        return None

    def add(self, member: GroupMember) -> GroupMember:
        """Store and return a member."""
        self.members[member.name] = member
        return member

    def remove(self, name: str) -> GroupMember | None:
        """Remove a member by name, returning None when missing."""
        return self.members.pop(name, None)

    def get(self, name: str) -> GroupMember | None:
        """Return a member by name, or None when missing."""
        return self.members.get(name)

    def enabled(self) -> list[GroupMember]:
        """Return enabled members in registration order."""
        return [member for member in self.members.values() if member.enabled]

    def require(self, name: str, *, error_type: type[Exception]) -> GroupMember:
        """Return a member by name or raise the provided error type."""
        member = self.get(name)
        if member is None:
            raise error_type(f"成员不存在: {name}")
        return member

    def resolve(
        self,
        member_or_name: GroupMember | str,
        *,
        error_type: type[Exception],
    ) -> GroupMember:
        """Resolve a member object or name to the stored member instance."""
        if isinstance(member_or_name, GroupMember):
            return self.require(member_or_name.name, error_type=error_type)
        return self.require(str(member_or_name), error_type=error_type)

    def select(
        self,
        speakers: Iterable[str] | None,
        *,
        error_type: type[Exception],
    ) -> list[GroupMember]:
        """Resolve an optional speaker list into selected members."""
        selected = (
            self.enabled()
            if speakers is None
            else [self.require(name, error_type=error_type) for name in speakers]
        )
        if not selected:
            raise error_type("没有可发言的成员")
        return selected

    def validate_mentions(self, mentions: Iterable[str]) -> list[str]:
        """Filter and deduplicate mentions against known members."""
        names = set(self.members)
        valid = [name for name in mentions if name in names]
        return list(dict.fromkeys(valid))

    def status_snapshot(self, member: GroupMember, *, unread: int, dispatchable: int) -> dict[str, object]:
        """Return a stable member status snapshot."""
        return {
            "name": member.name,
            "description": member.description,
            "enabled": member.enabled,
            "status": member.status,
            "unread": unread,
            "dispatchable": dispatchable,
        }
