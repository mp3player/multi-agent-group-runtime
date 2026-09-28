"""Turn exhaustion remains visible and recoverable through every public API."""

import pytest

from core.agent import Agent
from core.agent_runtime.context import validate_tool_pairs
from core.agent_runtime.options import AgentOptions
from core.context_archive import FileContextArchive
from core.session import Session
from models import AI, ToolCall
from tests.runtime_fakes import ScriptedModel, execute
from tools.registry import ToolRegistry


MODES = ["run", "arun", "run_stream", "arun_stream"]
LIMIT_MESSAGE = "(Maximum turns reached without a final response)"


@pytest.mark.parametrize("mode", MODES)
async def test_turn_limit_delivers_one_final_message_after_the_complete_tool_batch(mode):
    performed = []
    registry = ToolRegistry()

    def record(value: int) -> str:
        performed.append(value)
        return f"recorded {value}"

    registry.register(record)
    model = ScriptedModel(
        AI(tool_calls=[ToolCall("one", "record", {"value": 1})]),
        AI(tool_calls=[ToolCall("two", "record", {"value": 2}),
                       ToolCall("three", "record", {"value": 3})]),
        AI("recovered"),
    )
    agent = Agent(model, registry=registry, max_turns=2)
    events = []
    agent.subscribe(events.append)

    result = await execute(agent, mode)

    assert result == LIMIT_MESSAGE
    assert performed == [1, 2, 3]
    assert len(model.requests) == 2
    assert [e.turn for e in events if e.type == "turn_start"] == [1, 2]
    assert [e.turn for e in events if e.type == "turn_end"] == [1, 2]
    assert events[-1].type == "run_end"
    assert events[-1].status == "max_turns"
    assert events[-1].result == LIMIT_MESSAGE
    assert events[-1].error is None
    assert not agent.run_state.active
    assert [m.tool_call_id for m in agent.session.history if m.role == "tool"] == ["one", "two", "three"]
    validate_tool_pairs(agent.session.history)
    assert agent.session.history[-1].role == "assistant"
    assert agent.session.history[-1].message == LIMIT_MESSAGE
    assert agent.session.active[-1].message == LIMIT_MESSAGE
    assert sum(m.message == LIMIT_MESSAGE for m in agent.session.history) == 1
    assert sum(e.type == "message_end" and e.message.message == LIMIT_MESSAGE for e in events) == 1
    assert sum(e.chunk is not None and e.chunk.message == LIMIT_MESSAGE for e in events) == int("stream" in mode)
    terminal_run = events[-1].run_id

    assert await execute(agent, mode, "continue") == "recovered"
    assert performed == [1, 2, 3]
    assert len(model.requests) == 3
    assert model.requests[-1][-2].message == LIMIT_MESSAGE
    assert model.requests[-1][-1].message == "continue"
    validate_tool_pairs(model.requests[-1])
    assert events[-1].status == "completed"
    assert events[-1].run_id != terminal_run
    assert agent.session.history[-1].message == "recovered"


@pytest.mark.parametrize("mode", ["run_stream", "arun_stream"])
async def test_turn_limit_appends_feedback_once_after_streamed_tool_preamble(mode):
    model = ScriptedModel(AI("Checking the result. ", reasoning="need a tool",
                             tool_calls=[ToolCall("missing", "unavailable", {})]))
    agent = Agent(model, max_turns=1)
    events = []
    agent.subscribe(events.append)

    assert await execute(agent, mode) == "Checking the result. " + LIMIT_MESSAGE
    assert events[-1].status == "max_turns"
    assert events[-1].result == LIMIT_MESSAGE
    assert agent.session.history[-1].message == LIMIT_MESSAGE
    assert [e.chunk.message for e in events if e.chunk is not None and e.chunk.message] == [
        "Checking the result. ", LIMIT_MESSAGE,
    ]


@pytest.mark.parametrize("mode", MODES)
async def test_completion_on_the_last_allowed_turn_has_no_limit_feedback(mode):
    model = ScriptedModel(AI(tool_calls=[ToolCall("missing", "unavailable", {})]),
                          AI("finished", reasoning="all done"))
    agent = Agent(model, max_turns=2)
    events = []
    agent.subscribe(events.append)

    assert await execute(agent, mode) == "finished"
    assert events[-1].status == "completed"
    assert events[-1].result == "finished"
    assert len(model.requests) == 2
    assert agent.session.history[-1].message == "finished"
    assert agent.session.history[-1].reasoning == "all done"
    assert all(m.message != LIMIT_MESSAGE for m in agent.session.history)
    assert sum(e.chunk is not None and e.chunk.message == "finished" for e in events) == int("stream" in mode)


@pytest.mark.parametrize("mode", MODES)
async def test_summary_calls_do_not_consume_turns_or_replace_limit_feedback(tmp_path, mode):
    class SummarizingModel(ScriptedModel):
        def invoke(self, messages, **kwargs):
            if messages[0].message.startswith("CONTEXT_COMPACTION"):
                response = AI("Done: observations archived. Next: continue remaining observations.")
                return {"choices": [{"message": response.to_dict(), "finish_reason": "stop"}]}
            return super().invoke(messages, **kwargs)

    performed = []
    registry = ToolRegistry()

    def observe() -> str:
        performed.append(len(performed) + 1)
        return f"observation {performed[-1]}: " + "detail " * 140

    registry.register(observe)
    model = SummarizingModel(
        *(AI(tool_calls=[ToolCall(str(i), "observe", {})]) for i in range(1, 7)),
        AI("recovered"),
    )
    agent = Agent(model, registry=registry,
                  session=Session(archive_store=FileContextArchive(tmp_path)),
                  options=AgentOptions(max_turns=6, context_window=4096, max_tokens=256))
    events = []
    agent.subscribe(events.append)

    assert await execute(agent, mode) == LIMIT_MESSAGE
    assert performed == [1, 2, 3, 4, 5, 6]
    assert len(model.requests) == 6
    assert [e.turn for e in events if e.type == "turn_start"] == [1, 2, 3, 4, 5, 6]
    assert any(e.type == "context_compaction_end" for e in events)
    assert agent.context_status()["summary_calls"] > 0
    assert agent.session.checkpoint is not None
    agent.session.validate_archives()
    assert agent.session.active[-1].message == LIMIT_MESSAGE
    assert agent.session.history[-1].message == LIMIT_MESSAGE
    assert events[-1].status == "max_turns"
    assert events[-1].result == LIMIT_MESSAGE
    for messages in model.requests:
        validate_tool_pairs(messages)

    assert await execute(agent, mode, "continue") == "recovered"
    assert performed == [1, 2, 3, 4, 5, 6]
    assert len(model.requests) == 7
    validate_tool_pairs(model.requests[-1])
    assert events[-1].status == "completed"
