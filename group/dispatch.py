"""Atomic validation of proposals; this module never chooses a member."""

import json

from group import state as sql
from group.errors import CapacityError, ConflictError, GroupError, LifecycleError
from group.records import DispatchPlan, Disposition, RunProposal


def input_contexts(db, plan, limits):
    """Load immutable source records; never call a profile inside a transaction."""
    from group.profiles import MemberInput
    if not isinstance(plan, DispatchPlan):
        raise GroupError('Expected a DispatchPlan')
    if len(plan.runs) > limits.max_members or len(plan.dispositions) > limits.max_pending:
        raise CapacityError('Plan exceeds its bounded proposal limit')
    sql.check_revision(db, plan.revision)
    contexts = []
    for proposal in plan.runs:
        if not isinstance(proposal, RunProposal):
            raise GroupError('Expected a RunProposal')
        if len(proposal.opportunity_ids) > limits.max_pending:
            raise CapacityError('Too many trigger opportunities')
        triggers, included = [], set()
        for oid in proposal.opportunity_ids:
            row = db.execute('''SELECT m.id,m.sender,m.content,m.recipients,m.reply_to
                FROM opportunities o JOIN messages m ON m.id=o.message_id
                WHERE o.id=? AND o.invocation_id=?''', (oid, plan.invocation_id)).fetchone()
            if row is None:
                raise ConflictError('Unknown trigger opportunity')
            if row[0] not in included:
                included.add(row[0])
                triggers.append(_source(row))
        if len(proposal.required_source_ids) > limits.max_pending:
            raise CapacityError('Too many required source references')
        background = []
        from group.reception import high_water
        through = high_water(db, plan.invocation_id)
        for source_id in proposal.required_source_ids:
            sql.identifier(source_id, 'required source identity')
            row = db.execute('''SELECT id,sender,content,recipients,reply_to
                FROM messages WHERE id=? AND sequence<=?''', (source_id, through)).fetchone()
            if row is None:
                raise ConflictError('Required source is not available at this input boundary')
            if row[0] not in included:
                included.add(row[0])
                background.append(_source(row))
        contexts.append(MemberInput(plan.invocation_id, proposal.member_id, proposal.instruction,
                                    tuple(triggers), tuple(background), plan.revision, False))
    return tuple(contexts)


def _source(row):
    return {'message_id': row[0], 'sender': row[1], 'content': row[2],
            'recipients': json.loads(row[3]), 'reply_to': row[4]}


