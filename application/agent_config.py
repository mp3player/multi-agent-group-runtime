"""Neutral configuration for a standalone Agent application."""
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import os
from pathlib import Path
from dotenv import load_dotenv
from core import defaults
from core.llm_runtime.sse import validate_stream_compatibility

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


def _positive_int(mapping: Mapping[str, str], name: str, default: int | None = None) -> int | None:
    raw = mapping.get(name)
    if raw is None or (not raw.strip() and default is None):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f'{name} must be a positive integer') from error
    if value <= 0:
        raise ValueError(f'{name} must be a positive integer')
    return value


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
    timeout: float = defaults.TIMEOUT
    max_tokens: int = defaults.DEFAULT_MAX_TOKENS
    temperature: float = defaults.DEFAULT_TEMPERATURE
    stream_usage: bool = False
    trust_env: bool = True
    stream_compatibility: str = "standard"

    def __post_init__(self) -> None:
        validate_stream_compatibility(self.stream_compatibility)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "LLMProviderConfig":
        return cls(
            base_url=_str(mapping, "BaseURL"),
            api_key=_str(mapping, "BaseKey"),
            model=_str(mapping, "BaseModel"),
            timeout=_float(mapping, "MAS_LLM_TIMEOUT", defaults.TIMEOUT),
            max_tokens=_int(
                mapping,
                "MAS_DEFAULT_MAX_TOKENS",
                defaults.DEFAULT_MAX_TOKENS,
            ),
            temperature=_float(
                mapping,
                "MAS_DEFAULT_TEMPERATURE",
                defaults.DEFAULT_TEMPERATURE,
            ),
            stream_usage=_bool(mapping, 'MAS_LLM_STREAM_USAGE', False),
            trust_env=_bool(mapping, 'MAS_LLM_TRUST_ENV', True),
            stream_compatibility=_str(mapping, 'MAS_LLM_STREAM_COMPATIBILITY', 'standard'),
        )


@dataclass(frozen=True, slots=True)
class AgentRuntimeConfig:
    """Single-agent runtime limits."""

    max_turns: int = defaults.DEFAULT_MAX_TURNS
    active_message_limit: int = defaults.ACTIVE_MESSAGE_LIMIT
    history_message_limit: int = defaults.HISTORY_MESSAGE_LIMIT
    run_timeout: float = defaults.AGENT_RUN_TIMEOUT
    context_window: int | None = None
    context_input_limit: int | None = None
    context_archive_dir: str = field(default_factory=lambda: str(Path.home() / '.local/state/mas/context'))
    context_archive_quota_bytes: int = 268435456

    def __post_init__(self) -> None:
        for name, value in (
            ('MAS_CONTEXT_WINDOW', self.context_window),
            ('MAS_CONTEXT_INPUT_LIMIT', self.context_input_limit),
        ):
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f'{name} must be a positive integer')
        if type(self.context_archive_quota_bytes) is not int or self.context_archive_quota_bytes <= 0:
            raise ValueError('MAS_CONTEXT_ARCHIVE_QUOTA_BYTES must be a positive integer')

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "AgentRuntimeConfig":
        return cls(
            max_turns=_int(mapping, "MAS_DEFAULT_MAX_TURNS", defaults.DEFAULT_MAX_TURNS),
            active_message_limit=_int(
                mapping,
                "MAS_ACTIVE_MESSAGE_LIMIT",
                defaults.ACTIVE_MESSAGE_LIMIT,
            ),
            history_message_limit=_int(
                mapping,
                "MAS_HISTORY_MESSAGE_LIMIT",
                defaults.HISTORY_MESSAGE_LIMIT,
            ),
            run_timeout=_float(
                mapping,
                "MAS_AGENT_RUN_TIMEOUT",
                defaults.AGENT_RUN_TIMEOUT,
            ),
            context_window=_positive_int(mapping, 'MAS_CONTEXT_WINDOW'),
            context_input_limit=_positive_int(mapping, 'MAS_CONTEXT_INPUT_LIMIT'),
            context_archive_dir=(
                _str(mapping, 'MAS_CONTEXT_ARCHIVE_DIR')
                or str(Path.home() / '.local/state/mas/context')
            ),
            context_archive_quota_bytes=_positive_int(
                mapping, 'MAS_CONTEXT_ARCHIVE_QUOTA_BYTES', 268435456,
            ),
        )


