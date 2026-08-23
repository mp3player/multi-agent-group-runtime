"""Single-agent application service."""

from __future__ import annotations

from dataclasses import dataclass, field

import application.builders as builders
from application.config import AppConfig
from application.member_config_service import app_config_from_options
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

    config: AppConfig
    use_stream: bool = True
    agent: Agent = field(init=False)

    def __post_init__(self) -> None:
        self.agent = builders.build_agent(self.config)

    @classmethod
    def from_options(
        cls,
        options: AgentServiceOptions | None = None,
        *,
        max_turns: int | None = None,
        enable_tools: bool | None = None,
        use_stream: bool | None = None,
    ) -> "AgentAppService":
        if options is None:
            if max_turns is None or enable_tools is None:
                raise TypeError("max_turns and enable_tools are required")
            options = AgentServiceOptions(
                max_turns=max_turns,
                enable_tools=enable_tools,
                use_stream=True if use_stream is None else use_stream,
            )
        return cls(
            app_config_from_options(
                max_turns=options.max_turns,
                enable_tools=options.enable_tools,
            ),
            use_stream=options.use_stream,
        )

    def clear_session(self) -> None:
        self.agent.new_session()

    def new_session(self) -> None:
        self.agent.new_session()

    def toggle_stream(self) -> bool:
        self.use_stream = not self.use_stream
        return self.use_stream

    def tool_names(self) -> list[str]:
        return self.agent.registry.names()

    def tool_audit_records(self, *, limit: int | None = None) -> list[object]:
        return self.agent.registry.tool_audit_log.records(limit)

    def history_counts(self) -> tuple[int, int]:
        session = self.agent.session
        return len(session.history), len(session.active)

    def history_entries(self, *, text_limit: int = 80) -> list[HistoryEntry]:
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


__all__ = [
    "AgentAppService",
    "HistoryEntry",
]
