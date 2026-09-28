"""Single-agent application service."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from contextlib import contextmanager, closing, aclosing
from threading import RLock
from typing import AsyncIterator, Iterator
from pathlib import Path
from core.session import Session
from core.session_store import JsonSessionStore
from models import Chunk
from core.agent_runtime.ports import ModelClient
from core.agent_runtime.run_state import AgentBusyError
from core.usage import UsageMonitor, UsageRecord

import application.agent_builder as builders
from application.agent_config import AgentAppConfig
from application.options import AgentServiceOptions
from core.agent import Agent


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One stable CLI-facing session history entry."""

    index: int
    role: str
    text: str


@dataclass(slots=True)
class AgentAppService:
    """Application service for one interactive single-agent session."""

    config: AgentAppConfig
    use_stream: bool = True
    model: ModelClient | None = None
    owns_model: bool | None = None
    agent: Agent = field(init=False)
    _usage_monitor: UsageMonitor = field(init=False, default_factory=UsageMonitor)
    _lock: RLock = field(init=False, default_factory=RLock, repr=False)
    _state: str = field(init=False, default="open")
    _running: bool = field(init=False, default=False)
    _close_task: asyncio.Task[None] | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        if self.owns_model is None:
            self.owns_model = self.model is None
        self.agent = builders.build_agent(
            self.config, model=self.model, usage_monitor=self._usage_monitor,
        )
        self.model = self.agent.llm

    @classmethod
    def from_options(
        cls,
        options: AgentServiceOptions | None = None,
        *,
        max_turns: int | None = None,
        enable_tools: bool | None = None,
        use_stream: bool | None = None,
    ) -> "AgentAppService":
        """Compatibility factory; prefer an explicit AgentAppConfig for new code."""
        if options is None:
            if max_turns is None or enable_tools is None:
                raise TypeError("max_turns and enable_tools are required")
            options = AgentServiceOptions(
                max_turns=max_turns,
                enable_tools=enable_tools,
                use_stream=True if use_stream is None else use_stream,
            )
        return cls(
            AgentAppConfig.from_env().with_agent_options(
                max_turns=options.max_turns,
                enable_tools=options.enable_tools,
            ),
            use_stream=options.use_stream,
        )

    def clear_session(self) -> None:
        self.new_session()

    def new_session(self) -> None:
        with self._lock:
            self._ensure_open()
            if self._running:
                raise AgentBusyError("Agent is already running")
            self.agent.set_session(Session(
                history_limit=self.config.agent.history_message_limit,
                archive_store=self.agent.session.archive_store,
            ))

    def save_session(self, path: str | Path) -> None:
        with self._lock:
            self._ensure_open()
            if self._running:
                raise AgentBusyError("Agent is already running")
            with self.agent.run_state.mutation():
                JsonSessionStore(archive_store=self.agent.session.archive_store).save(self.agent.session, path)

    def load_session(self, path: str | Path) -> None:
        with self._lock:
            self._ensure_open()
            if self._running:
                raise AgentBusyError("Agent is already running")
            with self.agent.run_state.mutation():
                restored = JsonSessionStore(archive_store=self.agent.session.archive_store).load(path)
                self.agent.session = restored

    def context_status(self) -> dict:
        with self._lock:
            self._ensure_open()
            status = self.agent.context_status()
            if status.get('capacity') is None:
                status['reason'] = (
                    'Model context capacity is unknown; set MAS_CONTEXT_WINDOW to the '
                    'provider-documented token capacity before inference.'
                )
            status['migration_notice'] = (
                'MAS_ACTIVE_MESSAGE_LIMIT no longer trims messages; '
                'configure MAS_CONTEXT_WINDOW for token-budget compaction.'
            )
            return status

    def compact(self, focus: str = '') -> str:
        with self._run():
            return self.agent.compact(focus)

    async def acompact(self, focus: str = '') -> str:
        with self._run():
            return await self.agent.acompact(focus)

    def toggle_stream(self) -> bool:
        with self._lock:
            self._ensure_open()
            self.use_stream = not self.use_stream
            return self.use_stream

    def tool_names(self) -> list[str]:
        with self._lock:
            self._ensure_open()
            return self.agent.registry.names()

    def tool_audit_records(self, *, limit: int | None = None) -> list[object]:
        with self._lock:
            self._ensure_open()
            return self.agent.registry.tool_audit_log.records(limit)

    def history_counts(self) -> tuple[int, int]:
        with self._lock:
            self._ensure_open()
            session = self.agent.session
            return len(session.history), len(session.active)

    def history_entries(self, *, text_limit: int = 80) -> list[HistoryEntry]:
        with self._lock:
            self._ensure_open()
            entries: list[HistoryEntry] = []
            for index, message in enumerate(self.agent.session.history):
                text = message.message
                if len(text) > text_limit:
                    text = text[: max(0, text_limit - 3)] + "..."
                entries.append(HistoryEntry(
                    index=index,
                    role=message.role,
                    text=text.replace("\n", " "),
                ))
            return entries

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._state == "closed"

    def _ensure_open(self) -> None:
        if self._state != "open":
            raise RuntimeError(f"Agent application is {self._state}")

    @contextmanager
    def _run(self):
        with self._lock:
            self._ensure_open()
            if self._running:
                raise AgentBusyError("Agent is already running")
            self._running = True
        try:
            yield
        finally:
            with self._lock:
                self._running = False

    def run(self, message: str) -> str:
        with self._run():
            return self.agent.run(message)

    def run_stream(self, message: str) -> Iterator[Chunk]:
        with self._run(), closing(self.agent.run_stream(message)) as stream:
            yield from stream

    async def arun(self, message: str) -> str:
        with self._run():
            return await self.agent.arun(message)

    async def arun_stream(self, message: str) -> AsyncIterator[Chunk]:
        with self._run():
            async with aclosing(self.agent.arun_stream(message)) as stream:
                async for chunk in stream:
                    yield chunk

    def usage_records(self) -> list[UsageRecord]:
        with self._lock:
            self._ensure_open()
            return self._usage_monitor.records()

    def usage_summary(self) -> dict[str, UsageRecord]:
        with self._lock:
            self._ensure_open()
            return self._usage_monitor.summary()

    def clear_usage(self) -> None:
        with self._lock:
            self._ensure_open()
            self._usage_monitor.clear()

    async def aclose(self) -> None:
        """Await shared cleanup without cancelling it when one waiter cancels.

        Failed cleanup keeps runs blocked and may be retried. The embedding
        application must keep the owning event loop alive until cleanup ends.
        """
        with self._lock:
            if self._state == "closed":
                return
            if self._state != "closing":
                if self._running:
                    raise AgentBusyError("Agent is already running")
                with self.agent.run_state.mutation():
                    self._state = "closing"
                    self._close_task = asyncio.create_task(self._close_model())
                    # A cancelled waiter might never come back to retrieve an
                    # error. Observing it here does not hide it from awaiters.
                    self._close_task.add_done_callback(
                        lambda task: None if task.cancelled() else task.exception()
                    )
            task = self._close_task
        await asyncio.shield(task)

    async def _close_model(self) -> None:
        try:
            if self.owns_model:
                close = getattr(self.model, "aclose", None)
                if close is not None:
                    await close()
        except BaseException:
            with self._lock:
                self._state = "close_failed"
            raise
        else:
            with self._lock:
                self._state = "closed"

    async def __aenter__(self) -> "AgentAppService":
        with self._lock:
            self._ensure_open()
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        await self.aclose()


__all__ = [
    "AgentAppService",
    "HistoryEntry",
]
