"""Environment-backed LLM runtime configuration."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_env_loaded = False


def load_env() -> None:
    """Load project ``.env`` into ``os.environ`` without overriding values."""
    global _env_loaded
    if _env_loaded:
        return
    env_path = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(env_path, override=False)
    _env_loaded = True


def get_config() -> dict[str, str]:
    """Return LLM provider config using existing env names."""
    load_env()
    return {
        "base_url": os.environ.get("BaseURL", "").rstrip("/"),
        "api_key": os.environ.get("BaseKey", ""),
        "model": os.environ.get("BaseModel", ""),
    }


__all__ = ["get_config", "load_env"]