@dataclass(frozen=True, slots=True)
class PromptConfig:
    """Prompt loading settings."""

    prompts_dir: str = str(Path(__file__).resolve().parents[1] / "prompts")
    default_order_file: str = "order.txt"

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "PromptConfig":
        return cls(
            prompts_dir=(
                _str(mapping, "MAS_PROMPTS_DIR")
                or str(Path(__file__).resolve().parents[1] / "prompts")
            ),
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
    read_file_max_chars: int = defaults.READ_FILE_MAX_CHARS
    allow_unsafe_terminal: bool = False

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "ToolConfig":
        return cls(
            enable_workspace_tools=_bool(mapping, "MAS_ENABLE_TOOLS", True),
            workspace_roots=_str(mapping, "MAS_WORKSPACE_ROOTS", _str(mapping, "WorkspaceRoots")),
            permission_dry_run=_bool(mapping, "MAS_TOOL_PERMISSION_DRY_RUN", False),
            permission_enforce=_bool(mapping, "MAS_TOOL_PERMISSION_ENFORCE", False),
            permission_rules=_str(mapping, "MAS_TOOL_PERMISSION_RULES"),
            audit_jsonl=_str(mapping, "MAS_TOOL_AUDIT_JSONL"),
            read_file_max_chars=max(0, _int(mapping, "MAS_READ_FILE_MAX_CHARS", defaults.READ_FILE_MAX_CHARS)),
            allow_unsafe_terminal=_bool(mapping, "MAS_ALLOW_UNSAFE_TERMINAL", False),
        )


@dataclass(frozen=True, slots=True)
class SkillsConfig:
    """Skill discovery settings."""

    skills_dir: str = str(Path(__file__).resolve().parents[1] / "skills")

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "SkillsConfig":
        return cls(
            skills_dir=(
                _str(mapping, "MAS_SKILLS_DIR")
                or _str(mapping, "SkillsDir")
                or str(Path(__file__).resolve().parents[1] / "skills")
            ),
        )


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    """Logging settings."""

    level: str = defaults.LOG_LEVEL

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "LoggingConfig":
        return cls(level=_str(mapping, "MAS_LOG_LEVEL", defaults.LOG_LEVEL))


@dataclass(frozen=True, slots=True)
class UsageConfig:
    """Usage accounting settings."""

    enabled: bool = True

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "UsageConfig":
        return cls(enabled=_bool(mapping, "MAS_USAGE_ENABLED", True))


@dataclass(frozen=True, slots=True)
class AgentAppConfig:
    """Top-level application composition config."""

    llm: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    agent: AgentRuntimeConfig = field(default_factory=AgentRuntimeConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)
    tools: ToolConfig = field(default_factory=ToolConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    usage: UsageConfig = field(default_factory=UsageConfig)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "AgentAppConfig":
        return cls(
            llm=LLMProviderConfig.from_mapping(mapping),
            agent=AgentRuntimeConfig.from_mapping(mapping),
            prompt=PromptConfig.from_mapping(mapping),
            tools=ToolConfig.from_mapping(mapping),
            skills=SkillsConfig.from_mapping(mapping),
            logging=LoggingConfig.from_mapping(mapping),
            usage=UsageConfig.from_mapping(mapping),
        )

    @classmethod
    def from_env(cls, env_path: str | Path | None = None) -> "AgentAppConfig":
        """Load project .env and build config from the process environment."""
        load_project_env(env_path)
        return cls.from_mapping(os.environ)

    def with_agent_options(
        self,
        *,
        max_turns: int | None = None,
        enable_tools: bool | None = None,
    ) -> "AgentAppConfig":
        """Return a copy with entrypoint agent options applied."""
        agent = self.agent
        tools = self.tools
        if max_turns is not None:
            agent = replace(agent, max_turns=max_turns)
        if enable_tools is not None:
            tools = replace(tools, enable_workspace_tools=enable_tools)
        return replace(self, agent=agent, tools=tools)
