"""Run timeout helpers for single-agent execution."""

from __future__ import annotations

import time
import asyncio
from contextvars import ContextVar
from threading import Event, RLock
from contextlib import contextmanager
import math
import inspect


class AgentTimeoutError(TimeoutError):
    """Raised when one Agent run exceeds its configured wall-clock timeout."""


class AgentBusyError(RuntimeError):
    """An Agent's mutable session cannot belong to two simultaneous runs."""


class AgentRunCancelled(asyncio.CancelledError):
    """A worker-owned run was stopped between synchronous operations."""


class WorkerRunControl:
    """Stop and terminal state belonging to exactly one submitted run."""

    def __init__(self) -> None:
        self.stop_requested = Event()
        self.terminal = None
        self.before_inference = None
        self.phase = 'preparation'


class AgentRunState:
    """Compute and validate per-run deadlines."""

    def __init__(self, run_timeout: float | None = None) -> None:
        self.run_timeout = _normalize_timeout(run_timeout)
        self._lock = RLock()
        self._active = False
        self._owner: object | None = None
        self._owner_context: ContextVar[tuple[object, WorkerRunControl] | None] = ContextVar(
            f'agent_worker_owner_{id(self)}', default=None)

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def enter_run(self) -> None:
        with self._lock:
            self._check_owner()
            if self._active:
                raise AgentBusyError("Agent is already running")
            self._active = True

    def exit_run(self) -> None:
        with self._lock:
            self._check_owner()
            self._active = False

    @contextmanager
    def mutation(self):
        """Serialize idle mutations with run admission, including nested setters."""
        with self._lock:
            self._check_owner()
            if self._active:
                raise AgentBusyError("Agent is already running")
            yield

    def reserve(self, owner: object) -> None:
        """Reserve direct run and setter admission for an external executor."""
        with self._lock:
            if self._owner is not None or self._active:
                raise AgentBusyError('Agent is already owned or running')
            self._owner = owner

    def release(self, owner: object) -> None:
        with self._lock:
            if self._owner is not owner or self._active:
                raise AgentBusyError('Agent worker ownership cannot be released')
            self._owner = None

    @contextmanager
    def worker_execution(self, owner: object, run: WorkerRunControl):
        with self._lock:
            if self._owner is not owner:
                raise AgentBusyError('Agent worker does not own this Agent')
        token = self._owner_context.set((owner, run))
        try:
            yield
        finally:
            self._owner_context.reset(token)

    def request_stop(self, owner: object, run: WorkerRunControl) -> None:
        with self._lock:
            if self._owner is owner:
                run.stop_requested.set()

    def check_stop(self) -> None:
        context = self._owner_context.get()
        if context is not None and context[1].stop_requested.is_set():
            raise AgentRunCancelled('Agent worker stop requested')

    def before_main_inference(self) -> None:
        """Authoritative admission runs once, after preparation and before model I/O."""
        context = self._owner_context.get()
        if context is None:
            return
        run = context[1]
        if run.phase == 'preparation':
            self.check_stop()
            if run.before_inference is not None:
                invoke_preparation_callback(run.before_inference)
            self.check_stop()

    def mark_main_inference(self) -> None:
        """Mark the actual model boundary only after admission/deadline checks."""
        context = self._owner_context.get()
        if context is not None:
            context[1].phase = 'execution'

    def capture_terminal(self, event) -> None:
        """Retain the actual terminal event for the bound worker run."""
        context = self._owner_context.get()
        if context is not None:
            context[1].terminal = event

    def _check_owner(self) -> None:
        context = self._owner_context.get()
        if self._owner is not None and (context is None or context[0] is not self._owner):
            raise AgentBusyError('Agent is owned by a worker')

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
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
        raise ValueError("run_timeout must be finite")
    return value if value is not None and value > 0 else None


def invoke_preparation_callback(callback, *args):
    """Do not silently treat unawaited callback work as durable acknowledgement."""
    result = callback(*args)
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        raise TypeError('preparation callbacks must finish synchronously')
    return result
