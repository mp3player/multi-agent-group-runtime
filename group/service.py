"""An owned lifetime around the shared one-shot scheduler."""

import asyncio
import time

from group import state as sql


class GroupService:
    def __init__(self, runtime, scope):
        self._task = asyncio.create_task(self._serve(runtime, scope))
        self._task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)

    async def wait(self):
        """Cancelling a waiter does not cancel the scheduling owner."""
        return await asyncio.shield(self._task)

    @staticmethod
    async def _serve(runtime, scope):
        deadline = await runtime._store_control(lambda db: sql.invocation_config(db, scope)[1])
        while True:
            result = await runtime.drive(scope)
            if result.status not in ('waiting', 'needs_input'):
                return result
            try:
                await asyncio.wait_for(runtime.wait_for_change(result.revision), max(0, deadline - time.time()))
            except asyncio.TimeoutError:
                await runtime._cancel(scope, 'timeout')
