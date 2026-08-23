"""Application-layer builders shared by CLI and Web."""

from __future__ import annotations

from application.config import AppConfig, LLMProviderConfig, MemberRuntimeConfig
from core.agent import Agent
from core.group import GroupChat
from core.group_policy.registry import create_dispatch_policy
from domain.group import GroupMember
from domain.group_events import group_event_sink_from_path
from core.llm import LLMClient
from core.session import Session
from core.system_builder import SystemBuilder
from core.usage import UsageMonitor
from tools import ToolRegistry
from tools.audit import audit_sink_from_path
from tools.file_ops import workspace_roots_from_value
from tools.permissions import ToolPermissionPolicy
from tools.workspace_binding import bind_workspace_tools


def build_agent(
    config: AppConfig,
    *,
    usage_monitor: UsageMonitor | None = None,
    usage_label: str = "",
    llm_config: LLMProviderConfig | None = None,
) -> Agent:
    """Build one agent from application config."""
    effective_usage_monitor = usage_monitor if config.usage.enabled else None
    effective_llm = llm_config or config.llm
    registry = ToolRegistry()
    if config.tools.enable_workspace_tools:
        bind_workspace_tools(registry)
    builder = SystemBuilder(prompts_dir=config.prompt.prompts_dir)
    builder.load_default_from_specs()
    builder.load_skills(config.skills.skills_dir)
    agent = Agent(
        LLMClient(
            base_url=effective_llm.base_url or None,
            api_key=effective_llm.api_key or None,
            model=effective_llm.model or None,
            timeout=effective_llm.timeout,
            usage_monitor=effective_usage_monitor,
            usage_label=usage_label,
        ),
        session=Session(history_limit=config.agent.history_message_limit),
        registry=registry,
        max_turns=config.agent.max_turns,
        system_builder=builder,
        run_timeout=config.agent.run_timeout,
    )
    agent.active_message_limit = config.agent.active_message_limit
    agent.tool_executor.runtime.workspace_roots = workspace_roots_from_value(
        config.tools.workspace_roots
    )
    agent.tool_executor.runtime.permission_policy = ToolPermissionPolicy.from_config(
        dry_run=config.tools.permission_dry_run,
        enforce=config.tools.permission_enforce,
        rules=config.tools.permission_rules,
    )
    agent.registry.tool_audit_log.set_sink(
        audit_sink_from_path(config.tools.audit_jsonl)
    )
    return agent


def build_group(
    config: AppConfig,
    *,
    usage_monitor: UsageMonitor | None = None,
) -> GroupChat:
    """Build one group from application config."""
    group = GroupChat(
        name=config.group.name,
        max_dispatch_rounds=config.group.max_dispatch_rounds,
        run_timeout=config.group.run_timeout,
        transcript_limit=config.group.transcript_limit,
        dispatch_policy=create_dispatch_policy(config.group.dispatch_policy),
    )
    group.events.set_sink(group_event_sink_from_path(config.group.events_jsonl))
    for member_config in config.members:
        add_group_member(
            group,
            member_config,
            config,
            usage_monitor=usage_monitor,
        )
    return group


def add_group_member(
    group: GroupChat,
    member_config: MemberRuntimeConfig,
    app_config: AppConfig,
    *,
    usage_monitor: UsageMonitor | None = None,
) -> GroupMember:
    """Build and add one group member from application config."""
    agent = build_agent(
        app_config,
        usage_monitor=usage_monitor,
        usage_label=member_config.name,
        llm_config=member_config.llm_config(app_config.llm),
    )
    return group.add_member(GroupMember(
        name=member_config.name,
        agent=agent,
        description=(
            member_config.description.strip()
            or default_member_description(member_config.name, group.name)
        ),
    ))


def default_member_description(name: str, group_name: str) -> str:
    """Return the current default member description text."""
    return f"{name} 是群组 {group_name} 中的协作 Agent。"
