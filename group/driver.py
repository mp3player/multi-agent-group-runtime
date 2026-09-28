"""Owned scheduling loop. A quiet Group stays open until explicit completion."""

import asyncio
import time

from group import state as sql
from group.errors import GroupError, InvocationLimitError, QueueCapacityError, StalePlanError
from group.scheduling import DriveResult, SchedulingDecision


def result(view, status=None, reason='', *, require_reply=True):
    snapshot = view.snapshot
    missing = tuple(item.opportunity_id for item in view.evidence if not item.satisfied) if require_reply else ()
    satisfied = {item.opportunity_id for item in view.evidence if item.satisfied}
    failed = any((run.outcome not in ('completed', 'tool_stop') or run.error)
                 and not (run.opportunity_ids and all(oid in satisfied for oid in run.opportunity_ids))
                 for run in view.runs if run.state == 'settled')
    if status is None:
        status = 'needs_input' if missing or failed or snapshot.pending_count or snapshot.blocked_count else 'waiting'
    blocked = tuple(run.id for run in view.runs if run.state == 'blocked')
    if blocked and status == 'needs_input':
        reason = 'Preparation blocked; inspect assignment errors and explicitly retry or cancel'
    return DriveResult(status, reason, snapshot.admitted_count, missing, snapshot.revision, blocked)


async def drive(runtime, scope):
    view = None
    try:
        def read_deadline(db):
            if sql.invocation(db, scope)[0] == 'terminal':
                return runtime._scheduling_view(db, scope)
            config = sql.invocation_config(db, scope)
            return config[1] if config else None
        deadline = await runtime._store_control(read_deadline)
        if hasattr(deadline, 'snapshot'):
            return result(deadline, deadline.snapshot.reason or 'completed', require_reply=runtime.profile.require_public_reply)
        retry_delay = 0.01
        while True:
            try:
                await runtime.receive(scope)
                view = await runtime.scheduling_view(scope)
                snapshot = view.snapshot
                if snapshot.state == 'terminal':
                    return result(view, snapshot.reason or 'completed', require_reply=runtime.profile.require_public_reply)
                if snapshot.state == 'closing':
                    await runtime.wait_for_change(snapshot.revision)
                    continue
                remaining = snapshot.deadline_at - time.time()
                if remaining <= 0:
                    raise InvocationLimitError('timeout')
                if snapshot.queued_count:
                    launched = await runtime.launch_ready(scope)
                    if launched:
                        continue
                decision = runtime.strategy.decide(view)
                if not isinstance(decision, SchedulingDecision):
                    raise GroupError('Strategy must return a SchedulingDecision')
                if decision.plan is not None:
                    if decision.plan.invocation_id != scope or decision.plan.revision != snapshot.revision:
                        raise GroupError('Strategy plan must refer to its observed invocation and revision')
                    if not (decision.plan.runs or decision.plan.dispositions or decision.plan.observed_through is not None):
                        raise GroupError('A driver plan must advance work; state-only plans need an explicit manual commit')
                    try:
                        await runtime.commit_plan(decision.plan)
                    except StalePlanError:
                        await asyncio.sleep(retry_delay)
                        continue
                    continue
                if not snapshot.active_count and not snapshot.queued_count:
                    if snapshot.pending_count and snapshot.admitted_count >= snapshot.max_runs:
                        raise InvocationLimitError('limited')
                    return result(view, reason=decision.reason, require_reply=runtime.profile.require_public_reply)
                try:
                    await asyncio.wait_for(runtime.wait_for_change(snapshot.revision),
                                           max(0, snapshot.deadline_at - time.time()))
                except asyncio.TimeoutError:
                    raise InvocationLimitError('timeout') from None
            except QueueCapacityError:
                runtime._check()
                remaining = deadline - time.time() if deadline is not None else retry_delay
                if remaining <= 0:
                    raise InvocationLimitError('timeout') from None
                await asyncio.sleep(min(retry_delay, remaining))
                retry_delay = min(retry_delay * 2, 0.1)
    except InvocationLimitError as limit:
        await runtime._cancel(scope, limit.reason)
        view = await runtime._store_control(lambda db: runtime._scheduling_view(db, scope))
        return result(view, view.snapshot.reason, f'Invocation {limit.reason}',
                      require_reply=runtime.profile.require_public_reply)
    except Exception:
        # A broken strategy must not abandon active work or its deadline owner.
        # Persistence failure is fail-closed; runtime.close still owns cleanup.
        if view is not None and not runtime.store.failed:
            await runtime._cancel(scope, 'error')
        raise
