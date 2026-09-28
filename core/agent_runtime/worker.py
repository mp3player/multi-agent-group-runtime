"""Exclusive synchronous execution of a borrowed Agent on one worker thread."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import Context, copy_context
from dataclasses import dataclass
import inspect
from threading import RLock

from core.agent import Agent
from core.context_batch import ContextBatch, canonical_batch
from core.agent_runtime.context_management.errors import InputTooLarge
from models import User
from copy import deepcopy
from core.agent_runtime.run_state import AgentBusyError, WorkerRunControl, invoke_preparation_callback


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    status: str
    result: str = ''
    error: str | None = None
    phase: str = 'execution'
    error_type: str | None = None


class AgentExecution:
    """A cancellable wait handle whose settlement follows the real worker."""

    def __init__(self, future: Future[ExecutionOutcome], stop) -> None:
        self._future = future
        self._stop = stop

    def request_stop(self) -> None:
        if not self._future.done():
            self._stop()

    @property
    def settled(self) -> bool:
        return self._future.done()

    @property
    def outcome(self) -> ExecutionOutcome | None:
        return self._future.result() if self._future.done() else None

    async def wait(self) -> ExecutionOutcome:
        return await asyncio.shield(asyncio.wrap_future(self._future))


class AgentWorker:
    """Bind one complete sync Agent pipeline to a dedicated serial worker."""

    def __init__(self, agent: Agent, *, worker_safe: bool,
                 system_prompt_extension: str | None = None) -> None:
        if worker_safe is not True:
            raise ValueError('worker_safe=True must be explicitly declared')
        _validate_sync_pipeline(agent)
        self.agent = agent
        self._owner = object()
        self._lock = RLock()
        self._active: AgentExecution | None = None
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._system_extension = None
        agent.run_state.reserve(self._owner)
        try:
            if system_prompt_extension is not None:
                with agent.run_state.worker_execution(self._owner, WorkerRunControl()):
                    self._system_extension = agent.add_system_prompt_extension(system_prompt_extension)
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='agent-worker')
        except BaseException:
            self._release_agent()
            raise

    def submit(self, message: str, *, context: Context | None = None,
               context_batches=(), on_context_applied=None, before_inference=None,
               input_id: str | None = None, input_provenance: dict | None = None) -> AgentExecution:
        with self._lock:
            if self._closed:
                raise RuntimeError('AgentWorker is closed')
            if self._active is not None and not self._active.settled:
                raise AgentBusyError('Agent worker is already running')
            if not isinstance(message, str):
                raise TypeError('message must be str')
            if context is not None and not isinstance(context, Context):
                raise TypeError('context must be a contextvars.Context')
            for callback in (on_context_applied, before_inference):
                if callback is not None and (not callable(callback) or _is_async_callable(callback)):
                    raise TypeError('preparation callbacks must be synchronous callables')
            if input_provenance is not None and input_id is None:
                raise ValueError('input_provenance requires input_id')
            if input_id is not None and (not isinstance(input_id, str) or not input_id.strip()):
                raise ValueError('input_id must be nonempty text')
            run_context = context.copy() if context is not None else copy_context()
            run = WorkerRunControl()
            run.before_inference = before_inference
            future = self._executor.submit(run_context.run, self._run, message, run,
                context_batches, on_context_applied, input_id, deepcopy(input_provenance))
            execution = AgentExecution(future, lambda: self.agent.run_state.request_stop(self._owner, run))
            self._active = execution
            return execution

    def _run(self, message: str, run: WorkerRunControl, context_batches,
             on_context_applied, input_id, input_provenance) -> ExecutionOutcome:
        with self.agent.run_state.worker_execution(self._owner, run):
            try:
                self.agent.run_state.check_stop()
                for batch in context_batches:
                    self.agent.run_state.check_stop()
                    # Reject an unbounded single unit before materializing it. The
                    # iterable itself is consumed only on this owning worker.
                    messages, provenance, _ = canonical_batch(batch)
                    batch = ContextBatch(batch.operation_id, messages, provenance)
                    manager = self.agent.runtime.context_manager
                    budget = manager.model_budget().input_budget(self.agent.runtime.max_tokens)
                    fixed = [m for m in self.agent.session.active if m.role == 'system']
                    if manager.counter.estimate([*fixed, User(message), *messages],
                                                self.agent.runtime.tools_payload()).tokens > budget:
                        raise InputTooLarge('Context batch and protected task exceed input capacity; supply bounded batches')
                    receipt = self.agent.apply_context_batch(batch)
                    if on_context_applied is not None:
                        invoke_preparation_callback(on_context_applied, receipt)
                    self.agent.run_state.check_stop()
                    # Maintain between bounded units, never collecting the complete
                    # iterable before compaction. Focus is provided to every summary.
                    for _ in self.agent.react_loop.run_events(None, prepare_only=True, focus=message):
                        pass
                if input_id is not None:
                    receipt = self.agent.apply_context_batch(ContextBatch(input_id, (User(message),),
                        input_provenance if input_provenance is not None else {'origin': 'runtime'}))
                    if on_context_applied is not None:
                        invoke_preparation_callback(on_context_applied, receipt)
                    admission = run.before_inference
                    def authorize_input():
                        if not self.agent.session.context_batch_covered(input_id, require_raw=True):
                            raise ValueError('protected current task has no complete raw coverage')
                        if admission is not None:
                            invoke_preparation_callback(admission)
                    # Only the final user anchor is protected by the normal Agent
                    # compactor. Historical input IDs cannot reactivate an old task.
                    newest = next((entry for entry in reversed(self.agent.session.context_entries())
                                   if entry.message.role == 'user'), None)
                    if newest is None or newest.entry_id not in receipt.entry_ids:
                        raise ValueError('identified input is not the protected current task')
                    run.before_inference = authorize_input
                self.agent.run_state.check_stop()
                run.terminal = None
                for _ in self.agent.run_events(None if input_id is not None else message, stream=False):
                    pass
            except BaseException as error:
                status = 'cancelled' if isinstance(error, asyncio.CancelledError) else 'error'
                if run.terminal is not None and run.terminal.status == 'timeout':
                    status = 'timeout'
                return ExecutionOutcome(status, error=f'{type(error).__name__}: {error}',
                                        phase=run.phase, error_type=type(error).__name__)
            terminal = run.terminal
            if terminal is None:
                return ExecutionOutcome('error', error='Agent run ended without a terminal event',
                                        phase=run.phase, error_type='RuntimeError')
            return ExecutionOutcome(terminal.status or 'error', terminal.result or '', terminal.error,
                                    phase=run.phase)

    async def close(self) -> None:
        with self._lock:
            if self._close_task is None:
                self._closed = True
                self._close_task = asyncio.create_task(self._close_owned())
            task = self._close_task
        await asyncio.shield(task)

    async def _close_owned(self) -> None:
        try:
            active = self._active
            if active is not None and not active.settled:
                active.request_stop()
                await active.wait()
            await asyncio.to_thread(self._executor.shutdown, wait=True)
        finally:
            self._release_agent()

    def _release_agent(self) -> None:
        # Restore the prompt before opening direct Agent admission again.
        try:
            if self._system_extension is not None:
                with self.agent.run_state.worker_execution(self._owner, WorkerRunControl()):
                    self.agent.remove_system_prompt_extension(self._system_extension)
                self._system_extension = None
        finally:
            self.agent.run_state.release(self._owner)


def _is_async_callable(value) -> bool:
    return (inspect.iscoroutinefunction(value) or inspect.isasyncgenfunction(value)
            or inspect.iscoroutinefunction(getattr(value, '__call__', None))
            or inspect.isasyncgenfunction(getattr(value, '__call__', None)))


def _validate_sync_pipeline(agent: Agent) -> None:
    runtime = agent.runtime
    tool_runtime = runtime.tool_runtime
    callables = {
        'model invoke': agent.llm.invoke,
        'session add': agent.session.add,
        'session working messages': agent.session.working_messages,
        'session system replacement': agent.session.replace_system,
        'system prompt extension install': agent.add_system_prompt_extension,
        'system prompt extension removal': agent.remove_system_prompt_extension,
        'response parser': agent.response_parser.parse,
        'runtime response parse': runtime.parse_invoke_response,
        'request preparation': runtime.prepare_messages,
        'session message admission': runtime.add_session_message,
        'tool schema': agent.registry.to_openai_tools,
        'tool dispatch': runtime.execute_tool_calls,
        'runtime tool stop check': runtime.should_stop_after_tool_calls,
        'custom tool executor': agent.tool_executor.execute,
        'tool stop check': agent.tool_executor.should_stop,
        'tool runtime execute': tool_runtime.execute,
        'tool permission check': tool_runtime.permission_policy.check,
        'tool format': tool_runtime.format_result,
        'tool audit record': tool_runtime._record_audit,
        'audit append': tool_runtime.audit_log.append,
        'registry call': agent.registry.call,
        'registry tool call': agent.registry.call_tool_call,
        'registry tool metadata': agent.registry.tool_metadata,
        'registry tool terminal check': agent.registry.ends_run,
        'context begin': runtime.context_manager.begin_run,
        'context prepare': runtime.context_manager.prepare,
        'context history maintenance': runtime.context_manager.maintain_history,
        'context usage': runtime.context_manager.observe_usage,
    }
    if agent.context_transform is not None:
        callables['context transform'] = agent.context_transform
    if tool_runtime.audit_log.sink is not None:
        callables['audit sink write'] = tool_runtime.audit_log.sink.write
    archive = agent.session.archive_store
    if archive is not None:
        for name in ('write', 'load', 'read', '_metadata', '_metadata_scan'):
            operation = getattr(archive, name, None)
            if operation is not None:
                callables[f'archive {name}'] = operation
    for name in agent.registry.names():
        callables[f'tool {name}'] = agent.registry.get(name).func
    for label, function in callables.items():
        if _is_async_callable(function):
            raise TypeError(f'async {label} is not worker safe')
