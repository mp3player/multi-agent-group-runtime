"""Bounded, read-only observations; never dispatch or resolve response work."""

from __future__ import annotations

from dataclasses import dataclass

from group import state as sql
from group.errors import GroupError, LifecycleError
from group.scheduling import ResponseEvidence


@dataclass(frozen=True)
class StatusSnapshot:
    """Scalar lifecycle facts without loading assignment inputs or public history."""

    invocation_id: str
    revision: int
    state: str
    reason: str | None
    admitted_count: int
    active_count: int
    queued_count: int
    pending_count: int
    blocked_count: int
    deadline_at: float | None
    max_runs: int


@dataclass(frozen=True)
class StatusDetail:
    opportunity_id: str | None
    message_id: str | None
    member_id: str | None
    assignment_id: str | None
    opportunity_state: str | None
    assignment_state: str | None
    outcome: str | None
    error: str | None
    reply_id: str | None
    resolved_by: str | None
    resolution_reply_id: str | None
    other_publication_id: str | None = None
    other_reply_to: str | None = None

    @property
    def evidence(self) -> ResponseEvidence | None:
        """Live links are provisional; manual assignments have no response obligation."""
        if self.opportunity_id is None or self.assignment_state != 'settled':
            return None
        return ResponseEvidence(self.opportunity_id, self.message_id, self.member_id,
                                self.assignment_id, self.outcome, self.error, self.reply_id,
                                self.resolved_by, self.resolution_reply_id)


@dataclass(frozen=True)
class Status:
    snapshot: StatusSnapshot
    require_public_reply: bool
    details: tuple[StatusDetail, ...]
    detail_count: int
    missing_reply_count: int
    error_count: int

    @property
    def omitted_count(self) -> int:
        return self.detail_count - len(self.details)


# Keep the scope/member/run/trigger and accepted-resolution identity rules aligned
# with state.reply_evidence. Unlike that settled-only query, live rows here are
# publication facts and never instantiate ResponseEvidence before settlement.
_DETAILS = '''WITH details AS (
    SELECT o.sequence AS sort_sequence,o.id AS opportunity_id,o.message_id,
        COALESCE(a.member_id,o.target) AS member_id,a.id AS assignment_id,
        o.state AS opportunity_state,a.state AS assignment_state,
        CASE WHEN a.id IS NULL THEN o.outcome ELSE a.outcome END AS outcome,a.error,
        (SELECT m.id FROM messages m WHERE m.invocation_id=o.invocation_id
         AND m.run_id=a.id AND m.sender=a.member_id AND m.reply_to=o.message_id
         ORDER BY m.sequence LIMIT 1) AS reply_id,
        repaired.run_id AS resolved_by,r.reply_id AS resolution_reply_id
    FROM opportunities o LEFT JOIN assignments a ON a.id=o.assignment_id
    LEFT JOIN response_resolutions r ON r.opportunity_id=o.id
    LEFT JOIN messages repaired ON repaired.id=r.reply_id
    WHERE o.invocation_id=?
    UNION ALL
    SELECT a.sequence,NULL,NULL,a.member_id,a.id,NULL,a.state,a.outcome,a.error,NULL,NULL,NULL
    FROM assignments a WHERE a.invocation_id=?
        AND NOT EXISTS (SELECT 1 FROM opportunities o WHERE o.assignment_id=a.id)
)'''

# SQL equivalent of ResponseEvidence.satisfied for aggregate counts and ordering.
# The displayed settled rows still expose the existing authoritative value type.
_UNRESOLVED = '''opportunity_id IS NOT NULL AND assignment_state='settled' AND NOT (
    (COALESCE(resolved_by,'')!='' AND COALESCE(resolution_reply_id,'')!='') OR
    (reply_id IS NOT NULL AND COALESCE(outcome,'') IN ('completed','tool_stop') AND COALESCE(error,'')='')
)'''


async def status(runtime, scope, *, limit=20) -> Status:
    """Read a single consistent snapshot and at most 100 attributed detail rows.

    Counts cover the whole invocation; SQL aggregate scans are not constant-cost.
    Details prioritize blocked/error executions, then unresolved replies, pending
    work, and settled work. One row represents a request opportunity or a manual
    assignment with no triggers. Other publications are a single attributed
    sample per displayed row, not a complete history. No message content or
    assignment instruction is loaded. Stored execution errors are capped at 1024
    characters by the runtime; the CLI separately escapes and bounds its fields.
    """
    runtime._check(admission=False)
    sql.identifier(scope, 'invocation identity')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise GroupError('Diagnostic limit must be an integer from 1 to 100')
    required = runtime.profile.require_public_reply

    def read(db):
        invocation = db.execute('SELECT state,reason FROM invocations WHERE id=?', (scope,)).fetchone()
        if invocation is None:
            raise LifecycleError('Unknown invocation')
        counts = dict(db.execute('SELECT state,COUNT(*) FROM assignments WHERE invocation_id=? GROUP BY state',
                                 (scope,)))
        pending = db.execute("SELECT COUNT(*) FROM opportunities WHERE invocation_id=? AND state='pending'",
                             (scope,)).fetchone()[0]
        error_count = db.execute("SELECT COUNT(*) FROM assignments WHERE invocation_id=? AND COALESCE(error,'')!=''",
                                 (scope,)).fetchone()[0]
        config = sql.invocation_config(db, scope)
        snapshot = StatusSnapshot(scope, sql.revision(db), invocation[0], invocation[1],
            sum(counts.values()), counts.get('preparing', 0) + counts.get('running', 0),
            counts.get('queued', 0), pending, counts.get('blocked', 0),
            config[1] if config else None, config[0] if config else 0)
        total, missing = db.execute(_DETAILS + f'''
            SELECT COUNT(*),COALESCE(SUM(CASE WHEN ? AND ({_UNRESOLVED}) THEN 1 ELSE 0 END),0)
            FROM details''', (scope, scope, required)).fetchone()
        rows = db.execute(_DETAILS + f'''
            SELECT opportunity_id,message_id,member_id,assignment_id,opportunity_state,
                assignment_state,outcome,error,reply_id,resolved_by,resolution_reply_id
            FROM details ORDER BY CASE
                WHEN assignment_state='blocked' THEN 0
                WHEN COALESCE(error,'')!='' THEN 1
                WHEN ? AND ({_UNRESOLVED}) THEN 2
                WHEN assignment_state IS NULL OR assignment_state!='settled' THEN 3
                ELSE 4 END,sort_sequence,opportunity_id LIMIT ?''',
            (scope, scope, required, limit)).fetchall()
        details = []
        for row in rows:
            # Only inspect publication samples for the bounded displayed rows.
            other = None
            if row[3] is not None:
                other = db.execute('''SELECT id,reply_to FROM messages
                    WHERE invocation_id=? AND run_id=? AND sender=?
                    AND (? IS NULL OR reply_to IS NULL OR reply_to!=?)
                    ORDER BY sequence LIMIT 1''', (scope, row[3], row[2], row[1], row[1])).fetchone()
            details.append(StatusDetail(*row, *(other or (None, None))))
        return Status(snapshot, required, tuple(details), total, missing, error_count)

    return await runtime.store.read(read)
