"""Legacy count limits never discard active messages."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.agent import Agent
from core.agent_runtime import AgentOptions
from core.agent_runtime.context import validate_tool_pairs
from core.session import Session
from models import AI, Message, System, ToolCall, User
from runtime_fakes import ScriptedModel


def tool_result(call_id: str, message: str) -> Message:
    result = Message("tool", message)
    result.tool_call_id = call_id
    return result


def test_current_user_turn_can_soft_overflow_limit_with_two_tool_results() -> None:
    session = Session()
    session.add_many([
        System("rules"),
        User("task"),
        AI("calling", tool_calls=[
            ToolCall("one", "first", {}),
            ToolCall("two", "second", {}),
        ]),
        tool_result("one", "first result"),
        tool_result("two", "second result"),
    ])

    session.prune_active(2)

    assert [message.message for message in session.active] == [
        "rules", "task", "calling", "first result", "second result",
    ]
    validate_tool_pairs(session.active)


def test_consecutive_tool_rounds_stay_with_their_user_turn() -> None:
    session = Session()
    session.add_many([
        User("task"),
        AI("round one", tool_calls=[ToolCall("one", "first", {})]),
        tool_result("one", "first result"),
        AI("round two", tool_calls=[ToolCall("two", "second", {})]),
        tool_result("two", "second result"),
        AI("done"),
    ])

    session.prune_active(1)

    assert [message.message for message in session.active] == [
        "task", "round one", "first result", "round two", "second result", "done",
    ]
    validate_tool_pairs(session.active)


@pytest.mark.parametrize(
    ("with_system", "limit", "expected"),
    [
        (True, 4, ["rules", "old", "old answer", "recent", "recent answer", "latest"]),
        (True, 3, ["rules", "old", "old answer", "recent", "recent answer", "latest"]),
        (False, 3, ["old", "old answer", "recent", "recent answer", "latest"]),
        (False, 2, ["old", "old answer", "recent", "recent answer", "latest"]),
    ],
)
def test_message_count_limit_preserves_all_older_user_turns(
    with_system: bool,
    limit: int,
    expected: list[str],
) -> None:
    session = Session()
    messages = [
        User("old"), AI("old answer"),
        User("recent"), AI("recent answer"),
        User("latest"),
    ]
    if with_system:
        messages.insert(0, System("rules"))
    session.add_many(messages)

    session.prune_active(limit)

    assert [message.message for message in session.active] == expected


def test_keep_system_false_does_not_enable_message_dropping() -> None:
    session = Session()
    session.add_many([System("rules"), User("task"), AI("working")])

    session.prune_active(1, keep_system=False)

    assert [message.message for message in session.active] == ["rules", "task", "working"]


@pytest.mark.parametrize("limit", [None, 0, -1])
def test_nonpositive_or_missing_limit_disables_active_pruning(limit: int | None) -> None:
    session = Session()
    session.add_many([System("rules"), User("old"), AI("answer")])

    session.prune_active(limit)

    assert [message.message for message in session.active] == ["rules", "old", "answer"]


@pytest.mark.parametrize("keep_system", [True, False])
def test_transcript_without_user_boundary_is_preserved(keep_system: bool) -> None:
    session = Session()
    session.add_many([
        System("rules"),
        AI("calling", tool_calls=[ToolCall("one", "work", {})]),
        tool_result("one", "result"),
    ])

    session.prune_active(1, keep_system=keep_system)

    assert [message.message for message in session.active] == ["rules", "calling", "result"]
    validate_tool_pairs(session.active)


def test_interrupted_tool_results_remain_paired_when_current_turn_overflows() -> None:
    session = Session()
    session.add_many([
        User("task"),
        AI("calling", tool_calls=[
            ToolCall("completed", "work", {}),
            ToolCall("cancelled", "work", {}),
        ]),
        tool_result("completed", "finished"),
        tool_result("cancelled", "[ToolCallInterrupted] work: run cancelled"),
    ])

    session.prune_active(1)

    assert [message.message for message in session.active] == [
        "task", "calling", "finished", "[ToolCallInterrupted] work: run cancelled",
    ]
    assert [
        message.tool_call_id for message in session.active if message.role == "tool"
    ] == ["completed", "cancelled"]
    validate_tool_pairs(session.active)


def test_next_request_preserves_older_turns_despite_legacy_count_limit() -> None:
    session = Session()
    session.add_many([System("rules"), User("old"), AI("old answer")])
    model = ScriptedModel(AI("done"))
    agent = Agent(
        model,
        session=session,
        options=AgentOptions(active_message_limit=2, context_window=32000),
    )

    assert agent.run("new") == "done"

    assert [message.message for message in model.requests[0]] == ["rules", "old", "old answer", "new"]
    assert [message.message for message in session.history] == [
        "rules", "old", "old answer", "new", "done",
    ]
