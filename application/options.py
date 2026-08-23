"""Entrypoint option objects for application services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AgentServiceOptions:
    """Entrypoint options for a single-agent app service."""

    max_turns: int
    enable_tools: bool
    use_stream: bool = True


@dataclass(frozen=True, slots=True)
class GroupServiceOptions:
    """Entrypoint options for a group app service."""

    group_name: str
    member_config_path: Path | None
    extra_member_names: tuple[str, ...] = ()
    max_turns: int = 20
    enable_tools: bool = True
    group_max_rounds: int = 100
    persist_dynamic_members: bool = False


__all__ = [
    "AgentServiceOptions",
    "GroupServiceOptions",
]
