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

from core.config import MASConfig

from typing import AsyncIterator, Iterator

from core.agent_runtime import (
    AgentReactLoop,
    AgentResponseParser,
    AgentRunState,
    AgentRuntimeAdapter,
    AgentTimeoutError,
    AgentToolExecutor,
)
from core.llm import LLMClient
from core.system_builder import SystemBuilder
from models import Chunk, System
from core.session import Session
from tools.registry import ToolRegistry


class Agent:
    """Agent。

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
    """

    def __init__(
        self,
        llm: LLMClient,
        session: Session | None = None,
        registry: ToolRegistry | None = None,
        max_turns: int = MASConfig.DEFAULT_MAX_TURNS,
        system_builder: SystemBuilder | None = None,
        system_prompt: str | None = None,
        run_timeout: float | None = None,
    ) -> None:
        self.llm = llm
        self.registry: ToolRegistry = registry if registry is not None else ToolRegistry()
        self.max_turns = max_turns
        self.active_message_limit = _active_message_limit()
        self.run_state = AgentRunState(_run_timeout(run_timeout))
        self.run_timeout = self.run_state.run_timeout
        self.response_parser = AgentResponseParser()
        self.tool_executor = AgentToolExecutor(self.registry)
        self.runtime = AgentRuntimeAdapter(self)
        self.react_loop = AgentReactLoop(self.runtime)
        self.system_builder = system_builder
        self._system_prompt: str | None = system_prompt
        # Attach the registry before building so the tool list is included.
        if system_builder is not None:
            system_builder.attach_tool_registry(self.registry)
            self._system_prompt = system_builder.build()
        # Initialize the session.
        self.session: Session = session if session is not None else Session()
        self._inject_system()

    # ----- System Prompt -----

    @property
    def system_prompt(self) -> str | None:
        """Current effective system prompt."""
        return self._system_prompt

    def rebuild_system_prompt(self) -> str | None:
        """Rebuild the system prompt and update the session system message.

        This is used after dynamic SystemBuilder changes. Returns the new system
        prompt string, or ``None`` if no prompt is configured.
        """
        if self.system_builder is None:
            return self._system_prompt
        self._system_prompt = self.system_builder.build()
        self._inject_system(replace=True)
        return self._system_prompt

    def _inject_system(self, *, replace: bool = False) -> None:
        """Inject the system prompt at the beginning of active context.

        Args:
            replace: If true, remove existing System messages from active
                before injecting. History is preserved.
        """
        if not self._system_prompt:
            return
        if replace:
            # Remove stale active System messages without altering history.
            self.session.active = [
                m for m in self.session.active if not isinstance(m, System)
            ]
        # Avoid duplicate active System injection.
        if self.session.active and isinstance(self.session.active[0], System):
            return
        sys_msg = System(self._system_prompt)
        self.session.active.insert(0, sys_msg)
        self.session.history.insert(0, sys_msg)

    # ----- Session management -----

    def set_session(self, session: Session) -> None:
        """Replace the current session and reinject the system prompt."""
        self.session = session
        self._inject_system()

    def new_session(self) -> Session:
        """Create and switch to a new empty session."""
        self.session = Session()
        self._inject_system()
        return self.session

    def reset_active_to_system(self) -> None:
        """Keep only the system prompt in active context.

        This is useful for wrappers that provide their own per-turn context
        (for example group chat). History is preserved for audit/debugging, but
        old synthetic wrapper prompts are not sent back to the model.
        """
        system_msg = next(
            (m for m in self.session.active if isinstance(m, System)),
            None,
        )
        if system_msg is None:
            system_msg = next(
                (m for m in self.session.history if isinstance(m, System)),
                None,
            )
        if system_msg is None and self._system_prompt:
            system_msg = System(self._system_prompt)
            self.session.history.insert(0, system_msg)
        self.session.active = [system_msg] if system_msg is not None else []

    # ----- Tool management -----

    def set_registry(self, registry: ToolRegistry) -> None:
        """Replace the tool registry and sync it to the SystemBuilder."""
        self.registry = registry
        self.tool_executor = AgentToolExecutor(self.registry)
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

    async def _arun_unbounded(self, message: str) -> str:
        return await self.react_loop._arun_unbounded(message)

    # ----- Async streaming -----

    async def arun_stream(self, message: str) -> AsyncIterator[Chunk]:
        """Run one asynchronous streaming ReAct turn."""
        async for chunk in self.react_loop.arun_stream(message):
            yield chunk


def _active_message_limit() -> int | None:
    return MASConfig.ACTIVE_MESSAGE_LIMIT


def _run_timeout(value: float | None = None) -> float | None:
    timeout = MASConfig.AGENT_RUN_TIMEOUT if value is None else value
    return timeout if timeout > 0 else None
