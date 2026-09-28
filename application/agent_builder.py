"""Neutral factory for standalone Agent runtime composition."""

from __future__ import annotations

from application.agent_config import AgentAppConfig, LLMProviderConfig
from pathlib import Path
from core.agent_runtime.options import AgentOptions
from core.agent_runtime.ports import ModelClient
from core.agent import Agent
from core.context_archive import FileContextArchive
from core.llm import LLMClient
from core.session import Session
from core.system_builder import SystemBuilder
from core.usage import UsageMonitor
from tools import ToolRegistry
from tools.audit import audit_sink_from_path
from tools.workspace import Workspace, workspace_roots_from_value
from tools.runtime import ToolRuntime
from tools.permissions import ToolPermissionPolicy
from tools.workspace_binding import bind_workspace_tools


def build_agent(
    config: AgentAppConfig,
    *,
    model: ModelClient | None = None,
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
    registry.tool_audit_log.set_sink(audit_sink_from_path(config.tools.audit_jsonl))
    tool_runtime = ToolRuntime(
        registry,
        workspace=Workspace(
            roots=workspace_roots_from_value(config.tools.workspace_roots),
            read_file_max_chars=config.tools.read_file_max_chars,
            allow_unsafe_terminal=config.tools.allow_unsafe_terminal,
        ),
        permission_policy=ToolPermissionPolicy.from_config(
            dry_run=config.tools.permission_dry_run,
            enforce=config.tools.permission_enforce,
            rules=config.tools.permission_rules,
        ),
    )
    builder = SystemBuilder(prompts_dir=config.prompt.prompts_dir, skills_dir=config.skills.skills_dir)
    builder.load_default(order_file=config.prompt.default_order_file)
    builder.load_skills()
    return Agent(
        model if model is not None else LLMClient(
            base_url=effective_llm.base_url,
            api_key=effective_llm.api_key,
            model=effective_llm.model,
            timeout=effective_llm.timeout,
            usage_monitor=effective_usage_monitor,
            usage_label=usage_label,
            stream_usage=effective_llm.stream_usage,
            trust_env=effective_llm.trust_env,
            stream_compatibility=effective_llm.stream_compatibility,
        ),
        session=Session(
            history_limit=config.agent.history_message_limit,
            archive_store=FileContextArchive(
                Path(config.agent.context_archive_dir).expanduser(),
                quota_bytes=config.agent.context_archive_quota_bytes,
            ),
        ),
        registry=registry,
        options=AgentOptions(
            max_turns=config.agent.max_turns,
            max_tokens=effective_llm.max_tokens,
            temperature=effective_llm.temperature,
            active_message_limit=config.agent.active_message_limit,
            run_timeout=config.agent.run_timeout,
            context_window=config.agent.context_window,
            context_input_limit=config.agent.context_input_limit,
        ),
        system_builder=builder,
        tool_runtime=tool_runtime,
    )
