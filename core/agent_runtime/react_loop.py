"""ReAct execution loops for single-agent runs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

from models import AI, Chunk, ToolCall

from core.agent_runtime.run_state import AgentTimeoutError
from core.agent_runtime.runtime_adapter import AgentRuntimeAdapter

FALLBACK_MESSAGE = "(达到最大轮数，未获得最终回复)"


@dataclass(slots=True)
class AgentReactLoop:
    """Run sync/async ReAct loops through a runtime adapter."""

    runtime: AgentRuntimeAdapter

    def run(self, message: str) -> str:
        """Run one non-streaming sync ReAct exchange."""
        deadline = self.runtime.deadline()
        messages = self.runtime.build_invoke_messages(message)
        tools = self.runtime.tools_payload()

        for _ in range(self.runtime.max_turns):
            self.runtime.check_deadline(deadline)
            data = self.runtime.llm.invoke(
                messages,
                max_tokens=self.runtime.max_tokens,
                tools=tools,
                tool_choice="auto" if tools else None,
            )
            self.runtime.check_deadline(deadline)
            ai, tool_calls = self.runtime.parse_invoke_response(data)

            if not tool_calls:
                self.runtime.add_session_message(ai)
                return ai.message

            self._record_tool_round(ai, tool_calls, deadline)
            if self.runtime.should_stop_after_tool_calls(tool_calls):
                return ""
            messages = self._refresh_messages()

        return self._record_fallback()

    def run_stream(self, message: str) -> Iterator[Chunk]:
        """Run one streaming sync ReAct exchange."""
        deadline = self.runtime.deadline()
        messages = self.runtime.build_invoke_messages(message)
        tools = self.runtime.tools_payload()

        for _ in range(self.runtime.max_turns):
            self.runtime.check_deadline(deadline)
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            final_tool_calls: list[ToolCall] | None = None

            for chunk in self.runtime.llm.stream(
                messages,
                max_tokens=self.runtime.max_tokens,
                tools=tools,
                tool_choice="auto" if tools else None,
            ):
                self.runtime.check_deadline(deadline)
                if chunk.tool_calls:
                    final_tool_calls = chunk.tool_calls
                    yield chunk
                    continue
                if chunk.finish_reason:
                    continue
                if chunk.message:
                    content_parts.append(chunk.message)
                    yield chunk
                if chunk.reasoning:
                    reasoning_parts.append(chunk.reasoning)
                    yield chunk

            ai = AI(
                message="".join(content_parts),
                reasoning="".join(reasoning_parts),
                tool_calls=final_tool_calls,
            )
            if not final_tool_calls:
                self.runtime.add_session_message(ai)
                return

            self._record_tool_round(ai, final_tool_calls, deadline)
            if self.runtime.should_stop_after_tool_calls(final_tool_calls):
                return
            messages = self._refresh_messages()
        return

    async def arun(self, message: str) -> str:
        """Run one non-streaming async ReAct exchange."""
        timeout = self.runtime.run_timeout
        if timeout is not None and timeout > 0:
            try:
                return await asyncio.wait_for(
                    self._arun_unbounded(message),
                    timeout,
                )
            except TimeoutError as exc:
                raise AgentTimeoutError(
                    f"Agent run exceeded timeout {timeout:g}s"
                ) from exc
        return await self._arun_unbounded(message)

    async def _arun_unbounded(self, message: str) -> str:
        messages = self.runtime.build_invoke_messages(message)
        tools = self.runtime.tools_payload()

        for _ in range(self.runtime.max_turns):
            data = await self.runtime.llm.ainvoke(
                messages,
                max_tokens=self.runtime.max_tokens,
                tools=tools,
                tool_choice="auto" if tools else None,
            )
            ai, tool_calls = self.runtime.parse_invoke_response(data)

            if not tool_calls:
                self.runtime.add_session_message(ai)
                return ai.message

            self._record_tool_round(ai, tool_calls, None)
            if self.runtime.should_stop_after_tool_calls(tool_calls):
                return ""
            messages = self._refresh_messages()

        return self._record_fallback()

    async def arun_stream(self, message: str) -> AsyncIterator[Chunk]:
        """Run one streaming async ReAct exchange."""
        deadline = self.runtime.deadline()
        messages = self.runtime.build_invoke_messages(message)
        tools = self.runtime.tools_payload()

        for _ in range(self.runtime.max_turns):
            self.runtime.check_deadline(deadline)
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            final_tool_calls: list[ToolCall] | None = None

            async for chunk in self.runtime.llm.astream(
                messages,
                max_tokens=self.runtime.max_tokens,
                tools=tools,
                tool_choice="auto" if tools else None,
            ):
                self.runtime.check_deadline(deadline)
                if chunk.tool_calls:
                    final_tool_calls = chunk.tool_calls
                    yield chunk
                    continue
                if chunk.finish_reason:
                    continue
                if chunk.message:
                    content_parts.append(chunk.message)
                    yield chunk
                if chunk.reasoning:
                    reasoning_parts.append(chunk.reasoning)
                    yield chunk

            ai = AI(
                message="".join(content_parts),
                reasoning="".join(reasoning_parts),
                tool_calls=final_tool_calls,
            )
            if not final_tool_calls:
                self.runtime.add_session_message(ai)
                return

            self._record_tool_round(ai, final_tool_calls, deadline)
            if self.runtime.should_stop_after_tool_calls(final_tool_calls):
                return
            messages = self._refresh_messages()
        return

    def _record_tool_round(
        self,
        ai: AI,
        tool_calls: list[ToolCall],
        deadline: float | None,
    ) -> None:
        self.runtime.add_session_message(ai)
        tool_results = self.runtime.execute_tool_calls(tool_calls)
        self.runtime.check_deadline(deadline)
        for result in tool_results:
            self.runtime.add_session_message(result)

    def _refresh_messages(self):
        self.runtime.prune_active()
        return self.runtime.active_messages()

    def _record_fallback(self) -> str:
        fallback = AI(message=FALLBACK_MESSAGE)
        self.runtime.add_session_message(fallback)
        return fallback.message
