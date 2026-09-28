"""Explicit application reconciliation using a real, successful public reply."""

from group import state as sql
from group.errors import ConflictError, GroupError, LifecycleError
from group.records import Receipt


def resolve_response(db, scope, opportunity_id, reply_id, key):
    payload = sql.json_text(['resolve_response', opportunity_id, reply_id])
    existing = sql.receipt_lookup(db, scope, 'user', None, key, payload)
    if existing is not None:
        return existing
    if sql.invocation(db, scope)[0] != 'open':
        raise LifecycleError('Only an open invocation can resolve a response')
    sql.check_deadline(db, scope)
    original = db.execute('''SELECT o.message_id,a.member_id,a.id,a.sequence
        FROM opportunities o JOIN assignments a ON a.id=o.assignment_id
        WHERE o.id=? AND o.invocation_id=? AND o.state='settled' AND a.state='settled'
        ''', (opportunity_id, scope)).fetchone()
    if original is None:
        raise GroupError('Response resolution requires a settled execution in this invocation')
    if db.execute('SELECT 1 FROM response_resolutions WHERE opportunity_id=?', (opportunity_id,)).fetchone():
        raise ConflictError('Response already has an accepted resolution')
    message_id, member, assignment, sequence = original
    replacement = db.execute('''SELECT m.reply_to,m.sender,a.id,a.sequence,a.state,a.outcome,a.error
        FROM messages m JOIN assignments a ON a.id=m.run_id
        WHERE m.id=? AND m.invocation_id=? AND a.invocation_id=? AND a.member_id=m.sender
        ''', (reply_id, scope, scope)).fetchone()
    if (replacement is None or replacement[0] != message_id or replacement[1] != member
            or replacement[2] == assignment or replacement[3] <= sequence
            or replacement[4] != 'settled' or replacement[5] not in ('completed', 'tool_stop')
            or replacement[6]):
        raise GroupError('Resolution must use a linked reply from a later successful execution of the original member')
    db.execute('INSERT INTO response_resolutions VALUES (?,?)', (opportunity_id, reply_id))
    receipt = Receipt(scope, key, reply_id, (opportunity_id,), sql.bump(db), True)
    sql.receipt_save(db, 'user', None, payload, receipt)
    return receipt
