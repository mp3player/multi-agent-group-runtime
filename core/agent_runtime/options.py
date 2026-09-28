"""Validated, immutable options for one agent instance."""
from dataclasses import dataclass
import math
from core import defaults


@dataclass(frozen=True)
class AgentOptions:
    """Nonpositive message limits and finite nonpositive timeouts disable limits."""
    max_turns: int = defaults.DEFAULT_MAX_TURNS
    max_tokens: int = defaults.DEFAULT_MAX_TOKENS
    temperature: float = defaults.DEFAULT_TEMPERATURE
    active_message_limit: int | None = defaults.ACTIVE_MESSAGE_LIMIT
    run_timeout: float | None = None
    context_window: int | None = None
    context_input_limit: int | None = None

    def __post_init__(self) -> None:
        for name in ('max_turns', 'max_tokens'):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f'{name} must be a positive integer')
        if self.active_message_limit is not None and type(self.active_message_limit) is not int:
            raise ValueError('active_message_limit must be an integer or None')
        for name in ('context_window', 'context_input_limit'):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f'{name} must be a positive integer or None')
        for name in ('temperature', 'run_timeout'):
            value = getattr(self, name)
            if value is None and name == 'run_timeout':
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{name} must be finite')
