"""Runtime adapter for the public ``Agent`` facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.config import MASConfig
from models import Message, ToolCall, User

if TYPE_CHECKING:
    from core.agent import Agent


@dataclass(slots=True)
class AgentRuntimeAdapter:
    """Narrow runtime access to an ``Agent`` instance."""

    agent: "Agent"

    @property
    def llm(self) -> Any:
        return self.agent.llm

    @property
    def max_turns(self) -> int:
        return self.agent.max_turns

    @property
    def active_message_limit(self) -> int | None:
        return self.agent.active_message_limit

    @property
    def max_tokens(self) -> int:
        return MASConfig.DEFAULT_MAX_TOKENS

    @property
    def run_timeout(self) -> float | None:
        return self.agent.run_state.run_timeout

    def tools_payload(self) -> list[dict[str, Any]] | None:
        tools = self.agent.registry.to_openai_tools()
        return tools or None

    def build_invoke_messages(self, message: str) -> list[Message]:
        self.agent.session.prune_active(self.active_message_limit)
        self.agent.session.add(User(message))
        return self.agent.session.active_messages()

    def active_messages(self) -> list[Message]:
        return self.agent.session.active_messages()

    def add_session_message(self, message: Message) -> None:
        self.agent.session.add(message)

    def execute_tool_calls(self, tool_calls: list[ToolCall]) -> list[Message]:
        return self.agent.tool_executor.execute(tool_calls)

    def should_stop_after_tool_calls(self, tool_calls: list[ToolCall]) -> bool:
        return self.agent.tool_executor.should_stop(tool_calls)

    def prune_active(self) -> None:
        self.agent.session.prune_active(self.active_message_limit)

    def parse_invoke_response(self, data: dict[str, Any]):
        return self.agent.response_parser.parse(data)

    def deadline(self) -> float | None:
        return self.agent.run_state.deadline()

    def check_deadline(self, deadline: float | None) -> None:
        self.agent.run_state.check_deadline(deadline)
