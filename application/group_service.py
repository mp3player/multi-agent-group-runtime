"""Group application service."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import application.builders as builders
from application.config import AppConfig, MemberRuntimeConfig
from application.member_config_service import (
    app_config_from_options,
    load_group_member_configs,
    member_config_from_command,
)
from application.options import GroupServiceOptions
from core.group import GroupChat
from core.member_config import MemberConfig, save_member_configs
from core.usage import UsageMonitor
from domain.group import GroupMember, GroupMessage
from observability.views import (
    group_config_report_lines,
    group_config_view,
    group_debug_report_lines,
    group_debug_snapshot_view,
    group_member_view,
    group_message_view,
    tool_audit_view,
    transcript_view,
    usage_payload_view,
    usage_report_lines,
)


@dataclass(slots=True)
class GroupAppService:
    """Application service for a configured multi-agent group."""

    group_name: str
    member_configs: list[MemberConfig]
    member_config_path: Path | None
    max_turns: int
    enable_tools: bool
    group_max_rounds: int
    persist_dynamic_members: bool = True
    usage_monitor: UsageMonitor = field(default_factory=UsageMonitor)
    group: GroupChat = field(init=False)

    def __post_init__(self) -> None:
        self.member_configs = list(self.member_configs)
        self.group = self._build_group()

    @property
    def app_config(self) -> AppConfig:
        return app_config_from_options(
            max_turns=self.max_turns,
            enable_tools=self.enable_tools,
            group_name=self.group_name,
            group_max_rounds=self.group_max_rounds,
            member_configs=self.member_configs,
        )

    def _build_group(self) -> GroupChat:
        return builders.build_group(
            self.app_config,
            usage_monitor=self.usage_monitor,
        )

    @classmethod
    def from_options(
        cls,
        options: GroupServiceOptions,
        *,
        usage_monitor: UsageMonitor | None = None,
    ) -> "GroupAppService":
        return cls(
            group_name=options.group_name,
            member_configs=load_group_member_configs(
                options.member_config_path,
                options.extra_member_names,
            ),
            member_config_path=options.member_config_path,
            max_turns=options.max_turns,
            enable_tools=options.enable_tools,
            group_max_rounds=options.group_max_rounds,
            persist_dynamic_members=options.persist_dynamic_members,
            usage_monitor=usage_monitor or UsageMonitor(),
        )

    def reset_group(self) -> None:
        self.close_members()
        self.group = self._build_group()

    async def areset_group(self) -> None:
        await self.aclose_members()
        self.group = self._build_group()

    def clear_group(self) -> None:
        self.group.clear()

    def add_member(
        self,
        *,
        name: str,
        description: str = "",
        base_url: str = "",
        api_key: str = "",
        model: str = "",
    ) -> GroupMember:
        member = builders.add_group_member(
            self.group,
            MemberRuntimeConfig(
                name=name,
                description=description,
                base_url=base_url,
                api_key=api_key,
                model=model,
            ),
            self.app_config,
            usage_monitor=self.usage_monitor,
        )
        self.member_configs.append(MemberConfig(
            name=member.name,
            description=member.description,
            base_url=base_url.strip(),
            api_key=api_key.strip(),
            model=model.strip(),
        ))
        self.save_member_configs()
        return member

    def add_member_from_command(self, command: str) -> GroupMember:
        member_config = member_config_from_command(command)
        return self.add_member(
            name=member_config.name,
            description=member_config.description,
            base_url=member_config.base_url,
            api_key=member_config.api_key,
            model=member_config.model,
        )

    def save_member_configs(self) -> None:
        if self.persist_dynamic_members and self.member_config_path is not None:
            save_member_configs(self.member_configs, self.member_config_path)

    def history_messages(self) -> list[GroupMessage]:
        return list(self.group.messages)

    def message_payload(self, message: GroupMessage) -> dict[str, Any]:
        return group_message_view(message)

    def member_payload(self, member: GroupMember) -> dict[str, Any]:
        return group_member_view(self.group, member)

    def history_payload(self) -> dict[str, Any]:
        return {"messages": transcript_view(self.history_messages())}

    def state_payload(self, *, busy: bool) -> dict[str, Any]:
        return {
            "busy": busy,
            "group": self.group.name,
            "members": [
                self.member_payload(member)
                for member in self.group.members.values()
            ],
        }

    def usage_payload(self) -> dict[str, Any]:
        return usage_payload_view(self.usage_monitor)

    def usage_report_lines(self) -> list[str]:
        return usage_report_lines(self.usage_monitor)

    def config_report_lines(self) -> list[str]:
        return group_config_report_lines(
            group_config_view(self.app_config),
            member_config_path=self.member_config_path,
        )

    def enabled_members(self) -> list[GroupMember]:
        return self.group.enabled_members()

    def member_tool_names(self) -> dict[str, list[str]]:
        return {
            member.name: member.agent.registry.names()
            for member in self.group.members.values()
        }

    def tool_audit_records(self, *, limit: int | None = None) -> list[object]:
        records: list[object] = []
        for member in self.group.members.values():
            records.extend(member.agent.registry.tool_audit_log.records())
        records.sort(key=lambda record: getattr(record, "timestamp", ""))
        if limit is not None and limit > 0:
            return records[-limit:]
        return records

    def tool_audit_payload(self, *, limit: int | None = None) -> dict[str, Any]:
        return {"records": tool_audit_view(self.tool_audit_records(limit=limit))}

    def debug_snapshot(self, *, limit: int = 10) -> dict[str, Any]:
        return group_debug_snapshot_view(
            group=self.group,
            app_config=self.app_config,
            member_payloads=[
                self.member_payload(member)
                for member in self.group.members.values()
            ],
            audit_records=self.tool_audit_records(limit=limit),
            usage_monitor=self.usage_monitor,
            limit=limit,
        )

    def debug_report_lines(self, *, limit: int = 10) -> list[str]:
        return group_debug_report_lines(
            self.debug_snapshot(limit=limit),
            limit=limit,
        )

    def stats_report(self) -> str:
        return self.group.stats_report()

    def transcript(self, *, limit: int | None = None) -> str:
        return self.group.transcript(limit=limit)

    def clear_usage(self) -> None:
        self.usage_monitor.clear()

    async def arun_message(
        self,
        message: str,
        *,
        on_member_start: Callable[[GroupMember], None] | None = None,
        on_message: Callable[[GroupMessage], None] | None = None,
        close_after: bool = False,
    ) -> Any:
        try:
            return await self.group.arun(
                message,
                on_member_start=on_member_start,
                on_message=on_message,
            )
        finally:
            if close_after:
                await self.group.aclose_members()

    async def aclose_members(self) -> None:
        await self.group.aclose_members()

    def close_members(self) -> None:
        try:
            asyncio.run(self.group.aclose_members())
        except RuntimeError:
            # The caller may already be inside an event loop; async entrypoints
            # should use aclose_members()/areset_group() instead.
            return


__all__ = [
    "GroupAppService",
]
