"""Entrypoint options for a single-agent application."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentServiceOptions:
    """Legacy entrypoint overrides; new callers should use AgentAppConfig."""

    max_turns: int
    enable_tools: bool
    use_stream: bool = True


__all__ = ["AgentServiceOptions"]
