"""Normal completion seals admission before draining a finite reception boundary."""

import asyncio

from group import state as sql
from group.errors import InvocationLimitError, LifecycleError
from group.reception import high_water, receive_page
from group.scheduling import ResponseEvidence


async def finish(runtime, scope, revision):
    def seal(db):
        sql.check_revision(db, revision)
        if sql.invocation(db, scope)[0] != 'open':
            raise LifecycleError('Only an open invocation can finish normally')
        sql.check_deadline(db, scope)
        if (db.execute("SELECT 1 FROM opportunities WHERE invocation_id=? AND state!='settled' LIMIT 1", (scope,)).fetchone()
                or db.execute("SELECT 1 FROM assignments WHERE invocation_id=? AND state!='settled' LIMIT 1", (scope,)).fetchone()):
            raise LifecycleError('Invocation still has pending, queued, blocked or active work')
        if runtime.profile.require_public_reply and any(not ResponseEvidence(*row).satisfied for row in sql.reply_evidence(db, scope)):
            raise LifecycleError('Invocation has failed executions or missing public replies')
        through = high_water(db, scope)
        db.execute("UPDATE invocations SET state='closing',reason='completing' WHERE id=?", (scope,))
        db.execute('INSERT INTO invocation_closure VALUES (?,?)', (scope, through))
        sql.bump(db)
        return through

    try:
        through = await runtime._mutate(seal, control=True)
        def drain(db):
            state = db.execute('SELECT state,reason FROM invocations WHERE id=?', (scope,)).fetchone()
            if state != ('closing', 'completing'):
                raise LifecycleError('Normal completion was cancelled')
            sql.check_deadline(db, scope)
            if receive_page(db, tuple(runtime.workers), through, runtime.limits.page_size):
                return None
            sql.check_deadline(db, scope)
            db.execute("UPDATE invocations SET state='terminal',reason='completed' WHERE id=?", (scope,))
            return sql.bump(db)
        while True:
            final_revision = await runtime._mutate(drain, control=True)
            if final_revision is not None:
                return final_revision
            await asyncio.sleep(0)
    except InvocationLimitError:
        await runtime._cancel(scope, 'timeout')
        raise
