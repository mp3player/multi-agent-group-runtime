"""One bounded, cancellation-safe SQLite owner, separate from member workers."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from typing import Callable, TypeVar

from group.errors import LifecycleError, QueueCapacityError, StoreBusyError, StoreFailedError

T = TypeVar('T')


class GroupStore:
    """Local POSIX store. Callbacks are synchronous SQL-only transactions.

    The queue rejects overload before acceptance. Cancelling an admitted caller
    only stops that caller's wait; the worker still resolves its transaction.
    Control writes and one control read have independent reserved capacity.
    All lanes share FIFO ordering on the same SQLite worker.
    """

    def __init__(self, path: Path, queue_limit: int, control_limit: int):
        for value in (queue_limit, control_limit):
            if type(value) is not int or value < 1:
                raise ValueError('Store queue limits must be positive integers')
        self.path = path.resolve()
        self._loop = asyncio.get_running_loop()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='group-store')
        self._limits = (queue_limit, control_limit, 1)
        self._counts = [0, 0, 0]
        self._pending: set[asyncio.Future] = set()
        self._closing: asyncio.Task | None = None
        self._failure: str | None = None
        self.failure_event = asyncio.Event()
        self._db = None
        self._lock_file = None

    @classmethod
    async def open(cls, path: str | Path, *, queue_limit: int = 64,
                   control_limit: int = 8) -> GroupStore:
        owner = cls(Path(path), queue_limit, control_limit)
        initialization = asyncio.wrap_future(owner._executor.submit(owner._open), loop=owner._loop)
        try:
            await asyncio.shield(initialization)
        except BaseException:
            async def cleanup():
                try:
                    await initialization
                except BaseException:
                    pass
                await owner.close()
            task = owner._loop.create_task(cleanup())
            await asyncio.shield(task)
            raise
        return owner

    def _open(self):
        # Reject unsupported platforms rather than silently losing ownership.
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = self.path.with_suffix(self.path.suffix + '.lock').open('a+b')
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self._lock_file.close()
            self._lock_file = None
            raise StoreBusyError('Group store already has an owner') from error
        self._db = sqlite3.connect(self.path, timeout=2, isolation_level=None)
        db = self._db
        mode = db.execute('PRAGMA journal_mode=WAL').fetchone()[0]
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=2000')
        if (mode.lower() != 'wal' or db.execute('PRAGMA synchronous').fetchone()[0] != 2
                or db.execute('PRAGMA foreign_keys').fetchone()[0] != 1):
            raise StoreFailedError('Required SQLite durability settings are unavailable')

    @property
    def failed(self) -> bool:
        return self._failure is not None

    def _check_loop(self):
        if asyncio.get_running_loop() is not self._loop:
            raise LifecycleError('Group store belongs to a different event loop')

    async def read(self, operation: Callable[[sqlite3.Connection], T], *, control: bool = False) -> T:
        return await self._submit(operation, write=False, control=control)

    async def write(self, operation: Callable[[sqlite3.Connection], T], *, control: bool = False) -> T:
        return await self._submit(operation, write=True, control=control)

    async def _submit(self, operation, *, write, control):
        self._check_loop()
        if self._closing is not None:
            raise LifecycleError('Group store is closing')
        if self._failure is not None:
            raise StoreFailedError(self._failure)
        lane = (1 if write else 2) if control else 0
        if self._counts[lane] >= self._limits[lane]:
            raise QueueCapacityError('Group store queue is full; operation was not accepted')
        self._counts[lane] += 1
        future = asyncio.wrap_future(self._executor.submit(self._perform, operation, write), loop=self._loop)
        self._pending.add(future)

        def resolved(done):
            self._pending.discard(done)
            self._counts[lane] -= 1
            error = done.exception()
            if isinstance(error, sqlite3.DatabaseError):
                self._failure = f'SQLite failure: {type(error).__name__}'
                self.failure_event.set()

        future.add_done_callback(resolved)
        return await asyncio.shield(future)

    def _perform(self, operation, write):
        if self._failure is not None:
            raise StoreFailedError(self._failure)
        db = self._db
        db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        try:
            result = operation(db)
            db.execute('COMMIT')
            return result
        except BaseException as error:
            if isinstance(error, sqlite3.DatabaseError):
                self._failure = f'SQLite failure: {type(error).__name__}'
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise

    async def close(self):
        self._check_loop()
        if self._closing is None:
            self._closing = self._loop.create_task(self._finish_close())
        await asyncio.shield(self._closing)

    async def _finish_close(self):
        if self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)
        try:
            await asyncio.wrap_future(self._executor.submit(self._close), loop=self._loop)
        finally:
            self._executor.shutdown(wait=False)

    def _close(self):
        try:
            if self._db is not None:
                self._db.close()
                self._db = None
        finally:
            if self._lock_file is not None:
                self._lock_file.close()
                self._lock_file = None
