import os
from typing import Final

class MASConfig:
    """Central runtime configuration values."""
    
    # LLM defaults
    DEFAULT_MAX_TOKENS: Final[int] = int(os.environ.get("MAS_DEFAULT_MAX_TOKENS", "4096"))
    DEFAULT_TEMPERATURE: Final[float] = float(os.environ.get("MAS_DEFAULT_TEMPERATURE", "0.7"))
    
    # Agent limits
    ACTIVE_MESSAGE_LIMIT: Final[int] = int(os.environ.get("MAS_ACTIVE_MESSAGE_LIMIT", "80"))
    HISTORY_MESSAGE_LIMIT: Final[int] = int(os.environ.get("MAS_HISTORY_MESSAGE_LIMIT", "1000"))
    DEFAULT_MAX_TURNS: Final[int] = int(os.environ.get("MAS_DEFAULT_MAX_TURNS", "20"))
    AGENT_RUN_TIMEOUT: Final[float] = float(os.environ.get("MAS_AGENT_RUN_TIMEOUT", "0"))
    
    # GroupChat limits
    TRANSCRIPT_LIMIT: Final[int] = int(os.environ.get("MAS_TRANSCRIPT_LIMIT", "40"))
    MAX_DISPATCH_ROUNDS: Final[int] = int(os.environ.get("MAS_MAX_DISPATCH_ROUNDS", "100"))
    GROUP_RUN_TIMEOUT: Final[float] = float(os.environ.get("MAS_GROUP_RUN_TIMEOUT", "0"))
    
    # Operations
    LOG_LEVEL: Final[str] = os.environ.get("MAS_LOG_LEVEL", "INFO")
    TIMEOUT: Final[float] = float(os.environ.get("MAS_LLM_TIMEOUT", "120.0"))
