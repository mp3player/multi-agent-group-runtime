"""Provisional publication facts, scoped to one bound member execution."""

from group import state as sql
from group.errors import LifecycleError


def publication(db, scope, member, assignment, message_id, *, require_reply, limit):
    """Observe current links without declaring settlement or scheduling work."""
    row = db.execute('''SELECT a.state,m.reply_to FROM assignments a JOIN messages m
        ON m.run_id=a.id AND m.invocation_id=a.invocation_id AND m.sender=a.member_id
        WHERE a.id=? AND a.invocation_id=? AND a.member_id=? AND m.id=?''',
        (assignment, scope, member, message_id)).fetchone()
    if row is None:
        raise LifecycleError('Publication has no matching bound execution')
    query = '''SELECT o.message_id,EXISTS(SELECT 1 FROM messages m
        WHERE m.run_id=o.assignment_id AND m.invocation_id=o.invocation_id
        AND m.sender=? AND m.reply_to=o.message_id) AS linked,MIN(o.sequence) AS ordinal
        FROM opportunities o WHERE o.assignment_id=? AND o.invocation_id=?
        GROUP BY o.message_id'''
    args = (member, assignment, scope)
    count, linked = db.execute(f'SELECT COUNT(*),COALESCE(SUM(linked),0) FROM ({query})', args).fetchone()
    unlinked = [item[0] for item in db.execute(
        f'SELECT message_id FROM ({query}) WHERE NOT linked ORDER BY ordinal LIMIT ?',
        (*args, limit))]
    matches = bool(row[1] is not None and db.execute('''SELECT 1 FROM opportunities
        WHERE assignment_id=? AND invocation_id=? AND message_id=? LIMIT 1''',
        (assignment, scope, row[1])).fetchone())
    return {
        'available': True, 'assignment_id': assignment, 'execution_state': row[0],
        'revision': sql.revision(db), 'require_public_reply': require_reply,
        'trigger_count': count, 'linked_trigger_count': linked,
        'unlinked_trigger_count': count - linked, 'unlinked_trigger_ids': unlinked,
        'unlinked_trigger_ids_complete': len(unlinked) == count - linked,
        'reply_matches_trigger': matches,
    }
