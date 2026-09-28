"""Owned-runtime contracts, exercised through real execution and session state."""
from dataclasses import FrozenInstanceError
from copy import deepcopy
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.agent import Agent
from core import agent_runtime
from core.session import Session
from models import AI, Message, ToolCall, User
from runtime_fakes import ScriptedModel, execute
from tools.registry import ToolRegistry
from tools.permissions import ToolPermissionPolicy

MODES = ['run', 'arun', 'run_stream', 'arun_stream']


def options(**kwargs):
    assert hasattr(agent_runtime, 'AgentOptions'), 'runtime must expose immutable instance options'
    return agent_runtime.AgentOptions(**kwargs)


def test_runtime_executes_without_a_facade():
    assert hasattr(agent_runtime, 'AgentRuntime'), 'runtime must own its dependencies'
    session = Session()
    runtime = agent_runtime.AgentRuntime(ScriptedModel(AI('done')), session, ToolRegistry(), options())
    assert not hasattr(runtime, 'agent')
    assert agent_runtime.AgentReactLoop(runtime).run('go') == 'done'
    assert [m.message for m in session.history] == ['go', 'done']


@pytest.mark.parametrize('mode', MODES)
async def test_instance_options_control_every_model_transport(mode):
    class RequestModel(ScriptedModel):
        def _next(self, messages, **kwargs):
            self.request_options = kwargs
            return super()._next(messages, **kwargs)
    for tokens, temperature in [(123, 0.2), (456, 1.3)]:
        model = RequestModel(AI('done'))
        agent = Agent(model, options=options(max_tokens=tokens, temperature=temperature))
        assert await execute(agent, mode) == 'done'
        assert model.request_options['max_tokens'] == tokens
        assert model.request_options['temperature'] == temperature


@pytest.mark.parametrize('field,value', [('max_turns', 0), ('max_tokens', -1), ('max_turns', 1.5), ('max_tokens', True), ('temperature', float('nan')), ('temperature', float('inf')), ('run_timeout', float('nan')), ('run_timeout', float('inf')), ('active_message_limit', 1.5)])
def test_options_reject_invalid_values(field, value):
    with pytest.raises(ValueError):
        options(**{field: value})


def test_options_are_frozen_and_nonpositive_limits_disable_trimming():
    config = options(active_message_limit=0, run_timeout=0)
    with pytest.raises(FrozenInstanceError):
        config.max_tokens = 5
    session = Session()
    session.add_many([User('old'), AI('answer')])
    Agent(ScriptedModel(AI('done')), session=session, options=config).run('new')
    assert [m.message for m in session.active] == ['old', 'answer', 'new', 'done']


@pytest.mark.parametrize('mutation', [
    lambda a: setattr(a, 'llm', ScriptedModel()),
    lambda a: setattr(a, 'session', Session()),
    lambda a: a.set_session(Session()),
    lambda a: a.new_session(),
    lambda a: setattr(a, 'registry', ToolRegistry()),
    lambda a: a.set_registry(ToolRegistry()),
    lambda a: setattr(a, 'context_transform', None),
    lambda a: setattr(a, 'options', options()),
    lambda a: setattr(a, 'max_turns', 2),
    lambda a: setattr(a, 'run_timeout', 3),
    lambda a: setattr(a, 'active_message_limit', 3),
    lambda a: setattr(a, 'system_prompt', 'changed'),
    lambda a: setattr(a, 'system_builder', None),
    lambda a: a.rebuild_system_prompt(),
    lambda a: a.reset_active_to_system(),
])
def test_public_mutations_reject_an_active_stream(mutation):
    agent = Agent(ScriptedModel(AI('partial'), AI('next')), system_prompt='original')
    stream = agent.run_stream('go')
    next(stream)
    original = agent.session
    try:
        with pytest.raises(agent_runtime.AgentBusyError):
            mutation(agent)
        assert agent.session is original
        assert agent.system_prompt == 'original'
    finally:
        stream.close()
    assert agent.run('retry') == 'next'


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('registered', [False, True])
async def test_finish_task_name_alone_does_not_stop(mode, registered):
    registry = ToolRegistry()
    if registered:
        registry.register(lambda: 'ok', name='finish_task')
    model = ScriptedModel(AI(tool_calls=[ToolCall('x', 'finish_task', {})]), AI('done'))
    agent = Agent(model, registry=registry)
    assert await execute(agent, mode) == 'done'
    assert len(model.requests) == 2


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('failure', ['exception', 'denied', 'bad_arguments'])
async def test_unsuccessful_terminal_call_does_not_stop(mode, failure):
    registry = ToolRegistry()
    def finish():
        if failure == 'exception':
            raise ValueError('failed')
        return 'done'
    registry.register(finish, ends_run=True, permission='workspace_mutating')
    model = ScriptedModel(AI(tool_calls=[ToolCall('x', 'finish', '{' if failure == 'bad_arguments' else {})]), AI('recovered'))
    agent = Agent(model, registry=registry)
    if failure == 'denied':
        agent.tool_executor.runtime.permission_policy = ToolPermissionPolicy.from_config(enforce=True, rules='workspace_mutating=deny')
    assert await execute(agent, mode) == 'recovered'
    assert registry.tool_audit_log.records()[0].error


