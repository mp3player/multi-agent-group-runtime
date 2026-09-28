"""Cooperative deadlines that keep provider code in the calling task/context."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import sys
import time
from typing import TypeVar

T = TypeVar("T")


class DeadlineExceeded(TimeoutError):
    """This runtime's deadline, distinguishable from a provider TimeoutError."""


async def await_before(
    operation: Callable[[], Awaitable[T]], deadline: float | None, *,
    cleanup_grace: float | None = None,
) -> T:
    """Cancel in-place, optionally interrupting deadline cleanup after a grace.

    A cancelled async generator can still await inside its ``finally`` before
    ``__anext__`` returns. The second cancellation covers that unwinding without
    moving provider code into another task or abandoning it. Code that blocks
    the event loop or suppresses cancellation can still exceed both deadlines.
    """
    if deadline is None:
        return await operation()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DeadlineExceeded()
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("A deadline requires an asyncio task")
    # Python 3.11 adds cancellation counts. Python 3.10 does not retain them.
    cancelling = getattr(task, "cancelling", None)
    before = cancelling() if cancelling is not None else 0
    pending = before
    expired = False
    cancel_marker = object()
    previous_error = sys.exc_info()[1]
    cancellations = 0
    cleanup_timer: asyncio.TimerHandle | None = None
    loop = asyncio.get_running_loop()

    def cancel_operation() -> None:
        nonlocal cancellations
        if task.cancel(cancel_marker):
            cancellations += 1

    def expire() -> None:
        nonlocal expired, cleanup_timer
        expired = True
        cancel_operation()
        if cleanup_grace is not None:
            cleanup_timer = loop.call_later(cleanup_grace, cancel_operation)

    timer = loop.call_later(remaining, expire)
    try:
        try:
            result = await operation()
        finally:
            timer.cancel()
            if cleanup_timer is not None:
                cleanup_timer.cancel()
            uncancel = getattr(task, "uncancel", None)
            if uncancel is not None:
                for _ in range(cancellations):
                    pending = uncancel()
    except asyncio.CancelledError as error:
        # A deadline may interrupt cleanup that an external cancellation already
        # started. Reliable task counts take precedence over a provider-created
        # replacement CancelledError. Inspect the chain only when those counts
        # show an external cancellation, or on Python 3.10 without counts.
        if expired and (cancelling is None or pending > before):
            origin: BaseException | None = error
            seen: set[int] = set()
            while origin is not None and origin is not previous_error and id(origin) not in seen:
                seen.add(id(origin))
                if isinstance(origin, asyncio.CancelledError) and (
                    not origin.args or origin.args[0] is not cancel_marker
                ):
                    if origin is error:
                        raise
                    raise origin
                origin = origin.__context__
        # Without cancellation counts (3.10), only translate a cancellation
        # carrying this deadline's identity; preserve an external exception.
        own_cancel = (
            pending <= before if cancelling is not None
            else bool(error.args) and error.args[0] is cancel_marker
        )
        if expired and own_cancel:
            raise DeadlineExceeded() from error
        raise
    if expired or time.monotonic() >= deadline:
        raise DeadlineExceeded()
    return result
