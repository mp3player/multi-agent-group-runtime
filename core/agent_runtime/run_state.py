"""Run timeout helpers for single-agent execution."""

from __future__ import annotations

import time


class AgentTimeoutError(TimeoutError):
    """Raised when one Agent run exceeds its configured wall-clock timeout."""


class AgentRunState:
    """Compute and validate per-run deadlines."""

    def __init__(self, run_timeout: float | None = None) -> None:
        self.run_timeout = _normalize_timeout(run_timeout)

    def deadline(self) -> float | None:
        """Return the monotonic deadline for a run, or ``None`` when disabled."""
        if self.run_timeout is None or self.run_timeout <= 0:
            return None
        return time.monotonic() + self.run_timeout

    def check_deadline(self, deadline: float | None) -> None:
        """Raise when a deadline has expired."""
        if deadline is not None and time.monotonic() >= deadline:
            raise AgentTimeoutError(
                f"Agent run exceeded timeout {self.run_timeout:g}s"
            )


def _normalize_timeout(value: float | None) -> float | None:
    return value if value is not None and value > 0 else None
