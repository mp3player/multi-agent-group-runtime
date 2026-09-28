"""Agent facade holding an LLM client, session, and tool registry.

It implements the ReAct flow:
user -> tool call -> result -> tool call -> ... -> final AI response.

LLM execution methods:
    run          synchronous non-streaming
    run_stream   synchronous streaming, yields Chunk
    arun         asynchronous non-streaming
    arun_stream  asynchronous streaming, async-yields Chunk
"""

from __future__ import annotations

from dataclasses import replace

from collections.abc import Callable
from contextlib import aclosing, closing
from typing import AsyncIterator, Iterator

from core.agent_runtime import (
    AgentReactLoop,
    AgentRuntime,
    AgentOptions,
    AgentTimeoutError,
)
from core.agent_runtime.context import ContextTransform
from core.agent_runtime.events import AgentEvent, EventListener
from core.agent_runtime.ports import ModelClient
from core.system_builder import SystemBuilder
from models import Chunk, System
from core.session import Session
from core.context_batch import ContextBatch, ContextReceipt
from tools.registry import ToolRegistry
from tools.runtime import ToolRuntime


class Agent:
    """Agent.

    Args:
        llm: LLM client.
        session: Initial session. A new empty Session is created by default.
        registry: Tool registry. A new empty ToolRegistry is created by default.
        max_turns: ReAct turn limit used to prevent unbounded loops.
        system_builder: System prompt builder. If provided, its ``build()``
            output is injected as the session system prompt. ``system_prompt``
            can also be passed directly.
        system_prompt: Direct system prompt string. Lower priority than
            ``system_builder``.
        options: Immutable instance execution and model-request options. Non-None
            legacy max_turns/run_timeout arguments override these values.
        context_transform: Optional synchronous request projection. Receives
            detached messages and must preserve valid tool-call/result pairs.
        tool_runtime: Fully configured tool executor. If registry is also supplied,
            it must be the same registry owned by this runtime.
    """

    def __init__(
        self,
        llm: ModelClient,
        session: Session | None = None,
        registry: ToolRegistry | None = None,
        max_turns: int | None = None,
        system_builder: SystemBuilder | None = None,
        system_prompt: str | None = None,
        run_timeout: float | None = None,
        *,
        options: AgentOptions | None = None,
        context_transform: ContextTransform | None = None,
        tool_runtime: ToolRuntime | None = None,
        archive_store=None,
        token_counter=None,
    ) -> None:
        config = options if options is not None else AgentOptions()
        if max_turns is not None:
            config = replace(config, max_turns=max_turns)
        if run_timeout is not None:
            config = replace(config, run_timeout=run_timeout)
        if registry is None:
            registry = tool_runtime.registry if tool_runtime is not None else ToolRegistry()
        if session is None:
            session = Session(archive_store=archive_store)
        elif archive_store is not None:
            session.archive_store = archive_store
        self.runtime = AgentRuntime(
            llm, session,
            registry, config,
            context_transform=context_transform,
            tool_runtime=tool_runtime,
            token_counter=token_counter,
        )
        self.react_loop = AgentReactLoop(self.runtime)
        self._system_prompt_extensions: dict[object, str] = {}
        self._inherited_system_prompt = self._session_system_prompt(session)
        self._system_builder = system_builder
        self._system_prompt = system_prompt
        self._system_prompt_explicit = system_prompt is not None or system_builder is not None
        if system_builder is not None:
            system_builder.attach_tool_registry(self.registry)
            self._system_prompt = system_builder.build()
        self._inject_system(replace=self._system_prompt_explicit)

    @property
    def llm(self):
        return self.runtime.llm

    @llm.setter
    def llm(self, value):
        self.runtime.llm = value

    @property
    def session(self) -> Session:
        """Low-level mutable session; direct mutation during a run is unsupported."""
        return self.runtime.session

    @session.setter
    def session(self, value: Session):
        self.set_session(value)

    @property
    def registry(self) -> ToolRegistry:
        return self.runtime.registry

    @registry.setter
    def registry(self, value: ToolRegistry):
        self.set_registry(value)

    @property
    def options(self) -> AgentOptions:
        return self.runtime.options

    @options.setter
    def options(self, value: AgentOptions):
        self.runtime.options = value

    @property
    def context_transform(self):
        return self.runtime.context_transform

    @context_transform.setter
    def context_transform(self, value):
        self.runtime.context_transform = value

    @property
    def max_turns(self):
        return self.options.max_turns

    @max_turns.setter
    def max_turns(self, value):
        with self.run_state.mutation():
            self.options = replace(self.options, max_turns=value)

    @property
    def active_message_limit(self):
        return self.options.active_message_limit

    @active_message_limit.setter
    def active_message_limit(self, value):
        with self.run_state.mutation():
            self.options = replace(self.options, active_message_limit=value)

    @property
    def run_timeout(self):
        return self.options.run_timeout

    @run_timeout.setter
    def run_timeout(self, value):
        with self.run_state.mutation():
            self.options = replace(self.options, run_timeout=value)

    @property
    def run_state(self):
        return self.runtime.run_state

    @property
    def event_bus(self):
        return self.runtime.event_bus

    @property
    def response_parser(self):
        return self.runtime.response_parser

    @property
    def tool_executor(self):
        return self.runtime.tool_executor

    @property
    def system_builder(self):
        return self._system_builder

    @system_builder.setter
    def system_builder(self, value):
        with self.run_state.mutation():
            self._system_builder = value
            if value is not None:
                value.attach_tool_registry(self.registry)
                self.rebuild_system_prompt()

    # ----- System Prompt -----

    @property
    def system_prompt(self) -> str | None:
        """Current effective system prompt."""
        return self._compose_system_prompt() if self._system_prompt_extensions else self._system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str | None):
        with self.run_state.mutation():
            self._system_prompt = value
            self._system_prompt_explicit = True
            self._inject_system(replace=True)

    def rebuild_system_prompt(self) -> str | None:
        """Rebuild the system prompt and update the session system message.

        This is used after dynamic SystemBuilder changes. Returns the new system
        prompt string, or ``None`` if no prompt is configured.
        """
        with self.run_state.mutation():
            if self.system_builder is None:
                return self.system_prompt
            self._system_prompt = self.system_builder.build()
            self._system_prompt_explicit = True
            self._inject_system(replace=True)
            return self.system_prompt

    @staticmethod
    def _session_system_prompt(session: Session) -> str | None:
        # Audit history includes retired instructions; only the active view
        # can supply an inherited base for a newly attached session.
        active = [m.message for m in session.active if isinstance(m, System)]
        return '\n\n'.join(active) or None

    def _base_system_prompt(self) -> str | None:
        return self._system_prompt if self._system_prompt_explicit else self._inherited_system_prompt

    def _compose_system_prompt(self) -> str | None:
        parts = [self._base_system_prompt(), *self._system_prompt_extensions.values()]
        return '\n\n'.join(part for part in parts if part) or None

    def add_system_prompt_extension(self, instructions: str) -> object:
        """Append trusted fixed instructions and return an opaque removal handle.

        Extensions participate in context budgeting and survive compaction. They
        leave the configured base prompt/builder unchanged and require idle ownership.
        """
        if not isinstance(instructions, str) or not instructions.strip():
            raise ValueError('System prompt extension must be nonempty text')
        with self.run_state.mutation():
            if not self._system_prompt_extensions and not self._system_prompt_explicit:
                self._inherited_system_prompt = self._session_system_prompt(self.session)
            handle = object()
            self._system_prompt_extensions[handle] = instructions
            try:
                self._inject_system(replace=True)
            except BaseException:
                del self._system_prompt_extensions[handle]
                raise
            return handle

    def remove_system_prompt_extension(self, handle: object) -> None:
        """Remove exactly one owned extension, retaining the base and other layers."""
        with self.run_state.mutation():
            if handle not in self._system_prompt_extensions:
                raise KeyError('Unknown system prompt extension')
            previous = self._system_prompt_extensions
            self._system_prompt_extensions = {key: value for key, value in previous.items() if key is not handle}
            try:
                self._inject_system(replace=True)
            except BaseException:
                self._system_prompt_extensions = previous
                raise

    def _inject_system(self, *, replace: bool = False) -> None:
        """Inject the system prompt at the beginning of active context.

        Args:
            replace: If true, remove existing System messages from active
                before injecting. History is preserved.
        """
        prompt = self._compose_system_prompt()
        if replace or self._system_prompt_extensions:
            self.session.replace_system(prompt)
        elif prompt and not any(isinstance(m, System) for m in self.session.active):
            self.session.replace_system(prompt)

    # ----- Session management -----

    def apply_context_batch(self, batch: ContextBatch) -> ContextReceipt:
        """Apply detached identified context without model or tool execution."""
        with self.run_state.mutation():
            return self.session.apply_context_batch(batch)

    def set_session(self, session: Session) -> None:
        """Replace the current session and reinject the system prompt."""
        with self.run_state.mutation():
            if session is not self.session:
                inherited = self._session_system_prompt(session)
                previous = self.session
                self.runtime.session = session
                if self._system_prompt_extensions:
                    previous.replace_system(self._base_system_prompt())
                self._inherited_system_prompt = inherited
            else:
                self.runtime.session = session
            self._inject_system(replace=self._system_prompt_explicit)

    def new_session(self) -> Session:
        """Create and switch to a new empty session."""
        with self.run_state.mutation():
            self.set_session(Session(history_limit=self.session.history_limit, archive_store=self.session.archive_store))
            return self.session

    def reset_active_to_system(self) -> None:
        """Keep only the system prompt in active context.

        This is useful for wrappers that provide their own per-turn context
        from an outer application. History is preserved for audit/debugging,
        but old synthetic wrapper prompts are not sent back to the model.
        """
        with self.run_state.mutation():
            if self._system_prompt_explicit or self._system_prompt_extensions:
                self._inject_system(replace=True)
            elif not any(isinstance(m, System) for m in self.session.active):
                # Audit history may include removed extensions. Only restore
                # the inherited base captured when this session was attached.
                self.session.replace_system(self._inherited_system_prompt)
            self.session.reset_to_system()

    def context_status(self) -> dict:
        with self.run_state.mutation():
            return self.runtime.context_manager.status()

    def compact(self, focus: str = '') -> str:
        result = ''
        with closing(self.react_loop.run_events(None, compact=True, focus=focus)) as events:
            for event in events:
                if event.type == 'run_end':
                    result = event.result or ''
        return result

    async def acompact(self, focus: str = '') -> str:
        result = ''
        async with aclosing(self.react_loop.arun_events(None, compact=True, focus=focus)) as events:
            async for event in events:
                if event.type == 'run_end':
                    result = event.result or ''
        return result

    # ----- Tool management -----

    def set_registry(self, registry: ToolRegistry) -> None:
        """Replace the tool registry and sync it to the SystemBuilder."""
        with self.run_state.mutation():
            self.runtime.registry = registry
            if self.system_builder is not None:
                self.system_builder.attach_tool_registry(registry)
                self.rebuild_system_prompt()

    # ----- Sync non-streaming -----

    def run(self, message: str) -> str:
        """Run one synchronous non-streaming ReAct turn.

        Returns the final AI response text. Intermediate tool calls are recorded
        in ``session.history``.
        """
        return self.react_loop.run(message)

    # ----- Sync streaming -----

    def run_stream(self, message: str) -> Iterator[Chunk]:
        """Run one synchronous streaming ReAct turn.

        Yielded chunks may contain:
            - message / reasoning deltas
            - tool_calls after the stream ends
            - finish_reason

        After each stream turn, any tool calls are executed and the loop
        continues until there are no tool calls.
        """
        yield from self.react_loop.run_stream(message)

    # ----- Async non-streaming -----

    async def arun(self, message: str) -> str:
        """Run one asynchronous non-streaming ReAct turn."""
        return await self.react_loop.arun(message)

    # ----- Async streaming -----

    async def arun_stream(self, message: str) -> AsyncIterator[Chunk]:
        """Run one asynchronous streaming ReAct turn."""
        async with aclosing(self.react_loop.arun_stream(message)) as chunks:
            async for chunk in chunks:
                yield chunk

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        """Observe detached events synchronously; return an idempotent unsubscribe."""
        return self.event_bus.subscribe(listener)

    def run_events(self, message: str, *, stream: bool = False) -> Iterator[AgentEvent]:
        """Run the Agent and consume typed events instead of only final text."""
        yield from self.react_loop.run_events(message, stream=stream)

    async def arun_events(self, message: str, *, stream: bool = False) -> AsyncIterator[AgentEvent]:
        """Async event surface with the same cleanup semantics as arun."""
        async with aclosing(self.react_loop.arun_events(message, stream=stream)) as events:
            async for event in events:
                yield event