def test_terminal_outcome_survives_later_calls_and_registry_changes():
    registry = ToolRegistry()
    def finish():
        registry.unregister('stop')
        return 'finished'
    registry.register(finish, name='stop', ends_run=True)
    registry.register(lambda: 'other', name='other')
    assert [t['function']['name'] for t in registry.to_openai_tools()] == ['stop', 'other']
    model = ScriptedModel(AI(tool_calls=[ToolCall('1', 'stop', {}), ToolCall('2', 'other', {})]))
    agent = Agent(model, registry=registry)
    assert agent.run('go') == ''
    assert [m.message for m in agent.session.history if m.role == 'tool'] == ['finished', 'other']
    assert len(model.requests) == 1


def batch():
    messages = [User('old'), AI(tool_calls=[ToolCall('a', 'one', {}), ToolCall('b', 'two', {})])]
    for id in ['a', 'b']:
        result = Message('tool', id)
        result.tool_call_id = id
        messages.append(result)
    messages.append(User('latest'))
    return messages


@pytest.mark.parametrize(('target', 'limit', 'want'), [
    ('active', 4, ['user']),
    ('active', 3, ['user']),
    ('active', 2, ['user']),
    ('history', 4, ['assistant', 'tool', 'tool', 'user']),
    ('history', 3, ['user']),
    ('history', 2, ['user']),
])
def test_trimming_keeps_or_drops_a_whole_tool_batch(limit, want, target):
    session = Session(history_limit=None)
    session.add_many(batch())
    getattr(session, 'prune_' + target)(limit)
    assert [m.role for m in getattr(session, target)] == (
        ['user', 'assistant', 'tool', 'tool', 'user'] if target == 'active' else want)
    assert [m.role for m in session.active] == ['user', 'assistant', 'tool', 'tool', 'user']


@pytest.mark.parametrize('bad', ['drop_result', 'drop_call', 'duplicate_result', 'wrong_id', 'interleaved', 'duplicate_call'])
def test_invalid_projection_is_rejected_before_model_io_without_rewriting_history(bad):
    session = Session(history_limit=None)
    session.add_many(batch())
    before = deepcopy(session.history)
    def project(messages):
        if bad == 'drop_result': del messages[2]
        if bad == 'drop_call': del messages[1]
        if bad == 'duplicate_result': messages.insert(3, deepcopy(messages[2]))
        if bad == 'wrong_id': messages[2].tool_call_id = 'unknown'
        if bad == 'interleaved': messages.insert(2, User('bad'))
        if bad == 'duplicate_call': messages[1].tool_calls[1].id = 'a'
        return messages
    model = ScriptedModel(AI('unused'))
    agent = Agent(model, session=session, context_transform=project)
    with pytest.raises(ValueError, match='tool'):
        agent.run('go')
    assert model.requests == []
    assert session.history[:-1] == before
    assert session.history[1].tool_calls[1].id == 'b'


