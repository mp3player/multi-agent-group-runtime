"""Preparation reservations and execution outcomes; no scheduling decisions."""

from group import state as sql
from group.errors import CapacityError, LifecycleError


def admit_inference(db, assignment):
    scope, state = db.execute('SELECT invocation_id,state FROM assignments WHERE id=?', (assignment,)).fetchone()
    if state != 'preparing' or sql.invocation(db, scope)[0] != 'open':
        raise LifecycleError('Preparation no longer authorizes inference')
    sql.check_deadline(db, scope)
    db.execute("UPDATE assignments SET state='running' WHERE id=?", (assignment,))
    sql.bump(db)


def preparation_blocked(outcome):
    return outcome.phase == 'preparation' and outcome.status == 'error' and outcome.error_type in {
        'InputTooLarge', 'CompactionError', 'ContextCapacityUnknown', 'ContextManagementError',
        'ContextArchiveError', 'ValueError', 'FileNotFoundError',
    }


def record_outcome(db, assignment, status, error, blocked):
    row = db.execute('SELECT invocation_id,state FROM assignments WHERE id=?', (assignment,)).fetchone()
    if row is None or row[1] not in ('preparing', 'running'):
        raise LifecycleError('Execution outcome has no owned assignment')
    scope_state = sql.invocation(db, row[0])[0]
    error = error[:1024] if error else None
    if blocked and scope_state == 'open':
        db.execute("UPDATE assignments SET state='blocked',error=? WHERE id=?", (error, assignment))
    else:
        db.execute("UPDATE assignments SET state='settled',outcome=?,error=? WHERE id=?", (status, error, assignment))
        db.execute("UPDATE opportunities SET state='settled',outcome=? WHERE assignment_id=?", (status, assignment))
    if scope_state == 'closing' and not db.execute(
            "SELECT 1 FROM assignments WHERE invocation_id=? AND state IN ('preparing','running') LIMIT 1", (row[0],)).fetchone():
        db.execute("UPDATE invocations SET state='terminal' WHERE id=? AND reason!='completing'", (row[0],))
    sql.bump(db)


def retry(db, scope, assignment, limits):
    sql.identifier(assignment, 'assignment identity')
    if sql.invocation(db, scope)[0] != 'open':
        raise LifecycleError('Only an open invocation can retry preparation')
    sql.check_deadline(db, scope)
    row = db.execute('SELECT state FROM assignments WHERE id=? AND invocation_id=?', (assignment, scope)).fetchone()
    if row != ('blocked',):
        raise LifecycleError('Only a blocked preparation can be retried')
    if db.execute('SELECT input,error FROM assignments WHERE id=?', (assignment,)).fetchone() == (
            '', 'Protected member input exceeds its UTF-8 byte limit'):
        # The byte budget and frozen source manifest have not changed. Preserve
        # the blockage without manufacturing an empty task or a new attempt.
        return assignment
    if db.execute("SELECT COUNT(*) FROM assignments WHERE state='queued'").fetchone()[0] >= limits.max_queued:
        raise CapacityError('Assignment queue is full')
    db.execute("UPDATE assignments SET state='queued',error=NULL WHERE id=?", (assignment,))
    sql.bump(db)
    return assignment
