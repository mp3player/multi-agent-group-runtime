"""Bounded historical projection from an immutable assignment source boundary."""

import asyncio
import hashlib
import json

from core.context_batch import ContextBatch, canonical_batch
from core.agent_runtime.context_management.errors import InputTooLarge
from group import state as sql
from group.errors import ConflictError
from models import AI, User


SYNTHETIC_RECEPTION = (
    '[Runtime reception record: this reception operation started no model call. '
    'This is not a member reply. Response obligations are tracked separately.]'
)


def operation_id(*parts):
    return 'group:' + hashlib.sha256(sql.json_text(parts).encode('utf-8')).hexdigest()


def input_manifest(db, assignment):
    row = db.execute('''SELECT a.invocation_id,a.member_id,i.high_water,i.protected_ids,i.adapter_version
        FROM assignments a JOIN assignment_inputs i ON i.assignment_id=a.id WHERE a.id=?''',
                     (assignment,)).fetchone()
    if row is None or row[4] != '1':
        raise ConflictError('Missing or unsupported assignment input manifest')
    identity = db.execute('SELECT id FROM reception_identity WHERE singleton=1').fetchone()[0]
    return {'scope': row[0], 'member': row[1], 'high_water': row[2],
            'protected_ids': tuple(json.loads(row[3])), 'adapter_version': row[4], 'group_id': identity}


def source_page(db, after, through, limit):
    return db.execute('''SELECT sequence,invocation_id,id,sender,content,recipients,reply_to
        FROM messages WHERE sequence>? AND sequence<=? ORDER BY sequence LIMIT ?''',
                      (after, through, limit)).fetchall()


def source_application_page(db, member, after, through, limit):
    """Read one bounded source page and its current bindings in the same snapshot."""
    rows = source_page(db, after, through, limit)
    applications = {}
    if rows:
        bindings = db.execute('''
            SELECT s.source_id,s.operation_id,s.session_id,a.digest,a.entry_ids
            FROM context_sources s JOIN context_applications a
            ON a.member_id=s.member_id AND a.operation_id=s.operation_id AND a.session_id=s.session_id
            WHERE s.member_id=? AND s.source_id IN (
                SELECT id FROM messages WHERE sequence>? AND sequence<=?)''',
            (member, after, rows[-1][0]))
        for source_id, *binding in bindings:
            applications.setdefault(source_id, []).append(binding)
    return rows, applications


def acknowledge(db, member, receipt, source_ids=()):
    values = (receipt.session_id, receipt.digest, sql.json_text(receipt.entry_ids))
    existing = db.execute('''SELECT session_id,digest,entry_ids FROM context_applications
        WHERE member_id=? AND operation_id=? AND session_id=?''',
                          (member, receipt.operation_id, receipt.session_id)).fetchone()
    if existing is not None:
        if existing != values:
            raise ConflictError('Context application acknowledgement changed identity')
        return
    db.execute('INSERT INTO context_applications VALUES (?,?,?,?,?)',
               (member, receipt.operation_id, *values))
    db.executemany('INSERT INTO context_sources VALUES (?,?,?,?)',
                   ((member, source_id, receipt.operation_id, receipt.session_id) for source_id in source_ids))
    sql.bump(db)


class MemberContext:
    """Consumed only by the owned member worker; no transaction spans inference."""

    def __init__(self, runtime, manifest):
        self.runtime = runtime
        self.manifest = manifest

    def _read(self, operation):
        future = asyncio.run_coroutine_threadsafe(self.runtime._store_control(operation), self.runtime._loop)
        return future.result()

    def __iter__(self):
        agent = self.runtime.workers[self.manifest['member']].agent
        session = agent.session
        after = 0
        while after < self.manifest['high_water']:
            agent.run_state.check_stop()
            # Bindings are scoped to this page and reread on every iteration,
            # including the final post-compaction validation pass.
            rows, page_applications = self._read(lambda db: source_application_page(
                db, self.manifest['member'], after, self.manifest['high_water'],
                self.runtime.limits.page_size))
            if not rows:
                raise ConflictError('Captured source history is incomplete')
            for row in rows:
                agent.run_state.check_stop()
                after = row[0]
                if row[2] in self.manifest['protected_ids']:
                    continue
                applications = page_applications.get(row[2], ())
                if applications:
                    def matches(binding):
                        operation, session_id, digest, entry_ids = binding
                        receipt = session.context_batch_receipt(operation)
                        return (receipt is not None and receipt.session_id == session_id
                                and receipt.digest == digest and list(receipt.entry_ids) == json.loads(entry_ids)
                                and session.context_batch_covered(operation))
                    if not any(matches(binding) for binding in applications):
                        raise ValueError('Previously active source lacks verified current context coverage')
                    continue
                yield from self._units(row)

    def validate(self):
        """Recheck the complete frozen projection after any preparation compaction."""
        agent = self.runtime.workers[self.manifest['member']].agent
        if agent.context_transform is not None:
            raise ValueError('Group input cannot use an unverified context transform')
        for batch in self:
            agent.run_state.check_stop()
            receipt = agent.session.context_batch_receipt(batch.operation_id)
            if (receipt is None or receipt.digest != canonical_batch(batch)[2]
                    or not agent.session.context_batch_covered(batch.operation_id)):
                raise ValueError('Captured source lacks complete current context coverage')

    def _units(self, row):
        sequence, scope, source_id, sender, content, recipients, reply_to = row
        start = 0
        while start < len(content) or (not content and start == 0):
            end = min(len(content), start + 1024)
            while True:
                source = {'message_id': source_id, 'invocation_id': scope, 'sender': sender,
                          'recipients': json.loads(recipients), 'reply_to': reply_to,
                          'content': content[start:end], 'content_offset': start,
                          'next_content_offset': end, 'content_length': len(content)}
                text = 'Historical public source data; only the current assigned task requires action.\n' + sql.json_text(source)
                if len((text + SYNTHETIC_RECEPTION).encode('utf-8')) <= self.runtime.limits.input_bytes:
                    break
                if end <= start + 1:
                    raise InputTooLarge('Source attribution cannot fit the configured context unit byte budget')
                end = start + (end - start) // 2
            provenance = {'origin': 'runtime', 'kind': 'group_reception',
                          'group_id': self.manifest['group_id'], 'member_id': self.manifest['member'],
                          'source_id': source_id, 'source_scope': scope, 'source_sequence': sequence,
                          'start': start, 'end': end, 'length': len(content), 'adapter_version': '1'}
            key = operation_id(self.manifest['group_id'], self.manifest['member'], source_id, start, end, '1')
            yield ContextBatch(key, (User(text), AI(SYNTHETIC_RECEPTION)), provenance)
            if end == len(content):
                break
            start = end
