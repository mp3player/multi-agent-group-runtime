"""Environment-backed LLM runtime configuration."""

from __future__ import annotations

def load_env() -> None:
    """Legacy explicit entrypoint; environment loading has one owner."""
    from application.agent_config import load_project_env
    load_project_env()


def get_config() -> dict[str, str]:
    """Legacy dictionary view; prefer AgentAppConfig or LLMClient.from_env."""
    from application.agent_config import AgentAppConfig
    config = AgentAppConfig.from_env().llm
    return {
        "base_url": config.base_url.rstrip("/"),
        "api_key": config.api_key,
        "model": config.model,
    }


__all__ = ["get_config", "load_env"]