def test_raw_tool_call_context_is_validated_as_a_request():
    from core.agent_runtime.context import project_context
    with pytest.raises(ValueError, match='tool'):
        project_context([ToolCall('raw', 'work', {})])
    result = Message('tool', 'ok')
    result.tool_call_id = 'raw'
    projected = project_context([ToolCall('raw', 'work', {}), result])
    assert projected[-1].tool_call_id == 'raw'


def test_local_reasoning_does_not_break_provider_tool_pairing():
    from core.agent_runtime.context import project_context
    from models import Reasoning
    messages = batch()
    messages.insert(2, Reasoning('local notes'))
    assert len(project_context(messages)) == 6


def test_separate_complete_batches_can_reuse_provider_call_ids():
    from core.agent_runtime.context import project_context
    assert len(project_context(batch() + batch())) == 10


def test_new_session_preserves_history_limit_and_current_prompt():
    agent = Agent(ScriptedModel(), session=Session(history_limit=7), system_prompt='base')
    agent.new_session()
    assert agent.session.history_limit == 7
    assert [m.message for m in agent.session.active] == ['base']


@pytest.mark.parametrize('limit', [1, 2, 3])
def test_incremental_history_pruning_never_leaves_half_a_completed_batch(limit):
    from core.agent_runtime.context import validate_tool_pairs
    session = Session(history_limit=limit)
    session.add_many(batch())
    assert len(session.history) == 5  # maintenance waits for a complete safe boundary
    session.maintain_history()
    validate_tool_pairs(session.history)
    assert [m.message for m in session.history] == ['latest']


def test_stop_decision_uses_given_results_after_other_executions():
    from tools.runtime import ToolRuntime, should_stop_after_tool_calls
    registry = ToolRegistry()
    registry.register(lambda: 'stop', name='finish', ends_run=True)
    runtime = ToolRuntime(registry)
    stopped = runtime.execute([ToolCall('a', 'finish', {})])
    ordinary = runtime.execute([ToolCall('b', 'unknown', {})])
    assert should_stop_after_tool_calls(stopped)
    assert not should_stop_after_tool_calls(ordinary)


@pytest.mark.parametrize('target', ['active', 'history'])
def test_trimming_drops_results_when_local_reasoning_splits_a_batch(target):
    from models import Reasoning
    session = Session(history_limit=None)
    messages = batch()
    messages.insert(2, Reasoning('local'))
    session.add_many(messages)
    getattr(session, 'prune_' + target)(4)
    assert [m.message for m in getattr(session, target)] == (
        [m.message for m in messages] if target == 'active' else ['latest'])
    assert session.active == messages


def test_idle_prompt_mutation_is_atomic_with_run_admission():
    from threading import Event, Thread
    building, release, attempting, requested = (Event() for _ in range(4))
    class Model(ScriptedModel):
        def invoke(self, messages, **kwargs):
            requested.set()
            return super().invoke(messages, **kwargs)
    class Builder:
        def attach_tool_registry(self, registry):
            pass
        def build(self):
            building.set()
            assert release.wait(2)
            return 'new prompt'
    model = Model(AI('done'))
    agent = Agent(model, system_prompt='old prompt')
    errors = []
    def change_prompt():
        try:
            agent.system_builder = Builder()
        except BaseException as error:
            errors.append(error)
    def run():
        attempting.set()
        try:
            agent.run('go')
        except BaseException as error:
            errors.append(error)
    mutation = Thread(target=change_prompt)
    runner = Thread(target=run)
    mutation.start()
    try:
        assert building.wait(2)
        runner.start()
        assert attempting.wait(2)
        assert not requested.wait(0.05)
    finally:
        release.set()
        mutation.join(2)
        if runner.ident is not None:
            runner.join(2)
    assert not errors
    assert model.requests[0][0].message == 'new prompt'


