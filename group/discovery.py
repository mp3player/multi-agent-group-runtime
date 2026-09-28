"""Bounded policy observation; acknowledgment is separate from member reception."""

from group import state as sql
from group.errors import GroupError


def page(db, scope, limit):
    row = db.execute('SELECT through_sequence,high_water FROM policy_discovery WHERE invocation_id=?', (scope,)).fetchone()
    after, through = row if row else (0, None)
    return sql.page(db, 'messages', scope, after=after, high_water=through, limit=limit)


def acknowledge(db, plan, limit):
    if plan.observed_through is None:
        return False
    observed = page(db, plan.invocation_id, limit)
    if (type(plan.observed_through) is not int or not observed.items
            or plan.observed_through != observed.next_cursor):
        raise GroupError('Discovery acknowledgment must cover exactly the current bounded observation')
    db.execute('''INSERT INTO policy_discovery VALUES (?,?,?)
        ON CONFLICT(invocation_id) DO UPDATE SET through_sequence=excluded.through_sequence,high_water=excluded.high_water''',
        (plan.invocation_id, observed.next_cursor, None if observed.exhausted else observed.high_water))
    return True
