"""Durable complete source coverage, independent of execution and response work."""

from dataclasses import dataclass
from itertools import groupby

from group import state as sql
from group.errors import GroupError


SCHEMA = (
    'CREATE TABLE IF NOT EXISTS context_sources (member_id TEXT NOT NULL, source_id TEXT NOT NULL, operation_id TEXT NOT NULL, session_id TEXT NOT NULL, PRIMARY KEY(member_id,source_id,operation_id,session_id))',
    'CREATE TABLE IF NOT EXISTS policy_discovery (invocation_id TEXT PRIMARY KEY REFERENCES invocations(id), through_sequence INTEGER NOT NULL, high_water INTEGER)',
    'CREATE TABLE IF NOT EXISTS invocation_closure (invocation_id TEXT PRIMARY KEY REFERENCES invocations(id), high_water INTEGER NOT NULL)',
    'CREATE TABLE IF NOT EXISTS reception_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), id TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS reception_progress (member_id TEXT PRIMARY KEY REFERENCES members(id), through_sequence INTEGER NOT NULL DEFAULT 0)',
    '''CREATE TABLE IF NOT EXISTS reception_batches (
        id TEXT PRIMARY KEY, member_id TEXT NOT NULL REFERENCES members(id),
        invocation_id TEXT NOT NULL REFERENCES invocations(id),
        after_sequence INTEGER NOT NULL, through_sequence INTEGER NOT NULL,
        message_count INTEGER NOT NULL, UNIQUE(member_id,invocation_id,through_sequence))''',
    '''CREATE TABLE IF NOT EXISTS assignment_inputs (
        assignment_id TEXT PRIMARY KEY REFERENCES assignments(id),
        high_water INTEGER NOT NULL, protected_ids TEXT NOT NULL,
        adapter_version TEXT NOT NULL DEFAULT '1')''',
    '''CREATE TABLE IF NOT EXISTS context_applications (
        member_id TEXT NOT NULL REFERENCES members(id), operation_id TEXT NOT NULL,
        session_id TEXT NOT NULL, digest TEXT NOT NULL, entry_ids TEXT NOT NULL,
        PRIMARY KEY(member_id,operation_id,session_id))''',
    'CREATE INDEX IF NOT EXISTS reception_scope ON reception_batches(invocation_id,member_id,through_sequence)',
)


@dataclass(frozen=True)
class ReceptionStatus:
    invocation_id: str
    member_id: str
    received_through: int
    source_high_water: int
    received_count: int
    pending_count: int
    batch_count: int


def initialize(db, members):
    for statement in SCHEMA:
        db.execute(statement)
    db.execute('INSERT OR IGNORE INTO reception_identity VALUES (1,?)', (sql.new_id(),))
    db.executemany('INSERT OR IGNORE INTO reception_progress(member_id) VALUES (?)',
                   ((member,) for member in members))


def high_water(db, scope):
    """Include earlier closed scopes without allowing later scope input to leak back."""
    sql.invocation(db, scope)
    return db.execute('''SELECT COALESCE(MAX(m.sequence),0) FROM messages m
        JOIN invocations i ON i.id=m.invocation_id
        WHERE i.rowid <= (SELECT rowid FROM invocations WHERE id=?)''', (scope,)).fetchone()[0]


def receive_page(db, members, through, limit):
    """Commit one bounded page per member; source ranges never create response slots."""
    changed = False
    remaining = False
    for member in members:
        after = db.execute('SELECT through_sequence FROM reception_progress WHERE member_id=?',
                           (member,)).fetchone()[0]
        if after >= through:
            continue
        rows = db.execute('''SELECT sequence,invocation_id FROM messages
            WHERE sequence>? AND sequence<=? ORDER BY sequence LIMIT ?''',
                          (after, through, limit)).fetchall()
        cursor = after
        for source_scope, sources in groupby(rows, key=lambda row: row[1]):
            sources = list(sources)
            end = sources[-1][0]
            db.execute('INSERT INTO reception_batches VALUES (?,?,?,?,?,?)',
                       (sql.new_id(), member, source_scope, cursor, end, len(sources)))
            cursor = end
        end = through if len(rows) < limit else cursor
        db.execute('UPDATE reception_progress SET through_sequence=? WHERE member_id=?', (end, member))
        changed = True
        remaining |= end < through
    if changed:
        sql.bump(db)
    return remaining


def status(db, scope, member):
    sql.invocation(db, scope)
    row = db.execute('SELECT through_sequence FROM reception_progress WHERE member_id=?', (member,)).fetchone()
    if row is None:
        raise GroupError('Unknown reception member')
    through = row[0]
    total, source_end, received, received_end = db.execute('''SELECT COUNT(*),
        COALESCE(MAX(sequence),0), COALESCE(SUM(sequence<=?),0),
        COALESCE(MAX(CASE WHEN sequence<=? THEN sequence END),0)
        FROM messages WHERE invocation_id=?''', (through, through, scope)).fetchone()
    count = db.execute('SELECT COUNT(*) FROM reception_batches WHERE invocation_id=? AND member_id=?',
                       (scope, member)).fetchone()[0]
    return ReceptionStatus(scope, member, received_end, source_end, received, total - received, count)
