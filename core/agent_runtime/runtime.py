"""Owned execution state with no dependency on the Agent facade."""

from __future__ import annotations

from typing import Any
from functools import cached_property

from core.session import Session
from tools.registry import ToolRegistry
from tools.runtime import ToolRuntime
from core.agent_runtime.options import AgentOptions
from core.agent_runtime.run_state import AgentRunState
from core.agent_runtime.events import AgentEventBus
from core.agent_runtime.response_parser import AgentResponseParser
from core.agent_runtime.tool_executor import AgentToolExecutor
from core.agent_runtime.context import ContextTransform
from models import Message, ToolCall
from core.agent_runtime.context import project_context
from core.agent_runtime.events import AgentEvent
from core.agent_runtime.ports import ModelClient
from core.agent_runtime.context_management.manager import ContextManager
from core.agent_runtime.context_management.archive_tool import bind_context_archive_tool

class AgentRuntime:
    """Own dependencies and state. Direct Session mutation during runs is unsupported."""

    def __init__(self, llm: ModelClient, session: Session, registry: ToolRegistry,
                 options: AgentOptions, *, context_transform: ContextTransform | None = None,
                 tool_runtime: ToolRuntime | None = None, token_counter=None):
        self.run_state = AgentRunState(options.run_timeout)
        self._llm = llm
        self._session = session
        self._options = options
        self._context_transform = context_transform
        self.event_bus = AgentEventBus()
        if tool_runtime is not None and tool_runtime.registry is not registry:
            raise ValueError('registry must match the injected tool runtime')
        self._tool_runtime = tool_runtime if tool_runtime is not None else ToolRuntime(registry)
        self.context_manager = ContextManager(self, token_counter)
        self._bind_context_archive(self.registry, session)

    def _bind_context_archive(self, registry, session):
        if session.archive_store is not None:
            bind_context_archive_tool(registry, lambda: self.session, owner=self)

    @cached_property
    def response_parser(self):
        """Lazily create the parser used by non-streaming execution."""
        return AgentResponseParser()

    @cached_property
    def tool_executor(self):
        """Lazily create a compatibility view of the owned tool runtime."""
        return _BoundToolExecutor(self)

    @property
    def tool_runtime(self) -> ToolRuntime:
        return self._tool_runtime

    @tool_runtime.setter
    def tool_runtime(self, value: ToolRuntime) -> None:
        with self.run_state.mutation():
            self._bind_context_archive(value.registry, self.session)
            self._tool_runtime = value

    @property
    def llm(self):
        return self._llm

    @llm.setter
    def llm(self, value):
        with self.run_state.mutation():
            self._llm = value

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, value):
        with self.run_state.mutation():
            self._bind_context_archive(self.registry, value)
            self._session = value

    @property
    def registry(self):
        return self.tool_runtime.registry

    @registry.setter
    def registry(self, value):
        with self.run_state.mutation():
            self._bind_context_archive(value, self.session)
            self.tool_runtime.set_registry(value)

    @property
    def options(self):
        return self._options

    @options.setter
    def options(self, value):
        with self.run_state.mutation():
            if not isinstance(value, AgentOptions):
                raise TypeError('options must be AgentOptions')
            self._options = value
            self.run_state.run_timeout = value.run_timeout

    @property
    def context_transform(self):
        return self._context_transform

    @context_transform.setter
    def context_transform(self, value):
        with self.run_state.mutation():
            self._context_transform = value

    def enter_run(self) -> None:
        self.run_state.enter_run()

    def exit_run(self) -> None:
        self.run_state.exit_run()

    def publish(self, event: AgentEvent) -> None:
        self.event_bus.publish(event)

    @property
    def max_turns(self) -> int:
        return self.options.max_turns

    @property
    def max_tokens(self) -> int:
        return self.options.max_tokens

    @property
    def run_timeout(self) -> float | None:
        return self.run_state.run_timeout

    @property
    def temperature(self) -> float:
        return self.options.temperature

    def tools_payload(self) -> list[dict[str, Any]] | None:
        tools = self.registry.to_openai_tools()
        return tools or None

    def prepare_messages(self) -> list[Message]:
        return project_context(self.session.working_messages(), self.context_transform)

    def add_session_message(self, message: Message) -> None:
        self.session.add(message)

    def execute_tool_calls(self, tool_calls: list[ToolCall]) -> list[Message]:
        return self.tool_executor.execute(tool_calls)

    def should_stop_after_tool_calls(self, results: list[Message]) -> bool:
        return self.tool_executor.should_stop(results)

    def parse_invoke_response(self, data: dict[str, Any]):
        return self.response_parser.parse(data)

    def deadline(self) -> float | None:
        return self.run_state.deadline()

    def check_deadline(self, deadline: float | None) -> None:
        self.run_state.check_deadline(deadline)


class _BoundToolExecutor(AgentToolExecutor):
    """Forward compatibility access to one owner, without a second runtime reference."""

    def __init__(self, owner: AgentRuntime) -> None:
        self._owner = owner

    @property
    def runtime(self) -> ToolRuntime:
        return self._owner.tool_runtime

    @runtime.setter
    def runtime(self, value: ToolRuntime) -> None:
        self._owner.tool_runtime = value

    @property
    def registry(self) -> ToolRegistry:
        return self._owner.registry

    @registry.setter
    def registry(self, value: ToolRegistry) -> None:
        self._owner.registry = value
