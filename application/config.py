"""Typed application configuration for MAS runtime construction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import os
from pathlib import Path

from dotenv import load_dotenv

from core.config import MASConfig


_default_env_loaded = False


def load_project_env(env_path: str | Path | None = None) -> None:
    """Load the project .env once without overriding existing environment."""
    global _default_env_loaded
    is_default_path = env_path is None
    if _default_env_loaded and is_default_path:
        return
    if is_default_path:
        env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(env_path, override=False)
    if is_default_path:
        _default_env_loaded = True


def _str(mapping: Mapping[str, str], name: str, default: str = "") -> str:
    return mapping.get(name, default)


def _int(mapping: Mapping[str, str], name: str, default: int) -> int:
    return int(mapping.get(name, str(default)))


def _float(mapping: Mapping[str, str], name: str, default: float) -> float:
    return float(mapping.get(name, str(default)))


def _bool(mapping: Mapping[str, str], name: str, default: bool) -> bool:
    raw = mapping.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class LLMProviderConfig:
    """LLM provider settings after external configuration is parsed."""

    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: float = MASConfig.TIMEOUT
    max_tokens: int = MASConfig.DEFAULT_MAX_TOKENS
    temperature: float = MASConfig.DEFAULT_TEMPERATURE

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "LLMProviderConfig":
        return cls(
            base_url=_str(mapping, "BaseURL"),
            api_key=_str(mapping, "BaseKey"),
            model=_str(mapping, "BaseModel"),
            timeout=_float(mapping, "MAS_LLM_TIMEOUT", MASConfig.TIMEOUT),
            max_tokens=_int(
                mapping,
                "MAS_DEFAULT_MAX_TOKENS",
                MASConfig.DEFAULT_MAX_TOKENS,
            ),
            temperature=_float(
                mapping,
                "MAS_DEFAULT_TEMPERATURE",
                MASConfig.DEFAULT_TEMPERATURE,
            ),
        )


@dataclass(frozen=True, slots=True)
class AgentRuntimeConfig:
    """Single-agent runtime limits."""

    max_turns: int = MASConfig.DEFAULT_MAX_TURNS
    active_message_limit: int = MASConfig.ACTIVE_MESSAGE_LIMIT
    history_message_limit: int = MASConfig.HISTORY_MESSAGE_LIMIT
    run_timeout: float = MASConfig.AGENT_RUN_TIMEOUT

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "AgentRuntimeConfig":
        return cls(
            max_turns=_int(mapping, "MAS_DEFAULT_MAX_TURNS", MASConfig.DEFAULT_MAX_TURNS),
            active_message_limit=_int(
                mapping,
                "MAS_ACTIVE_MESSAGE_LIMIT",
                MASConfig.ACTIVE_MESSAGE_LIMIT,
            ),
            history_message_limit=_int(
                mapping,
                "MAS_HISTORY_MESSAGE_LIMIT",
                MASConfig.HISTORY_MESSAGE_LIMIT,
            ),
            run_timeout=_float(
                mapping,
                "MAS_AGENT_RUN_TIMEOUT",
                MASConfig.AGENT_RUN_TIMEOUT,
            ),
        )


@dataclass(frozen=True, slots=True)
class GroupRuntimeConfig:
    """Group runtime limits and policy selection."""

    name: str = "default"
    transcript_limit: int = MASConfig.TRANSCRIPT_LIMIT
    max_dispatch_rounds: int = MASConfig.MAX_DISPATCH_ROUNDS
    run_timeout: float = MASConfig.GROUP_RUN_TIMEOUT
    dispatch_policy: str = "default"
    events_jsonl: str = ""

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "GroupRuntimeConfig":
        return cls(
            name=_str(mapping, "MAS_GROUP_NAME", "default"),
            transcript_limit=_int(
                mapping,
                "MAS_TRANSCRIPT_LIMIT",
                MASConfig.TRANSCRIPT_LIMIT,
            ),
            max_dispatch_rounds=_int(
                mapping,
                "MAS_MAX_DISPATCH_ROUNDS",
                MASConfig.MAX_DISPATCH_ROUNDS,
            ),
            run_timeout=_float(
                mapping,
                "MAS_GROUP_RUN_TIMEOUT",
                MASConfig.GROUP_RUN_TIMEOUT,
            ),
            dispatch_policy=_str(mapping, "MAS_GROUP_DISPATCH_POLICY", "default"),
            events_jsonl=_str(mapping, "MAS_GROUP_EVENTS_JSONL"),
        )


@dataclass(frozen=True, slots=True)
class PromptConfig:
    """Prompt loading settings."""

    prompts_dir: str = "prompts"
    default_order_file: str = "order.txt"

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "PromptConfig":
        return cls(
            prompts_dir=_str(mapping, "MAS_PROMPTS_DIR", "prompts"),
            default_order_file=_str(mapping, "MAS_PROMPT_ORDER_FILE", "order.txt"),
        )


@dataclass(frozen=True, slots=True)
class ToolConfig:
    """Tool availability and workspace boundaries."""

    enable_workspace_tools: bool = True
    workspace_roots: str = ""
    permission_dry_run: bool = False
    permission_enforce: bool = False
    permission_rules: str = ""
    audit_jsonl: str = ""

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "ToolConfig":
        return cls(
            enable_workspace_tools=_bool(mapping, "MAS_ENABLE_TOOLS", True),
            workspace_roots=_str(mapping, "MAS_WORKSPACE_ROOTS"),
            permission_dry_run=_bool(mapping, "MAS_TOOL_PERMISSION_DRY_RUN", False),
            permission_enforce=_bool(mapping, "MAS_TOOL_PERMISSION_ENFORCE", False),
            permission_rules=_str(mapping, "MAS_TOOL_PERMISSION_RULES"),
            audit_jsonl=_str(mapping, "MAS_TOOL_AUDIT_JSONL"),
        )


@dataclass(frozen=True, slots=True)
class SkillsConfig:
    """Skill discovery settings."""

    skills_dir: str = "/home/coder/skill"

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "SkillsConfig":
        return cls(
            skills_dir=(
                _str(mapping, "MAS_SKILLS_DIR")
                or _str(mapping, "SkillsDir")
                or "/home/coder/skill"
            ),
        )


@dataclass(frozen=True, slots=True)
class MemberRuntimeConfig:
    """Configured member identity."""

    name: str
    description: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""

    def llm_config(self, default: LLMProviderConfig) -> LLMProviderConfig:
        """Return the effective LLM config for this member."""
        return replace(
            default,
            base_url=self.base_url.strip() or default.base_url,
            api_key=self.api_key.strip() or default.api_key,
            model=self.model.strip() or default.model,
        )


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    """Logging settings."""

    level: str = MASConfig.LOG_LEVEL

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "LoggingConfig":
        return cls(level=_str(mapping, "MAS_LOG_LEVEL", MASConfig.LOG_LEVEL))


@dataclass(frozen=True, slots=True)
class UsageConfig:
    """Usage accounting settings."""

    enabled: bool = True

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "UsageConfig":
        return cls(enabled=_bool(mapping, "MAS_USAGE_ENABLED", True))


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Top-level application composition config."""

    llm: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    agent: AgentRuntimeConfig = field(default_factory=AgentRuntimeConfig)
    group: GroupRuntimeConfig = field(default_factory=GroupRuntimeConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)
    tools: ToolConfig = field(default_factory=ToolConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    members: tuple[MemberRuntimeConfig, ...] = ()
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    usage: UsageConfig = field(default_factory=UsageConfig)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "AppConfig":
        return cls(
            llm=LLMProviderConfig.from_mapping(mapping),
            agent=AgentRuntimeConfig.from_mapping(mapping),
            group=GroupRuntimeConfig.from_mapping(mapping),
            prompt=PromptConfig.from_mapping(mapping),
            tools=ToolConfig.from_mapping(mapping),
            skills=SkillsConfig.from_mapping(mapping),
            logging=LoggingConfig.from_mapping(mapping),
            usage=UsageConfig.from_mapping(mapping),
        )

    @classmethod
    def from_env(cls, env_path: str | Path | None = None) -> "AppConfig":
        """Load project .env and build config from the process environment."""
        load_project_env(env_path)
        return cls.from_mapping(os.environ)

    def with_agent_options(
        self,
        *,
        max_turns: int | None = None,
        enable_tools: bool | None = None,
    ) -> "AppConfig":
        """Return a copy with CLI/Web agent options applied."""
        agent = self.agent
        tools = self.tools
        if max_turns is not None:
            agent = replace(agent, max_turns=max_turns)
        if enable_tools is not None:
            tools = replace(tools, enable_workspace_tools=enable_tools)
        return replace(self, agent=agent, tools=tools)

    def with_group_options(
        self,
        *,
        name: str | None = None,
        max_dispatch_rounds: int | None = None,
        dispatch_policy: str | None = None,
    ) -> "AppConfig":
        """Return a copy with CLI/Web group options applied."""
        group = self.group
        if name is not None:
            group = replace(group, name=name)
        if max_dispatch_rounds is not None:
            group = replace(group, max_dispatch_rounds=max_dispatch_rounds)
        if dispatch_policy is not None:
            group = replace(group, dispatch_policy=dispatch_policy)
        return replace(self, group=group)

    def with_members(
        self,
        members: tuple[MemberRuntimeConfig, ...],
    ) -> "AppConfig":
        """Return a copy with configured group members applied."""
        return replace(self, members=members)
