"""Group run state management.

``GroupRunState`` will own run guards, concurrency checks, and whole-run
deadline calculation. Domain-specific exception types remain owned by
``core.group.GroupChat``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import threading
import time


@dataclass(slots=True)
class GroupRunState:
    """Run locking and timeout state for one group."""

    run_timeout: float | None = None
    active: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        self.run_timeout = (
            self.run_timeout if self.run_timeout and self.run_timeout > 0 else None
        )

    def ensure_not_running(
        self,
        action: str,
        error_factory: Callable[[str], Exception],
    ) -> None:
        """Raise when a protected action is attempted during a run."""
        with self.lock:
            if self.active:
                raise error_factory(f"群组正在运行，不能{action}")

    def enter(self, error_factory: Callable[[str], Exception]) -> None:
        """Mark the group as running, raising when already active."""
        with self.lock:
            if self.active:
                raise error_factory("群组正在运行，不能并发调度")
            self.active = True

    def exit(self) -> None:
        """Mark the group as not running."""
        with self.lock:
            self.active = False

    def deadline(self) -> float | None:
        """Return the current run deadline timestamp."""
        if self.run_timeout is None or self.run_timeout <= 0:
            return None
        return time.monotonic() + self.run_timeout

    def check_deadline(
        self,
        deadline: float | None,
        error_factory: Callable[[str], Exception],
    ) -> None:
        """Raise when the configured deadline has passed."""
        if deadline is not None and time.monotonic() >= deadline:
            raise error_factory(f"Group run exceeded timeout {self.run_timeout:g}s")
