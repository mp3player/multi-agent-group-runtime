"""Member config helpers shared by CLI and Web application services."""

from __future__ import annotations

import shlex
from pathlib import Path

from application.config import AppConfig, MemberRuntimeConfig
from core.member_config import (
    MemberConfig,
    load_member_configs,
    merge_member_configs,
)


def load_group_member_configs(
    path: Path | None,
    extra_names: list[str] | tuple[str, ...] = (),
) -> list[MemberConfig]:
    """Load configured members and append entrypoint-provided member names."""
    return merge_member_configs(
        load_member_configs(path),
        list(extra_names),
    )


def member_runtime_configs(
    member_configs: list[MemberConfig] | tuple[MemberConfig, ...],
) -> tuple[MemberRuntimeConfig, ...]:
    """Convert persisted member configs into runtime member configs."""
    return tuple(
        MemberRuntimeConfig(
            name=member.name,
            description=member.description,
            base_url=member.base_url,
            api_key=member.api_key,
            model=member.model,
        )
        for member in member_configs
    )


def app_config_from_options(
    *,
    max_turns: int,
    enable_tools: bool,
    group_name: str = "default",
    group_max_rounds: int = 100,
    member_configs: list[MemberConfig] | tuple[MemberConfig, ...] = (),
) -> AppConfig:
    """Build the current AppConfig shape from CLI/Web options."""
    return (
        AppConfig.from_env()
        .with_agent_options(max_turns=max_turns, enable_tools=enable_tools)
        .with_group_options(name=group_name, max_dispatch_rounds=group_max_rounds)
        .with_members(member_runtime_configs(member_configs))
    )


def member_config_from_command(command: str) -> MemberRuntimeConfig:
    """Parse the /addmember command body."""
    parts = shlex.split(command)
    if not parts:
        raise ValueError(
            "用法: /addmember NAME [DESCRIPTION] "
            "[--url URL] [--key KEY] [--model MODEL]"
        )
    name = parts[0]
    description_parts: list[str] = []
    base_url = ""
    api_key = ""
    model = ""
    index = 1
    while index < len(parts):
        part = parts[index]
        if part in {"--url", "--base-url", "--base_url"}:
            index += 1
            if index >= len(parts):
                raise ValueError(f"{part} 需要参数")
            base_url = parts[index].strip()
        elif part in {"--key", "--api-key", "--api_key"}:
            index += 1
            if index >= len(parts):
                raise ValueError(f"{part} 需要参数")
            api_key = parts[index].strip()
        elif part == "--model":
            index += 1
            if index >= len(parts):
                raise ValueError("--model 需要参数")
            model = parts[index].strip()
        else:
            description_parts.append(part)
        index += 1
    return MemberRuntimeConfig(
        name=name,
        description=" ".join(description_parts).strip(),
        base_url=base_url,
        api_key=api_key,
        model=model,
    )


__all__ = [
    "app_config_from_options",
    "load_group_member_configs",
    "member_config_from_command",
    "member_runtime_configs",
]
