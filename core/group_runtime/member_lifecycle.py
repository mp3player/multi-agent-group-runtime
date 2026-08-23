"""Group member activation and cleanup lifecycle."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from domain.group import GroupMember
from core.group_tools import attach_group_tools, detach_group_tools


@dataclass(slots=True)
class GroupMemberLifecycle:
    """Bind members to a group and manage group-specific agent side effects."""

    def validate_available(self, group: object, member: GroupMember) -> str | None:
        """Return an error when a member's agent is already group-bound."""
        bound_group = getattr(member.agent, "_group_chat_binding", None)
        if bound_group is not None and bound_group is not group:
            return f"Agent 已经绑定到一个群聊，不能重复加入: {member.name}"
        builder = member.agent.system_builder
        if builder is not None and builder.has_group_chat:
            return f"Agent 已经绑定到一个群聊，不能重复加入: {member.name}"
        return None

    def activate(self, group: object, group_name: str, member: GroupMember) -> None:
        """Attach group tools and prompt rules to one member agent."""
        try:
            setattr(member.agent, "_group_chat_binding", group)
            self._sync_tool_audit_caller(member)
            attach_group_tools(group, member)
            self._sync_member_system(group_name, member)
        except Exception:
            self.deactivate(group, member)
            raise

    def deactivate(self, group: object, member: GroupMember) -> None:
        """Remove group tools and prompt rules from one member agent."""
        detach_group_tools(member)
        self._clear_tool_audit_caller(member)
        self._unsync_member_system(member)
        if getattr(member.agent, "_group_chat_binding", None) is group:
            delattr(member.agent, "_group_chat_binding")
        member.status = "idle"

    async def close_members(self, members: Iterable[GroupMember]) -> None:
        """Close async resources owned by member agents."""
        for member in members:
            llm = getattr(member.agent, "llm", None)
            aclose = getattr(llm, "aclose", None)
            if aclose is not None:
                await aclose()

    def _sync_member_system(self, group_name: str, member: GroupMember) -> None:
        builder = member.agent.system_builder
        if builder is None:
            return
        builder.enable_group_chat(
            group_name=group_name,
            member_name=member.name,
            member_description=member.description,
        )
        member.agent.rebuild_system_prompt()

    def _sync_tool_audit_caller(self, member: GroupMember) -> None:
        tool_executor = getattr(member.agent, "tool_executor", None)
        runtime = getattr(tool_executor, "runtime", None)
        if runtime is not None and hasattr(runtime, "caller"):
            runtime.caller = member.name

    def _clear_tool_audit_caller(self, member: GroupMember) -> None:
        tool_executor = getattr(member.agent, "tool_executor", None)
        runtime = getattr(tool_executor, "runtime", None)
        if runtime is not None and hasattr(runtime, "caller"):
            runtime.caller = ""

    def _unsync_member_system(self, member: GroupMember) -> None:
        builder = member.agent.system_builder
        if builder is None or not builder.has_group_chat:
            return
        builder.disable_group_chat()
        member.agent.rebuild_system_prompt()
        member.agent.reset_active_to_system()