@pytest.mark.parametrize('replacement', [None, '', 'new instructions'])
def test_explicit_prompt_change_survives_reset_without_rewriting_audit(replacement):
    from models import System
    model = ScriptedModel(AI('done'))
    agent = Agent(model, system_prompt='old instructions')
    agent.system_prompt = replacement
    agent.session.clear_active()
    historical = list(agent.session.history)
    agent.reset_active_to_system()
    assert agent.run('go') == 'done'
    assert [m.message for m in model.requests[0] if isinstance(m, System)] == ([replacement] if replacement else [])
    assert all(message in agent.session.history for message in historical)


@pytest.mark.parametrize('replacement', [None, 'new instructions'])
def test_explicit_prompt_change_is_authoritative_when_replacing_session(replacement):
    from models import System
    agent = Agent(ScriptedModel(AI('done')), system_prompt='old instructions')
    agent.system_prompt = replacement
    inherited = Session()
    inherited.add(System('session instructions'))
    agent.set_session(inherited)
    agent.reset_active_to_system()
    assert [m.message for m in agent.session.active] == ([replacement] if replacement else [])
    assert any(m.message == 'session instructions' for m in agent.session.history)


def test_unspecified_prompt_can_still_reset_to_an_inherited_session_prompt():
    from models import System
    session = Session()
    session.add(System('inherited'))
    agent = Agent(ScriptedModel(), session=session)
    session.clear_active()
    agent.reset_active_to_system()
    assert [m.message for m in session.active] == ['inherited']


@pytest.mark.parametrize('surface', ['property', 'method', 'runtime'])
def test_registry_replacement_preserves_denials_attribution_and_audit_sink(surface, tmp_path):
    import json
    from tools.audit import audit_sink_from_path
    agent = Agent(ScriptedModel(AI(tool_calls=[ToolCall('a', 'mutate', {})]), AI('done')))
    old_registry = agent.registry
    audit_path = tmp_path / 'audit.jsonl'
    old_registry.tool_audit_log.set_sink(audit_sink_from_path(audit_path))
    agent.tool_executor.runtime.permission_policy = ToolPermissionPolicy.from_config(enforce=True, rules='workspace_mutating=deny')
    agent.tool_executor.runtime.caller = 'configured-caller'
    performed = []
    replacement = ToolRegistry()
    replacement.register(lambda: performed.append('mutated'), name='mutate', permission='workspace_mutating')
    if surface == 'property': agent.registry = replacement
    if surface == 'method': agent.set_registry(replacement)
    if surface == 'runtime': agent.runtime.registry = replacement
    assert agent.run('go') == 'done'
    assert performed == []
    records = replacement.tool_audit_log.records()
    assert len(records) == 1
    assert records[0].caller == 'configured-caller'
    assert records[0].error and not records[0].decision.allowed
    assert old_registry.tool_audit_log.records() == []
    persisted = json.loads(audit_path.read_text())
    assert persisted['caller'] == 'configured-caller'
    assert persisted['error'] is True


def test_registry_replacement_preserves_workspace_bounds(tmp_path, monkeypatch):
    from tools.file_ops import read_file
    allowed = tmp_path / 'allowed'
    allowed.mkdir()
    inside = allowed / 'inside.txt'
    inside.write_text('allowed content')
    outside = tmp_path / 'outside.txt'
    outside.write_text('outside content')
    # If the Agent-scoped roots are lost, fallback environment permits outside.
    monkeypatch.setenv('MAS_WORKSPACE_ROOTS', str(tmp_path))
    model = ScriptedModel(AI(tool_calls=[ToolCall('a', 'read_file', {'path': str(outside)}), ToolCall('b', 'read_file', {'path': str(inside)})]), AI('done'))
    agent = Agent(model)
    agent.tool_executor.runtime.workspace_roots = (str(allowed),)
    replacement = ToolRegistry()
    replacement.register(read_file)
    agent.registry = replacement
    assert agent.run('go') == 'done'
    results = [m.message for m in agent.session.active if m.role == 'tool']
    assert 'Path is outside the allowed workspace' in results[0]
    assert 'outside content' not in results[0]
    assert 'allowed content' in results[1]
