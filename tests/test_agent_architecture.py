import asyncio
import ast
import inspect
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.agent import Agent, AgentTimeoutError
from core.agent_runtime import (
    AgentReactLoop,
    AgentResponseParser,
    AgentRunState,
    AgentRuntime,
    AgentToolExecutor,
    parse_invoke_response,
)
from core.llm import LLMError
from models import AI, Chunk, Message, ToolCall, User
from tools.permissions import (
    ToolPermission,
    ToolPermissionPolicy,
    parse_permission_rules,
)
from tools.registry import ToolRegistry
from tools.runtime import ToolRuntime


class FakeLLM:
    model = "fake"
    context_window = 131072
    base_url = "http://fake.local"

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.responses = list(responses or [])
        self.invoke_count = 0
        self.ainvoke_count = 0
        self.stream_calls: list[list[Chunk]] = []
        self.astream_calls: list[list[Chunk]] = []

    def invoke(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.invoke_count += 1
        return self.responses.pop(0)

    async def ainvoke(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.ainvoke_count += 1
        return self.responses.pop(0)

    def stream(self, *args: Any, **kwargs: Any):
        chunks = self.stream_calls.pop(0)
        yield from chunks

    async def astream(self, *args: Any, **kwargs: Any):
        chunks = self.astream_calls.pop(0)
        for chunk in chunks:
            yield chunk


def _response(
    content: str = "",
    *,
    reasoning: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"content": content}
    if reasoning:
        message["reasoning_content"] = reasoning
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


def _tool_call_dict(
    name: str,
    arguments: str = "{}",
    *,
    id: str = "call-1",
) -> dict[str, Any]:
    return {
        "id": id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }


def test_agent_initializes_runtime_components() -> None:
    agent = Agent(FakeLLM([_response("ok")]))  # type: ignore[arg-type]

    assert isinstance(agent.run_state, AgentRunState)
    assert isinstance(agent.response_parser, AgentResponseParser)
    assert isinstance(agent.tool_executor, AgentToolExecutor)
    assert isinstance(agent.runtime, AgentRuntime)
    assert isinstance(agent.react_loop, AgentReactLoop)
    assert agent.runtime.session is agent.session
    assert not hasattr(agent.runtime, "agent")
    assert agent.react_loop.runtime is agent.runtime


def test_agent_facade_delegates_run_methods_to_react_loop() -> None:
    assert "react_loop.run(message)" in inspect.getsource(Agent.run)
    assert "react_loop.run_stream(message)" in inspect.getsource(Agent.run_stream)
    assert "react_loop.arun(message)" in inspect.getsource(Agent.arun)
    assert "react_loop.arun_stream(message)" in inspect.getsource(Agent.arun_stream)


def test_response_parser_parses_content_reasoning_and_tool_calls() -> None:
    ai, tool_calls = parse_invoke_response(_response(
        "hello",
        reasoning="because",
        tool_calls=[_tool_call_dict("demo", '{"x": 1}')],
    ))

    assert ai.message == "hello"
    assert ai.reasoning == "because"
    assert ai.tool_calls == tool_calls
    assert tool_calls[0].id == "call-1"
    assert tool_calls[0].name == "demo"
    assert tool_calls[0].arguments == '{"x": 1}'


def test_response_parser_preserves_missing_choices_error() -> None:
    try:
        parse_invoke_response({})
    except LLMError as error:
        assert "choices" in str(error)
    else:
        raise AssertionError("expected LLMError")


def test_tool_executor_success_none_and_error_shapes() -> None:
    registry = ToolRegistry()

    def echo(value: str) -> str:
        return value

    def returns_none() -> None:
        return None

    registry.register(echo)
    registry.register(returns_none)
    executor = AgentToolExecutor(registry)
    results = executor.execute([
        ToolCall("ok", "echo", {"value": "hello"}),
        ToolCall("none", "returns_none", {}),
        ToolCall("bad", "echo", {}),
    ])

    assert [result.role for result in results] == ["tool", "tool", "tool"]
    assert isinstance(executor.runtime, ToolRuntime)
    assert [result.message for result in results[:2]] == ["hello", "(No return value)"]
    assert results[2].message.startswith("[ToolCallError] echo: Invalid arguments: ")
    assert "missing 1 required positional argument" in results[2].message
    assert getattr(results[0], "tool_call_id") == "ok"
    records = executor.runtime.audit_log.records()
    assert [record.tool for record in records] == ["echo", "returns_none", "echo"]
    assert [record.error for record in records] == [False, False, True]
    assert records[0].result == "hello"


def test_tool_runtime_unknown_tool_and_bad_json_shapes() -> None:
    runtime = ToolRuntime(ToolRegistry())

    results = runtime.execute([
        ToolCall("unknown", "missing_tool", {}),
        ToolCall("bad-json", "missing_tool", "{"),
    ])

    assert results[0].message == "[ToolCallError] missing_tool: Tool does not exist: missing_tool"
    assert results[1].message.startswith(
        "[ToolCallError] missing_tool: Arguments are not valid JSON: "
    )
    assert getattr(results[1], "tool_call_id") == "bad-json"
    records = runtime.audit_log.records()
    assert [record.tool for record in records] == ["missing_tool", "missing_tool"]
    assert [record.error for record in records] == [True, True]
    assert records[0].side_effect == "unknown"


def test_tool_runtime_permission_dry_run_records_decision_without_blocking() -> None:
    registry = ToolRegistry()

    def mutate() -> str:
        return "mutated"

    registry.register(mutate, permission="workspace_mutating")
    runtime = ToolRuntime(
        registry,
        permission_policy=ToolPermissionPolicy.from_config(
            dry_run=True,
            rules="workspace_mutating=deny",
        ),
        caller="A",
    )

    result = runtime.execute([ToolCall("call-1", "mutate", {})])[0]
    record = runtime.audit_log.records()[0]

    assert result.message == "mutated"
    assert record.result == "mutated"
    assert record.error is False
    assert record.caller == "A"
    assert record.decision.mode == "deny"
    assert record.decision.allowed is True


def test_tool_runtime_permission_enforce_blocks_denied_tools() -> None:
    registry = ToolRegistry()
    calls: list[str] = []

    def mutate() -> str:
        calls.append("mutated")
        return "mutated"

    registry.register(mutate, permission="workspace_mutating")
    runtime = ToolRuntime(
        registry,
        permission_policy=ToolPermissionPolicy.from_config(
            enforce=True,
            rules="workspace_mutating=deny",
        ),
        caller="A",
    )

    result = runtime.execute([ToolCall("call-1", "mutate", {})])[0]
    record = runtime.audit_log.records()[0]

    assert calls == []
    assert result.message.startswith("[ToolCallError] mutate: Permission denied: ")
    assert "enforced deny" in result.message
    assert record.error is True
    assert record.decision.mode == "deny"
    assert record.decision.allowed is False
    assert record.result.startswith("[ToolCallError] mutate: Permission denied: ")


def test_tool_registry_normalizes_permission_metadata() -> None:
    registry = ToolRegistry()

    def demo() -> str:
        return "ok"

    registry.register(demo, permission="workspace_mutating")

    permission = registry.tool_metadata("demo")
    assert isinstance(permission, ToolPermission)
    assert permission.side_effect == "workspace_mutating"
    assert registry.tool_metadata_map()["demo"] == "workspace_mutating"

    registry.set_permission("demo", {"side_effect": "read_only", "scope": "member"})

    updated = registry.get_permission("demo")
    assert isinstance(updated, ToolPermission)
    assert updated.side_effect == "read_only"
    assert updated.scope == "member"

    assert parse_permission_rules(
        "external_effect=approval,workspace_mutating=deny,read_only=allow"
    ) == {
        "external_effect": "approval",
        "workspace_mutating": "deny",
        "read_only": "allow",
    }


def test_tool_runtime_does_not_import_agent_group_or_interfaces() -> None:
    forbidden = {
        "application",
        "observability",
        "main",
        "web_server",
        "core.agent",
        "core.group",
        "core.group_runtime",
    }
    path = Path("tools/runtime.py")
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module not in forbidden, path
            assert not module.startswith("core.group_runtime"), path
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden, path
                assert not alias.name.startswith("core.group_runtime"), path


def test_run_executes_multiturn_tool_flow_and_records_session() -> None:
    registry = ToolRegistry()

    def echo(value: str) -> str:
        return f"tool:{value}"

    registry.register(echo)
    llm = FakeLLM([
        _response(tool_calls=[_tool_call_dict("echo", '{"value":"x"}')]),
        _response("done"),
    ])
    agent = Agent(llm, registry=registry, max_turns=3)  # type: ignore[arg-type]

    assert agent.run("go") == "done"
    assert llm.invoke_count == 2
    assert [type(message) for message in agent.session.history] == [
        User,
        AI,
        Message,
        AI,
    ]
    assert agent.session.history[2].message == "tool:x"


async def test_arun_executes_multiturn_tool_flow() -> None:
    registry = ToolRegistry()

    def echo(value: str) -> str:
        return f"tool:{value}"

    registry.register(echo)
    llm = FakeLLM([
        _response(tool_calls=[_tool_call_dict("echo", '{"value":"x"}')]),
        _response("done"),
    ])
    agent = Agent(llm, registry=registry, max_turns=3)  # type: ignore[arg-type]

    assert await agent.arun("go") == "done"
    assert llm.ainvoke_count == 2
    assert agent.session.history[-1].message == "done"


def test_finish_task_tool_call_stops_run_after_recording_tool_result() -> None:
    llm = FakeLLM([
        _response(tool_calls=[_tool_call_dict("finish_task")]),
        _response("should not be called"),
    ])
    registry = ToolRegistry()
    registry.register(lambda: "PASS", name="finish_task", ends_run=True)
    agent = Agent(llm, max_turns=3, registry=registry)  # type: ignore[arg-type]

    assert agent.run("go") == ""
    assert llm.invoke_count == 1
    assert agent.session.history[-2].tool_calls[0].name == "finish_task"
    assert agent.session.history[-1].role == "tool"


def test_max_turn_fallback_is_still_recorded_for_non_stream_run() -> None:
    llm = FakeLLM([
        _response(tool_calls=[_tool_call_dict("missing")]),
    ])
    agent = Agent(llm, max_turns=1)  # type: ignore[arg-type]

    assert agent.run("go") == "(Maximum turns reached without a final response)"
    assert agent.session.history[-1].message == "(Maximum turns reached without a final response)"


def test_sync_stream_yields_chunks_and_continues_after_tool_call() -> None:
    registry = ToolRegistry()

    def echo(value: str) -> str:
        return f"tool:{value}"

    registry.register(echo)
    llm = FakeLLM()
    llm.stream_calls = [
        [
            Chunk(message="pre"),
            Chunk(tool_calls=[ToolCall("call-1", "echo", {"value": "x"})]),
        ],
        [
            Chunk(reasoning="why"),
            Chunk(message="done"),
            Chunk(finish_reason="stop"),
        ],
    ]
    agent = Agent(llm, registry=registry, max_turns=3)  # type: ignore[arg-type]

    chunks = list(agent.run_stream("go"))

    assert [chunk.message for chunk in chunks if chunk.message] == ["pre", "done"]
    assert [chunk.reasoning for chunk in chunks if chunk.reasoning] == ["why"]
    assert chunks[1].tool_calls[0].name == "echo"
    assert agent.session.history[-1].message == "done"
    assert agent.session.history[-1].reasoning == "why"


async def test_async_stream_yields_chunks_and_continues_after_tool_call() -> None:
    registry = ToolRegistry()

    def echo(value: str) -> str:
        return f"tool:{value}"

    registry.register(echo)
    llm = FakeLLM()
    llm.astream_calls = [
        [
            Chunk(message="pre"),
            Chunk(tool_calls=[ToolCall("call-1", "echo", {"value": "x"})]),
        ],
        [
            Chunk(message="done"),
            Chunk(finish_reason="stop"),
        ],
    ]
    agent = Agent(llm, registry=registry, max_turns=3)  # type: ignore[arg-type]

    chunks = []
    async for chunk in agent.arun_stream("go"):
        chunks.append(chunk)

    assert [chunk.message for chunk in chunks if chunk.message] == ["pre", "done"]
    assert chunks[1].tool_calls[0].name == "echo"
    assert agent.session.history[-1].message == "done"


def test_run_state_timeout_error_message_is_unchanged() -> None:
    run_state = AgentRunState(run_timeout=0.001)
    try:
        run_state.check_deadline(0)
    except AgentTimeoutError as error:
        assert str(error) == "Agent run exceeded timeout 0.001s"
    else:
        raise AssertionError("expected AgentTimeoutError")


def test_agent_runtime_does_not_import_group_or_interfaces() -> None:
    forbidden = {
        "application",
        "observability",
        "main",
        "web_server",
        "core.group",
        "core.group_runtime",
    }
    for path in Path("core/agent_runtime").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module not in forbidden, path
                assert not module.startswith("core.group_runtime"), path
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in forbidden, path
                    assert not alias.name.startswith("core.group_runtime"), path


if __name__ == "__main__":
    test_agent_initializes_runtime_components()
    test_agent_facade_delegates_run_methods_to_react_loop()
    test_response_parser_parses_content_reasoning_and_tool_calls()
    test_response_parser_preserves_missing_choices_error()
    test_tool_executor_success_none_and_error_shapes()
    test_tool_runtime_unknown_tool_and_bad_json_shapes()
    test_tool_runtime_permission_dry_run_records_decision_without_blocking()
    test_tool_runtime_permission_enforce_blocks_denied_tools()
    test_tool_registry_normalizes_permission_metadata()
    test_tool_runtime_does_not_import_agent_group_or_interfaces()
    test_run_executes_multiturn_tool_flow_and_records_session()
    asyncio.run(test_arun_executes_multiturn_tool_flow())
    test_finish_task_tool_call_stops_run_after_recording_tool_result()
    test_max_turn_fallback_is_still_recorded_for_non_stream_run()
    test_sync_stream_yields_chunks_and_continues_after_tool_call()
    asyncio.run(test_async_stream_yields_chunks_and_continues_after_tool_call())
    test_run_state_timeout_error_message_is_unchanged()
    test_agent_runtime_does_not_import_group_or_interfaces()
