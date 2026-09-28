"""Authoritative peer collaboration lifecycle, independent of speaker selection."""

from __future__ import annotations

import asyncio
from contextvars import copy_context
from copy import deepcopy
import inspect
from pathlib import Path
import time
from types import MappingProxyType

from core.agent import Agent
from group import state as sql
from group.errors import CapacityError, ConflictError, GroupError, LifecycleError, QueueCapacityError, StoreFailedError
from group.records import DispatchPlan, GroupLimits, MemberView, Receipt, Snapshot
from group.store import GroupStore


class GroupRuntime:
    """Fixed peer membership and explicit dispatch. No message launches a run.

    Application calls must stay on the creating event loop. Agent execution and
    durable SQL use independent worker capacity. Runtime.close() drains actual
    execution; cancelling its waiter never releases a still-busy Agent.
    """

    def __init__(self, store, limits, profile, strategy):
        self.store = store
        self._limits = limits
        self._profile = profile
        self._strategy = strategy
        self._loop = asyncio.get_running_loop()
        self._workers = {}
        self.workers = MappingProxyType(self._workers)
        self._operations = set()
        self._runs = {}
        self._changed = asyncio.Event()
        self._launch_lock = asyncio.Lock()
        self._launch_tasks = {}
        self._cancel_tasks = {}
        self._drive_tasks = {}
        self._receive_tasks = {}
        self._finish_tasks = {}
        self._services = {}
        self._control_lock = asyncio.Lock()
        self._closing = None
        self._failed = False
        self._installed = []

    @property
    def limits(self):
        return self._limits

    @property
    def profile(self):
        return self._profile

    @property
    def strategy(self):
        return self._strategy

    @classmethod
    async def create(cls, path: str | Path, members: dict[str, Agent], *,
                     worker_safe: bool, limits: GroupLimits | None = None,
                     profile=None, strategy=None) -> GroupRuntime:
        from core.agent_runtime.worker import AgentWorker
        from group.profiles import CollaborationProfile
        from group.tools import install_tools

        limits = limits or GroupLimits()
        if strategy is not None:
            if profile is not None:
                raise GroupError('Select a strategy or a standalone profile, not both')
            sql.identifier(strategy.name, 'strategy name')
            sql.identifier(strategy.version, 'strategy version')
            if not callable(strategy.decide) or inspect.iscoroutinefunction(strategy.decide):
                raise GroupError('Strategy decisions must be synchronous')
            profile = strategy.profile
        profile = profile if profile is not None else CollaborationProfile()
        if (not isinstance(profile, CollaborationProfile)
                or inspect.iscoroutinefunction(profile.render_input)
                or inspect.iscoroutinefunction(profile.render_system_prompt)):
            raise GroupError('A synchronous CollaborationProfile is required')
        system_prompt = profile.render_system_prompt()
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            if inspect.iscoroutine(system_prompt):
                system_prompt.close()
            raise GroupError('Profile system renderer must return nonempty text')
        members = dict(members)
        if not members or len(members) > limits.max_members:
            raise GroupError('Group membership must be nonempty and within its configured limit')
        if worker_safe is not True:
            raise GroupError('Declare the complete synchronous member pipeline worker-safe before binding')
        for name in members:
            sql.identifier(name, 'member identity')
            if name == 'user':
                raise GroupError('The user identity is reserved for external commands')
        for attribute in (None, 'session', 'registry'):
            values = [agent if attribute is None else getattr(agent, attribute) for agent in members.values()]
            if len({id(value) for value in values}) != len(values):
                raise GroupError('Members require distinct Agent, session and registry instances')
        for agent in members.values():
            if agent.context_transform is not None:
                raise GroupError('Complete Group reception requires an unmodified verified context projection')
            if agent.runtime.tool_runtime.max_result_chars < 4096 + limits.max_members * 300:
                raise GroupError('Tool result limit is too small for complete collaboration receipts and metadata')
            for name in agent.registry.names():
                if name in profile.tool_names:
                    raise GroupError('Collaboration tool names are already registered')
                if agent.registry.get_permission(name).side_effect not in ('read_only', 'memory_only'):
                    raise GroupError(f'Tool {name} requires an unsupported effect profile; only declared read-only and memory tools can bind')
        store = await GroupStore.open(path, queue_limit=limits.queue_jobs, control_limit=limits.max_active + 4)
        runtime = cls(store, limits, profile, strategy)
        try:
            await store.write(lambda db: sql.initialize(db, tuple(members)))
            for name, agent in members.items():
                # Validate admission before changing this Agent's tools.
                with agent.run_state.mutation():
                    install_tools(agent.registry, profile.tools)
                    runtime._installed.append(agent)
                    try:
                        runtime._workers[name] = AgentWorker(agent, worker_safe=True,
                                                            system_prompt_extension=system_prompt)
                    except BaseException:
                        from group.tools import uninstall_tools
                        uninstall_tools(agent.registry, profile.tools)
                        runtime._installed.pop()
                        raise
            return runtime
        except BaseException:
            await asyncio.shield(runtime._dispose())
            raise

    def _check(self, *, admission=True):
        if asyncio.get_running_loop() is not self._loop:
            raise LifecycleError('Group runtime belongs to a different event loop')
        if self._failed or self.store.failed:
            raise StoreFailedError('Group persistence failed; admission and launch are stopped')
        if admission and self._closing is not None:
            raise LifecycleError('Group runtime is closing')

    def _signal(self):
        self._changed.set()
        self._changed = asyncio.Event()

    async def _mutate(self, operation, *, control=False):
        self._check(admission=not control)
        if not control and len(self._operations) >= self.limits.queue_jobs:
            raise QueueCapacityError('Group command capacity is full; operation was not accepted')

        async def persist():
            try:
                if control:
                    async with self._control_lock:
                        result = await self._store_control(operation, write=True)
                else:
                    result = await self.store.write(operation)
                self._signal()
                return result
            except BaseException:
                if self.store.failed:
                    self._failed = True
                    self._signal()
                raise

        task = self._loop.create_task(persist())
        self._operations.add(task)
        def done(future):
            self._operations.discard(future)
            if not future.cancelled():
                future.exception()
        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def _store_control(self, operation, *, write=False):
        """Retry lifecycle admission without replaying an accepted transaction."""
        submit = self.store.write if write else self.store.read
        delay = 0.01
        while True:
            self._check(admission=False)
            try:
                return await submit(operation, control=True)
            except QueueCapacityError:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 0.1)

    async def open_invocation(self, invocation_id: str) -> str:
        """A caller-supplied identity makes a lost admission reply recoverable."""
        sql.identifier(invocation_id, 'invocation identity')
        def open_scope(db):
            if db.execute('SELECT 1 FROM invocations WHERE id=?', (invocation_id,)).fetchone():
                return invocation_id
            if db.execute("SELECT 1 FROM invocations WHERE state!='terminal'").fetchone():
                raise LifecycleError('Only one nonterminal invocation is allowed')
            db.execute("INSERT INTO invocations(id,state) VALUES (?, 'open')", (invocation_id,))
            identity = f'{self.strategy.name}:{self.strategy.version}' if self.strategy else 'manual'
            db.execute('INSERT INTO invocation_config VALUES (?,?,?,?,?)',
                       (invocation_id, identity, self.profile.name, self.limits.max_runs,
                        time.time() + self.limits.invocation_timeout))
            sql.bump(db)
            return invocation_id
        return await self._mutate(open_scope)

    async def post(self, invocation_id, content, *, key, recipients=(), reply_to=None):
        return await self._command(invocation_id, 'user', None, content, key, recipients, reply_to, request=False)

    async def request(self, invocation_id, content, *, key, recipients=(), reply_to=None):
        """Accept explicit response opportunities; an empty target allows policy selection."""
        return await self._command(invocation_id, 'user', None, content, key, recipients, reply_to, request=True)

    async def resolve_response(self, scope, opportunity_id, reply_id, *, key):
        """Accept explicit replacement evidence without changing an execution outcome."""
        from group.recovery import resolve_response
        for value, label in ((scope, 'invocation identity'), (opportunity_id, 'opportunity identity'),
                             (reply_id, 'reply identity'), (key, 'operation key')):
            sql.identifier(value, label)
        return await self._mutate(lambda db: resolve_response(db, scope, opportunity_id, reply_id, key))

    async def _command(self, scope, actor, run_id, content, key, recipients, reply_to, *, request):
        sql.identifier(scope, 'invocation identity')
        sql.identifier(key, 'operation key')
        sql.bounded_text(content, self.limits.message_bytes, 'Message')
        if isinstance(recipients, str) or not isinstance(recipients, (tuple, list)):
            raise GroupError('Recipients must be a sequence of member identities')
        recipients = tuple(recipients)
        if len(recipients) > self.limits.max_members or any(not isinstance(item, str) for item in recipients):
            raise GroupError('Invalid recipient list')
        if len(set(recipients)) != len(recipients) or any(item not in self._workers for item in recipients):
            raise GroupError('Recipients must be distinct current members')
        if reply_to is not None:
            sql.identifier(reply_to, 'reply identity')
        payload = sql.json_text([request, content, recipients, reply_to])

        def accept(db):
            existing = sql.receipt_lookup(db, scope, actor, run_id, key, payload)
            if existing is not None:
                return existing
            scope_state, _ = sql.invocation(db, scope)
            if run_id is not None:
                row = db.execute('SELECT member_id,invocation_id,state FROM assignments WHERE id=?', (run_id,)).fetchone()
                if row != (actor, scope, 'running'):
                    raise LifecycleError('Member command has no active execution in this scope')
            cleanup_post = scope_state == 'closing' and run_id is not None and not request
            if scope_state != 'open' and not cleanup_post:
                raise LifecycleError('Invocation no longer accepts this command')
            if not cleanup_post:
                sql.check_deadline(db, scope)
            if reply_to is not None and not db.execute('SELECT 1 FROM messages WHERE id=? AND invocation_id=?', (reply_to, scope)).fetchone():
                raise GroupError('Reply must reference an actual message in this invocation')
            targets = (recipients or (None,)) if request else ()
            pending = db.execute("SELECT COUNT(*) FROM opportunities WHERE invocation_id=? AND state!='settled'", (scope,)).fetchone()[0]
            if pending + len(targets) > self.limits.max_pending:
                raise CapacityError('Outstanding response request capacity is full')
            message_id = sql.new_id()
            db.execute('INSERT INTO messages(id,invocation_id,sender,run_id,content,recipients,reply_to) VALUES (?,?,?,?,?,?,?)',
                       (message_id, scope, actor, run_id, content, sql.json_text(recipients), reply_to))
            opportunities = tuple(sql.new_id() for _ in targets)
            db.executemany("INSERT INTO opportunities(id,invocation_id,message_id,target,state) VALUES (?,?,?,?,'pending')",
                           ((oid, scope, message_id, target) for oid, target in zip(opportunities, targets)))
            receipt = Receipt(scope, key, message_id, opportunities, sql.bump(db), scope_state == 'open')
            sql.receipt_save(db, actor, run_id, payload, receipt)
            return receipt

        return await self._mutate(accept, control=run_id is not None and self._closing is not None)

    def _limit(self, limit):
        limit = self.limits.page_size if limit is None else limit
        if type(limit) is not int or not 1 <= limit <= self.limits.max_page_size:
            raise GroupError('Page size exceeds the configured limit')
        return limit

    async def _page(self, table, scope, *, after=0, high_water=None, limit=None, state=None):
        self._check(admission=False)
        limit = self._limit(limit)
        def read(db):
            sql.invocation(db, scope)
            return sql.page(db, table, scope, after=after, high_water=high_water, limit=limit, state=state)
        return await self.store.read(read)

    async def history(self, scope, *, after=0, high_water=None, limit=None):
        return await self._page('messages', scope, after=after, high_water=high_water, limit=limit)

    async def message(self, scope, message_id):
        """Read one original; tools expose bounded content slices of this record."""
        self._check(admission=False)
        sql.identifier(message_id, 'message identity')
        def read(db):
            row = db.execute('SELECT sequence FROM messages WHERE id=? AND invocation_id=?', (message_id, scope)).fetchone()
            if row is None:
                raise GroupError('Unknown message in this invocation')
            return sql.page(db, 'messages', scope, after=row[0] - 1, high_water=row[0], limit=1).items[0]
        return await self.store.read(read)

    async def opportunities(self, scope, **options):
        return await self._page('opportunities', scope, **options)

    async def receive(self, scope):
        """Durably receive every source through a fixed boundary without inference."""
        return await self._coalesced(scope, self._receive_tasks, self._receive, control=True)

    async def _receive(self, scope):
        from group.reception import high_water, receive_page
        through = await self._store_control(lambda db: high_water(db, scope))
        def advance(db):
            if sql.invocation(db, scope)[0] == 'open':
                sql.check_deadline(db, scope)
            return receive_page(db, tuple(self._workers), through, self.limits.page_size)
        while await self._mutate(advance, control=True):
            await asyncio.sleep(0)
        return through

    async def reception(self, scope, member):
        """Return source coverage, never proof of inference or task completion."""
        from group.reception import status
        self._check(admission=False)
        return await self.store.read(lambda db: status(db, scope, member))

    async def assignments(self, scope, **options):
        return await self._page('assignments', scope, **options)

    def _members(self, db):
        reservations = {row[0]: (row[1], row[2]) for row in db.execute("SELECT member_id,state,id FROM assignments WHERE state IN ('queued','preparing','running','blocked')")}
        return tuple(MemberView(member, *reservations.get(member, ('idle', None))) for member in self._workers)

    async def members(self):
        self._check(admission=False)
        return await self.store.read(self._members)

    async def snapshot(self, scope):
        self._check(admission=False)
        return await self.store.read(lambda db: self._snapshot(db, scope))

    async def scheduling_view(self, scope):
        """Read one consistent bounded observation, including ordinary public posts."""
        self._check(admission=False)
        return await self.store.read(lambda db: self._scheduling_view(db, scope))

    def _scheduling_view(self, db, scope):
        from group.scheduling import ResponseEvidence, SchedulingView
        snapshot = self._snapshot(db, scope)
        from group.discovery import page as discovery_page
        messages = discovery_page(db, scope, self.limits.max_page_size)
        return SchedulingView(snapshot,
            sql.page(db, 'opportunities', scope, limit=self.limits.max_pending, state='pending').items,
            messages, tuple(ResponseEvidence(*row) for row in sql.reply_evidence(db, scope)),
            sql.page(db, 'assignments', scope, limit=snapshot.admitted_count or 1).items,
            self.limits.max_queued - snapshot.queued_count)

    async def drive(self, scope):
        """Drain eligible work using the explicitly bound strategy; shield owned work."""
        from group.driver import drive
        self._check()
        sql.identifier(scope, 'invocation identity')
        if self.strategy is None:
            raise LifecycleError('Automatic driving requires an explicitly selected strategy')
        task = self._drive_tasks.get(scope)
        if task is None:
            task = self._loop.create_task(drive(self, scope))
            self._drive_tasks[scope] = task
            def done(future):
                self._drive_tasks.pop(scope, None)
                if not future.cancelled():
                    future.exception()
            task.add_done_callback(done)
        return await asyncio.shield(task)

    def _snapshot(self, db, scope):
        status, policy_state = sql.invocation(db, scope)
        counts = dict(db.execute('SELECT state,COUNT(*) FROM assignments WHERE invocation_id=? GROUP BY state', (scope,)))
        pending = db.execute("SELECT COUNT(*) FROM opportunities WHERE invocation_id=? AND state='pending'", (scope,)).fetchone()[0]
        config = sql.invocation_config(db, scope)
        reason = db.execute('SELECT reason FROM invocations WHERE id=?', (scope,)).fetchone()[0]
        return Snapshot(scope, sql.revision(db), status, policy_state, self._members(db), pending,
                        counts.get('queued', 0), counts.get('preparing', 0) + counts.get('running', 0),
                        sql.page(db, 'opportunities', scope, limit=self.limits.page_size, state='pending'),
                        sql.page(db, 'assignments', scope, limit=self.limits.page_size),
                        sum(counts.values()), config[1] if config else None,
                        config[0] if config else 0, reason, counts.get('blocked', 0))

    async def wait_for_change(self, revision):
        """Subscribe before reading revision so a concurrent commit cannot be missed."""
        if type(revision) is not int or revision < 0:
            raise GroupError('Invalid revision')
        while True:
            self._check()
            changed = self._changed
            # Idle driver/service waits retain lifecycle ownership under queue
            # pressure; their caller still owns the absolute deadline.
            current = await self._store_control(sql.revision)
            if current != revision:
                return current
            changes = self._loop.create_task(changed.wait())
            failures = self._loop.create_task(self.store.failure_event.wait())
            try:
                await asyncio.wait((changes, failures), return_when=asyncio.FIRST_COMPLETED)
            finally:
                changes.cancel()
                failures.cancel()
                await asyncio.gather(changes, failures, return_exceptions=True)

    async def commit_plan(self, plan: DispatchPlan) -> tuple[str, ...]:
        from group.dispatch import commit, input_contexts
        from group.profiles import validate_input
        self._check()
        contexts = await self.store.read(lambda db: input_contexts(db, plan, self.limits))
        inputs = []
        for context in contexts:
            prompt = self.profile.render_input(deepcopy(context))
            if not isinstance(prompt, str):
                raise GroupError('Profile input renderer must return text')
            validate_input(context, prompt)
            # A protected source never gets truncated to admit another member.
            # None records an individually blocked assignment with its manifest.
            inputs.append(prompt if len(prompt.encode('utf-8')) <= self.limits.input_bytes else None)
        return await self._mutate(lambda db: commit(db, plan, self.limits, tuple(self._workers), inputs))

    async def launch_ready(self, scope):
        """Launch only previously committed assignments. Never select new work."""
        return await self._coalesced(scope, self._launch_tasks, self._launch)

    async def _coalesced(self, scope, cache, operation, *, control=False):
        self._check()
        sql.identifier(scope, 'invocation identity')
        task = cache.get(scope)
        if task is None:
            if not control and len(self._operations) >= self.limits.queue_jobs:
                raise QueueCapacityError('Group control capacity is full; operation was not accepted')
            task = self._loop.create_task(operation(scope))
            cache[scope] = task
            self._operations.add(task)
            def done(future):
                cache.pop(scope, None)
                self._launch_done(future)
            task.add_done_callback(done)
        return await asyncio.shield(task)

    def _launch_done(self, task):
        self._operations.discard(task)
        if not task.cancelled():
            task.exception()

    async def _launch(self, scope):
        from group.tools import ExecutionContext, execution_context
        from group.member_context import MemberContext, acknowledge, input_manifest, operation_id
        launched = []
        await self.receive(scope)
        async with self._launch_lock:
            if self._closing is not None:
                return ()
            while self._closing is None and len(self._runs) < self.limits.max_active:
                def claim(db):
                    if sql.invocation(db, scope)[0] != 'open':
                        return None
                    sql.check_deadline(db, scope)
                    row = db.execute("SELECT id,member_id,input FROM assignments WHERE invocation_id=? AND state='queued' ORDER BY sequence LIMIT 1", (scope,)).fetchone()
                    if row is not None:
                        db.execute("UPDATE assignments SET state='preparing' WHERE id=?", (row[0],))
                        sql.bump(db)
                    return row
                row = await self._mutate(claim, control=True)
                if row is None:
                    break
                assignment, member, prompt = row
                if self._closing is not None:
                    await self._record_outcome(assignment, 'cancelled')
                    break
                try:
                    manifest = await self._store_control(lambda db: input_manifest(db, assignment))
                    if self._closing is not None:
                        await self._record_outcome(assignment, 'cancelled')
                        break
                    member_context = MemberContext(self, manifest)
                    context = copy_context()
                    context.run(execution_context.set, ExecutionContext(self, scope, member, assignment))
                    task_id = operation_id(manifest['group_id'], member, assignment, 'task')
                    def applied(receipt, member=member, manifest=manifest, task_id=task_id):
                        return asyncio.run_coroutine_threadsafe(self._mutate(
                            lambda db: acknowledge(db, member, receipt,
                                manifest['protected_ids'] if receipt.operation_id == task_id else ()), control=True), self._loop).result()
                    def admitted(assignment=assignment, member_context=member_context):
                        from group.execution import admit_inference
                        member_context.validate()
                        def authorize(db):
                            if self._closing is not None:
                                return False
                            admit_inference(db, assignment)
                            return True
                        approved = asyncio.run_coroutine_threadsafe(self._mutate(
                            authorize, control=True), self._loop).result()
                        if not approved:
                            raise asyncio.CancelledError('Group closed during preparation')
                    handle = self._workers[member].submit(prompt, context=context,
                        context_batches=member_context, on_context_applied=applied,
                        before_inference=admitted, input_id=task_id,
                        input_provenance={'origin': 'runtime', 'kind': 'group_task',
                            'assignment_id': assignment, 'source_ids': list(manifest['protected_ids'])})
                except Exception as error:
                    await self._record_outcome(assignment, 'error', f'{type(error).__name__}: {error}',
                                               blocked=isinstance(error, ConflictError))
                    continue
                settlement = self._loop.create_task(self._settle(assignment, handle))
                self._runs[assignment] = (handle, settlement)
                settlement.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
                launched.append(assignment)
        return tuple(launched)

    async def _record_outcome(self, assignment, status, error=None, *, blocked=False):
        from group.execution import record_outcome
        await self._mutate(lambda db: record_outcome(db, assignment, status, error, blocked), control=True)

    async def retry_preparation(self, scope, assignment_id):
        """Explicitly retry the same frozen input; never replay a business run."""
        from group.execution import retry
        return await self._mutate(lambda db: retry(db, scope, assignment_id, self.limits))

    async def _settle(self, assignment, handle):
        outcome = await handle.wait()
        try:
            error = outcome.error
            if self.profile.publish_final and outcome.status == 'completed' and outcome.result:
                try:
                    await self._publish_final(assignment, outcome.result)
                except GroupError as publication_error:
                    error = f'Final publication failed: {publication_error}'
            from group.execution import preparation_blocked
            await self._record_outcome(assignment, outcome.status, error,
                                       blocked=preparation_blocked(outcome))
        except BaseException:
            self._failed = True
            self._signal()
            raise
        else:
            self._runs.pop(assignment, None)

    async def _publish_final(self, assignment, result):
        def targets(db):
            scope, member = db.execute('SELECT invocation_id,member_id FROM assignments WHERE id=?', (assignment,)).fetchone()
            triggers = tuple(row[0] for row in db.execute('SELECT DISTINCT message_id FROM opportunities WHERE assignment_id=?', (assignment,))) or (None,)
            missing = tuple(trigger for trigger in triggers if not db.execute(
                'SELECT 1 FROM messages WHERE run_id=? AND reply_to IS ? LIMIT 1', (assignment, trigger)).fetchone())
            return scope, member, missing
        scope, member, triggers = await self.store.read(targets)
        for index, trigger in enumerate(triggers):
            await self._command(scope, member, assignment, result, f'final:{assignment}:{index}', (), trigger, request=False)

    async def wait_idle(self):
        """Wait for launched runs only; queued assignments are never auto-started."""
        while self._runs:
            await asyncio.shield(asyncio.gather(*(value[1] for value in tuple(self._runs.values()))))

    async def finish(self, scope, *, revision):
        from group.lifecycle import finish
        return await self._coalesced(scope, self._finish_tasks,
                                     lambda scope: finish(self, scope, revision))

    async def start_service(self, scope):
        """Keep one owned scheduling service alive through idle waits."""
        from group.service import GroupService
        self._check()
        sql.identifier(scope, 'invocation identity')
        if self.strategy is None:
            raise LifecycleError('A service requires an explicitly selected strategy')
        await self._store_control(lambda db: sql.invocation(db, scope))
        if scope not in self._services:
            service = GroupService(self, scope)
            self._services[scope] = service
            service._task.add_done_callback(lambda _: self._services.pop(scope, None))
        return self._services[scope]

    async def cancel(self, scope):
        return await self._coalesced(scope, self._cancel_tasks, self._cancel)

    async def _cancel(self, scope, reason='cancelled'):
        async with self._launch_lock:
            def close_scope(db):
                status, _ = sql.invocation(db, scope)
                if status == 'terminal':
                    return ()
                if status == 'open' or db.execute('SELECT reason FROM invocations WHERE id=?', (scope,)).fetchone()[0] == 'completing':
                    db.execute("UPDATE invocations SET state='closing',reason=? WHERE id=?", (reason, scope))
                db.execute("UPDATE opportunities SET state='settled',outcome=? WHERE invocation_id=? AND (state='pending' OR assignment_id IN (SELECT id FROM assignments WHERE state IN ('queued','blocked')))", (reason, scope))
                db.execute("UPDATE assignments SET state='settled',outcome=? WHERE invocation_id=? AND state IN ('queued','blocked')", (reason, scope))
                if not db.execute("SELECT 1 FROM assignments WHERE invocation_id=? AND state IN ('preparing','running') LIMIT 1", (scope,)).fetchone():
                    db.execute("UPDATE invocations SET state='terminal' WHERE id=?", (scope,))
                sql.bump(db)
                return tuple(row[0] for row in db.execute("SELECT id FROM assignments WHERE invocation_id=? AND state IN ('preparing','running')", (scope,)))
            active = await self._mutate(close_scope, control=True)
            runs = [self._runs[assignment] for assignment in active if assignment in self._runs]
            for handle, _ in runs:
                handle.request_stop()
        if runs:
            await asyncio.shield(asyncio.gather(*(run[1] for run in runs)))

    async def close(self):
        if asyncio.get_running_loop() is not self._loop:
            raise LifecycleError('Group runtime belongs to a different event loop')
        if self._closing is None:
            self._closing = self._loop.create_task(self._close())
            self._signal()
        await asyncio.shield(self._closing)

    async def _close(self):
        try:
            for service in self._services.values():
                service._task.cancel()
            if self._services:
                await asyncio.gather(*(service._task for service in self._services.values()), return_exceptions=True)
            for task in tuple(self._drive_tasks.values()):
                task.cancel()
            if self._drive_tasks:
                await asyncio.gather(*tuple(self._drive_tasks.values()), return_exceptions=True)
            if self._operations:
                await asyncio.gather(*tuple(self._operations), return_exceptions=True)
            scopes = await self._store_control(lambda db: db.execute("SELECT id FROM invocations WHERE state!='terminal'").fetchall())
            for (scope,) in scopes:
                await self._cancel(scope)
        finally:
            await self._dispose()

    async def _dispose(self):
        from group.tools import uninstall_tools
        for handle, _ in tuple(self._runs.values()):
            handle.request_stop()
        for worker in self._workers.values():
            await worker.close()
        for agent in self._installed:
            with agent.run_state.mutation():
                uninstall_tools(agent.registry, self.profile.tools)
        self._installed.clear()
        await self.store.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
