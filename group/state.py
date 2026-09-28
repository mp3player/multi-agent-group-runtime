"""SQL state transitions shared by the Group control plane."""

from __future__ import annotations

from dataclasses import asdict
import json
import time
from uuid import uuid4

from group.errors import ConflictError, GroupError, InvocationLimitError, LifecycleError, RecoveryRequiredError, StalePlanError
from group.records import Assignment, Message, Opportunity, Page, Receipt

SCHEMA = (
    'CREATE TABLE IF NOT EXISTS metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL, revision INTEGER NOT NULL)',
    'CREATE TABLE IF NOT EXISTS members (id TEXT PRIMARY KEY)',
    "CREATE TABLE IF NOT EXISTS invocations (id TEXT PRIMARY KEY, state TEXT NOT NULL, reason TEXT, policy_state TEXT NOT NULL DEFAULT '{}')",
    'CREATE TABLE IF NOT EXISTS invocation_config (invocation_id TEXT PRIMARY KEY REFERENCES invocations(id), strategy TEXT NOT NULL, profile TEXT NOT NULL, max_runs INTEGER NOT NULL, deadline_at REAL NOT NULL)',
    'CREATE TABLE IF NOT EXISTS messages (sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, invocation_id TEXT NOT NULL REFERENCES invocations(id), sender TEXT NOT NULL, run_id TEXT, content TEXT NOT NULL, recipients TEXT NOT NULL, reply_to TEXT REFERENCES messages(id))',
    'CREATE TABLE IF NOT EXISTS assignments (sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, invocation_id TEXT NOT NULL REFERENCES invocations(id), member_id TEXT NOT NULL REFERENCES members(id), instruction TEXT NOT NULL, opportunity_ids TEXT NOT NULL, origin_key TEXT, state TEXT NOT NULL, outcome TEXT, error TEXT, input TEXT NOT NULL, UNIQUE(invocation_id, origin_key))',
    'CREATE TABLE IF NOT EXISTS opportunities (sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, invocation_id TEXT NOT NULL REFERENCES invocations(id), message_id TEXT NOT NULL REFERENCES messages(id), target TEXT REFERENCES members(id), state TEXT NOT NULL, assignment_id TEXT REFERENCES assignments(id), outcome TEXT)',
    'CREATE TABLE IF NOT EXISTS receipts (invocation_id TEXT NOT NULL REFERENCES invocations(id), actor TEXT NOT NULL, run_id TEXT NOT NULL, operation_key TEXT NOT NULL, payload TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY(invocation_id, actor, run_id, operation_key))',
    'CREATE TABLE IF NOT EXISTS response_resolutions (opportunity_id TEXT PRIMARY KEY REFERENCES opportunities(id), reply_id TEXT NOT NULL REFERENCES messages(id))',
    'CREATE INDEX IF NOT EXISTS message_page ON messages(invocation_id, sequence)',
    'CREATE INDEX IF NOT EXISTS message_reply ON messages(run_id, reply_to, sequence)',
    'CREATE INDEX IF NOT EXISTS opportunity_page ON opportunities(invocation_id, state, sequence)',
    'CREATE INDEX IF NOT EXISTS opportunity_assignment ON opportunities(assignment_id, sequence)',
    'CREATE INDEX IF NOT EXISTS assignment_page ON assignments(invocation_id, state, sequence)',
    "CREATE UNIQUE INDEX IF NOT EXISTS member_reservation ON assignments(member_id) WHERE state IN ('queued', 'preparing', 'running', 'blocked')",
)


def initialize(db, members):
    for statement in SCHEMA:
        db.execute(statement)
    metadata = db.execute('SELECT version FROM metadata WHERE singleton=1').fetchone()
    if metadata is None:
        db.execute('INSERT INTO metadata VALUES (1, 4, 0)')
        db.executemany('INSERT INTO members VALUES (?)', ((member,) for member in members))
    elif metadata[0] not in (1, 2, 3, 4):
        raise GroupError('Unsupported Group store schema version')
    elif metadata[0] < 4 and db.execute('SELECT 1 FROM messages LIMIT 1').fetchone():
        raise GroupError('Legacy Group history requires an explicit reception schema import')
    if db.execute("SELECT 1 FROM invocations WHERE state != 'terminal' LIMIT 1").fetchone():
        raise RecoveryRequiredError('Unfinished invocation retained; reconcile it before opening this store')
    if metadata is not None and metadata[0] < 4:
        db.execute('UPDATE metadata SET version=4 WHERE singleton=1')
    stored = {row[0] for row in db.execute('SELECT id FROM members')}
    if stored != set(members):
        raise ConflictError('Persisted membership differs from supplied members')
    db.execute('DROP INDEX IF EXISTS member_reservation')
    db.execute("CREATE UNIQUE INDEX member_reservation ON assignments(member_id) WHERE state IN ('queued','preparing','running','blocked')")
    from group.reception import initialize as initialize_reception
    initialize_reception(db, members)


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True, allow_nan=False)


def identifier(value, label='identity'):
    if not isinstance(value, str) or not value or len(value.encode('utf-8')) > 128 or any(ord(c) < 32 for c in value):
        raise GroupError(f'{label} must be nonempty text of at most 128 UTF-8 bytes without control characters')
    return value