def commit(db, plan, limits, members, inputs):
    if not isinstance(plan, DispatchPlan):
        raise GroupError('Expected a DispatchPlan')
    sql.check_revision(db, plan.revision)
    if sql.invocation(db, plan.invocation_id)[0] != 'open':
        raise LifecycleError('Only an open invocation can accept a dispatch plan')
    sql.check_run_limit(db, plan.invocation_id, len(plan.runs))
    if len(inputs) != len(plan.runs):
        raise GroupError('Each proposal requires one prepared input')
    if len(plan.runs) > limits.max_members or len(plan.dispositions) > limits.max_pending:
        raise CapacityError('Plan exceeds its bounded proposal limit')
    queued = db.execute("SELECT COUNT(*) FROM assignments WHERE state='queued'").fetchone()[0]
    if queued + sum(prompt is not None for prompt in inputs) > limits.max_queued:
        raise CapacityError('Assignment queue is full')
    policy_state = plan.policy_state
    if policy_state is not None:
        sql.bounded_text(policy_state, limits.policy_state_bytes, 'Policy state')
        try:
            parsed = json.loads(policy_state)
            if not isinstance(parsed, dict):
                raise ValueError('Expected an object')
            policy_state = sql.json_text(parsed)
        except (ValueError, TypeError, RecursionError) as error:
            raise GroupError('Policy state must be a finite JSON object') from error

    touched = set()
    selected = set()
    origins = set()
    prepared = []

    def pending(opportunity_id):
        sql.identifier(opportunity_id, 'opportunity identity')
        if opportunity_id in touched:
            raise ConflictError('A plan cannot consume or dispose an opportunity twice')
        touched.add(opportunity_id)
        row = db.execute('SELECT target,state,message_id FROM opportunities WHERE id=? AND invocation_id=?',
                         (opportunity_id, plan.invocation_id)).fetchone()
        if row is None or row[1] != 'pending':
            raise ConflictError('Opportunity is not pending in this invocation')
        return row

    for proposal, prompt in zip(plan.runs, inputs):
        if not isinstance(proposal, RunProposal):
            raise GroupError('Expected a RunProposal')
        sql.identifier(proposal.member_id, 'member identity')
        if proposal.member_id not in members or proposal.member_id in selected:
            raise ConflictError('Run proposals need distinct current members')
        selected.add(proposal.member_id)
        if db.execute("SELECT 1 FROM assignments WHERE member_id=? AND state IN ('queued','preparing','running','blocked')", (proposal.member_id,)).fetchone():
            raise ConflictError('Member already has an unsettled assignment')
        sql.bounded_text(proposal.instruction, limits.input_bytes, 'Instruction')
        if len(proposal.opportunity_ids) > limits.max_pending:
            raise CapacityError('Too many trigger opportunities')
        if not proposal.opportunity_ids and proposal.origin_key is None:
            raise GroupError('A policy-originated run requires a stable origin key')
        if proposal.origin_key is not None:
            sql.identifier(proposal.origin_key, 'origin key')
            if proposal.origin_key in origins or db.execute('SELECT 1 FROM assignments WHERE invocation_id=? AND origin_key=?', (plan.invocation_id, proposal.origin_key)).fetchone():
                raise ConflictError('Origin key already has an assignment')
            origins.add(proposal.origin_key)
        for oid in proposal.opportunity_ids:
            target, _, message_id = pending(oid)
            if target is not None and target != proposal.member_id:
                raise ConflictError('Proposal member differs from the opportunity target')
        if prompt is not None:
            sql.bounded_text(prompt, limits.input_bytes, 'Member input including complete triggers')
        prepared.append((sql.new_id(), proposal, prompt))
    for disposition in plan.dispositions:
        if not isinstance(disposition, Disposition) or disposition.outcome not in ('refused', 'cancelled'):
            raise GroupError('A terminal disposition must be refused or cancelled; deferral remains pending')
        pending(disposition.opportunity_id)

    from group.discovery import acknowledge
    discovery_changed = acknowledge(db, plan, limits.max_page_size)
    # All effects, policy state and reservations share this transaction.
    for assignment, proposal, prompt in prepared:
        db.execute("INSERT INTO assignments(id,invocation_id,member_id,instruction,opportunity_ids,origin_key,state,input,error) VALUES (?,?,?,?,?,?,?,?,?)",
                   (assignment, plan.invocation_id, proposal.member_id, proposal.instruction,
                    sql.json_text(proposal.opportunity_ids), proposal.origin_key,
                    'blocked' if prompt is None else 'queued', prompt or '',
                    'Protected member input exceeds its UTF-8 byte limit' if prompt is None else None))
        from group.reception import high_water
        protected = list(proposal.required_source_ids)
        protected.extend(db.execute('SELECT message_id FROM opportunities WHERE id=?', (oid,)).fetchone()[0]
                         for oid in proposal.opportunity_ids)
        db.execute('INSERT INTO assignment_inputs(assignment_id,high_water,protected_ids) VALUES (?,?,?)',
                   (assignment, high_water(db, plan.invocation_id), sql.json_text(sorted(set(protected)))))
        db.executemany("UPDATE opportunities SET state='assigned',assignment_id=? WHERE id=?",
                       ((assignment, oid) for oid in proposal.opportunity_ids))
    for disposition in plan.dispositions:
        db.execute("UPDATE opportunities SET state='settled',outcome=? WHERE id=?", (disposition.outcome, disposition.opportunity_id))
    if policy_state is not None:
        db.execute('UPDATE invocations SET policy_state=? WHERE id=?', (policy_state, plan.invocation_id))
    if prepared or plan.dispositions or policy_state is not None or discovery_changed:
        sql.bump(db)
    return tuple(assignment for assignment, _, _ in prepared)
