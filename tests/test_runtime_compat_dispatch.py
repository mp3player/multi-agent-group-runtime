"""Compatibility hooks must control real execution and share runtime state."""

import pytest

from core.agent import Agent
from core.agent_runtime.run_state import AgentBusyError
from models import AI, ToolCall
from tests.runtime_fakes import ScriptedModel, execute
from tools.permissions import ToolPermissionPolicy
from tools.registry import ToolRegistry
from tools.runtime import ToolRuntime


@pytest.mark.parametrize('mode', ['run', 'arun', 'run_stream', 'arun_stream'])
@pytest.mark.parametrize('replacement_path', ['legacy', 'runtime'])
async def test_replacement_runtime_enforces_policy_through_both_views(mode, replacement_path):
    effects = []
    registry = ToolRegistry()

    def write_marker() -> str:
        """Record an in-memory effect."""
        effects.append('executed')
        return 'ok'

    registry.register(write_marker, permission='workspace_mutating')
    original = ToolRuntime(registry)
    agent = Agent(ScriptedModel(
        AI(tool_calls=[ToolCall('call-1', 'write_marker', {})]), AI('done'),
    ), tool_runtime=original)
    adapter = agent.tool_executor
    replacement = ToolRuntime(registry, caller='replacement')
    if replacement_path == 'legacy':
        adapter.runtime = replacement
    else:
        agent.runtime.tool_runtime = replacement
    adapter.runtime.permission_policy = ToolPermissionPolicy.from_config(
        enforce=True, rules='workspace_mutating=deny',
    )

    assert await execute(agent, mode) == 'done'
    assert effects == []
    tool_result = next(message for message in agent.session.history if message.role == 'tool')
    assert 'Permission denied' in tool_result.message
    assert not tool_result.tool_success
    audit = replacement.audit_log.records()
    assert len(audit) == 1 and audit[0].caller == 'replacement'
    assert audit[0].decision.mode == 'deny' and audit[0].error


@pytest.mark.parametrize('mode', ['run', 'arun'])
async def test_custom_response_parser_controls_committed_response(mode):
    agent = Agent(ScriptedModel(AI('provider response')))

    def normalize(data):
        content = data['choices'][0]['message']['content']
        return AI('normalized: ' + content), []

    agent.response_parser.parse = normalize

    assert await execute(agent, mode) == 'normalized: provider response'
    assert agent.session.history[-1].message == 'normalized: provider response'


@pytest.mark.parametrize('mode', ['run', 'arun'])
async def test_custom_response_parser_validation_is_not_bypassed(mode):
    agent = Agent(ScriptedModel(AI('rejected response')))

    def validate(data):
        raise ValueError('custom response validation failed')

    agent.response_parser.parse = validate

    with pytest.raises(ValueError, match='custom response validation failed'):
        await execute(agent, mode)
    assert not agent.run_state.active
    assert all(message.role != 'assistant' for message in agent.session.history)


@pytest.mark.parametrize('replacement_path', ['legacy', 'runtime'])
def test_runtime_replacement_is_rejected_during_a_run(replacement_path):
    agent = Agent(ScriptedModel(AI('done')))
    original = agent.tool_executor.runtime
    replacement = ToolRuntime(agent.registry)
    agent.run_state.enter_run()
    try:
        with pytest.raises(AgentBusyError):
            if replacement_path == 'legacy':
                agent.tool_executor.runtime = replacement
            else:
                agent.runtime.tool_runtime = replacement
    finally:
        agent.run_state.exit_run()
    assert agent.tool_executor.runtime is agent.runtime.tool_runtime is original


@pytest.mark.parametrize('mode', ['run', 'arun', 'run_stream', 'arun_stream'])
async def test_custom_tool_executor_validation_prevents_effects(mode):
    effects = []
    registry = ToolRegistry()

    def write_marker() -> str:
        effects.append('executed')
        return 'ok'

    registry.register(write_marker)
    agent = Agent(ScriptedModel(
        AI(tool_calls=[ToolCall('call-1', 'write_marker', {})]), AI('done'),
    ), registry=registry)

    def validate(calls):
        raise ValueError('custom tool validation failed')

    agent.tool_executor.execute = validate

    with pytest.raises(ValueError, match='custom tool validation failed'):
        await execute(agent, mode)
    assert effects == []
    assert not agent.run_state.active
    assert agent.session.history[-1].tool_call_id == 'call-1'