def bounded_text(value, limit, label):
    if not isinstance(value, str) or len(value.encode('utf-8')) > limit:
        raise GroupError(f'{label} exceeds its UTF-8 byte limit or is not text')
    return value


def revision(db):
    return db.execute('SELECT revision FROM metadata WHERE singleton=1').fetchone()[0]


def bump(db):
    db.execute('UPDATE metadata SET revision=revision+1 WHERE singleton=1')
    return revision(db)


def check_revision(db, expected):
    if type(expected) is not int or revision(db) != expected:
        raise StalePlanError('Snapshot revision changed; read current state and propose again')


def invocation(db, invocation_id):
    row = db.execute('SELECT state, policy_state FROM invocations WHERE id=?', (invocation_id,)).fetchone()
    if row is None:
        raise LifecycleError('Unknown invocation')
    return row


def invocation_config(db, scope):
    return db.execute('SELECT max_runs,deadline_at FROM invocation_config WHERE invocation_id=?', (scope,)).fetchone()


def check_deadline(db, scope):
    config = invocation_config(db, scope)
    if config is not None and time.time() >= config[1]:
        raise InvocationLimitError('timeout')


def check_run_limit(db, scope, count):
    check_deadline(db, scope)
    config = invocation_config(db, scope)
    admitted = db.execute('SELECT COUNT(*) FROM assignments WHERE invocation_id=?', (scope,)).fetchone()[0]
    if config is not None and admitted + count > config[0]:
        raise InvocationLimitError('limited')


def reply_evidence(db, scope):
    """Execution evidence and the first committed reply from that exact run."""
    return db.execute('''SELECT o.id,o.message_id,a.member_id,a.id,a.outcome,a.error,
        (SELECT m.id FROM messages m WHERE m.invocation_id=o.invocation_id
         AND m.run_id=a.id AND m.sender=a.member_id AND m.reply_to=o.message_id
         ORDER BY m.sequence LIMIT 1), repaired.run_id,r.reply_id
        FROM opportunities o JOIN assignments a ON a.id=o.assignment_id
        LEFT JOIN response_resolutions r ON r.opportunity_id=o.id
        LEFT JOIN messages repaired ON repaired.id=r.reply_id
        WHERE o.invocation_id=? AND a.state='settled' ORDER BY o.sequence''', (scope,)).fetchall()


def new_id():
    return uuid4().hex


def receipt_lookup(db, scope, actor, run_id, key, payload):
    row = db.execute('SELECT payload, receipt FROM receipts WHERE invocation_id=? AND actor=? AND run_id=? AND operation_key=?',
                     (scope, actor, run_id or '', key)).fetchone()
    if row is None:
        return None
    if row[0] != payload:
        raise ConflictError('Operation key already belongs to a different command')
    value = json.loads(row[1])
    value['opportunity_ids'] = tuple(value['opportunity_ids'])
    return Receipt(**value)


def receipt_save(db, actor, run_id, payload, receipt):
    db.execute('INSERT INTO receipts VALUES (?, ?, ?, ?, ?, ?)',
               (receipt.invocation_id, actor, run_id or '', receipt.operation_key, payload, json_text(asdict(receipt))))


_FIELDS = {
    'messages': ('sequence,id,invocation_id,sender,run_id,content,recipients,reply_to', Message, 6),
    'opportunities': ('sequence,id,invocation_id,message_id,target,state,assignment_id,outcome', Opportunity, None),
    'assignments': ('sequence,id,invocation_id,member_id,instruction,opportunity_ids,origin_key,state,outcome,error', Assignment, 5),
}


def page(db, table, scope, *, after=0, high_water=None, limit=20, state=None):
    columns, record, json_column = _FIELDS[table]
    allowed_states = {'messages': (), 'opportunities': ('pending', 'assigned', 'settled'), 'assignments': ('queued', 'preparing', 'running', 'blocked', 'settled')}
    if state is not None and state not in allowed_states[table]:
        raise GroupError('Invalid record state filter')
    if type(after) is not int or after < 0 or (high_water is not None and (type(high_water) is not int or high_water < after)):
        raise GroupError('Invalid page cursor')
    actual_max = db.execute(f'SELECT COALESCE(MAX(sequence), 0) FROM {table} WHERE invocation_id=?', (scope,)).fetchone()[0]
    if after > actual_max:
        raise GroupError('Page cursor is ahead of stored records')
    if high_water is None:
        high_water = actual_max
    elif high_water > actual_max:
        raise GroupError('Page high-water mark is ahead of stored records')
    params = [scope, after, high_water]
    query = f'SELECT {columns} FROM {table} WHERE invocation_id=? AND sequence>? AND sequence<=?'
    if state is not None:
        query += ' AND state=?'
        params.append(state)
    params.append(limit + 1)
    rows = db.execute(query + ' ORDER BY sequence LIMIT ?', params).fetchall()
    exhausted = len(rows) <= limit
    records = []
    for raw in rows[:limit]:
        values = list(raw)
        if json_column is not None:
            values[json_column] = tuple(json.loads(values[json_column]))
        records.append(record(*values))
    cursor = high_water if exhausted else records[-1].sequence
    return Page(tuple(records), cursor, high_water, exhausted)
