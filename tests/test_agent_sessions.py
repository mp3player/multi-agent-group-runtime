"""Idle snapshots preserve local types and reject unsafe provider contexts."""
import json
import os
import pytest
from core.session import Session
from models import AI, Message, Reasoning, System, ToolCall, User
from application.agent_config import AgentAppConfig
from application.agent_service import AgentAppService
from core.agent_runtime.run_state import AgentBusyError


def store():
    from core.session_store import JsonSessionStore
    return JsonSessionStore()


def sample():
    session = Session(history_limit=37)
    session.add(System('system'))
    session.add(User('audit only'), to_active=False)
    session.add(Reasoning('local reasoning'))
    session.add(AI('', reasoning='think', tool_calls=[ToolCall('read-1', 'read', {'path': 'a'}), ToolCall('write-1', 'write', '{"path":"b"}')]))
    for call_id in ('write-1', 'read-1'):
        result = Message('tool', 'done')
        result.tool_call_id = call_id
        session.add(result)
    return session


def test_snapshot_round_trip(tmp_path):
    path = tmp_path / 'session.json'
    store().save(sample(), path)
    restored = store().load(path)
    assert restored.history_limit == 37
    assert len(restored.history) == len(restored.active) + 1
    assert restored.active[-1].tool_call_id == 'read-1'
    assert isinstance(restored.active[1], Reasoning)
    assert restored.active[2].reasoning == 'think'
    assert restored.active[2].tool_calls[0].arguments == {'path': 'a'}
    assert restored.active[2].tool_calls[1].arguments == '{"path":"b"}'
    assert path.stat().st_mode & 0o777 == 0o600


def active_record(data, index):
    entry_id = data['active_ids'][index]
    return next(entry['message'] for entry in data['entries'] if entry['entry_id'] == entry_id)


@pytest.mark.parametrize('change', [
    lambda d: d.update(version=3),
    lambda d: d.update(extra=True),
    lambda d: d.update(history_limit=True),
    lambda d: active_record(d, 0).update(role='alien'),
    lambda d: active_record(d, -1).update(tool_call_id='unknown'),
    lambda d: d['active_ids'].pop(),
    lambda d: active_record(d, 2)['tool_calls'][0].update(id=''),
    lambda d: active_record(d, 2)['tool_calls'][0].update(arguments=1),
    lambda d: active_record(d, 0).update(content=42),
])
def test_reject_invalid_snapshot(tmp_path, change):
    path = tmp_path / 'session.json'
    store().save(sample(), path)
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        store().load(path)


def test_atomic_failed_save_keeps_original_and_removes_temp(tmp_path, monkeypatch):
    path = tmp_path / 'session.json'
    store().save(sample(), path)
    original = path.read_bytes()
    def fail(*args):
        raise OSError('replace failed')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError, match='replace failed'):
        store().save(Session(), path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_service_snapshot_gates_and_failed_load_preserves_session(tmp_path):
    service = AgentAppService(AgentAppConfig.from_mapping({'MAS_ENABLE_TOOLS': 'false'}), model=object())
    old = service.agent.session
    path = tmp_path / 'session.json'
    path.write_text('{"version":99}')
    with pytest.raises(ValueError):
        service.load_session(path)
    assert service.agent.session is old
    with service._run():
        with pytest.raises(AgentBusyError):
            service.save_session(path)
    service.agent.run_state.enter_run()
    try:
        with pytest.raises(AgentBusyError):
            service.load_session(path)
    finally:
        service.agent.run_state.exit_run()
    store().save(sample(), path)
    service.load_session(path)
    assert service.agent.session.active[-1].tool_call_id == 'read-1'


def test_new_after_load_restores_configured_limits_and_current_prompt(tmp_path):
    app = AgentAppService(AgentAppConfig.from_mapping({'MAS_HISTORY_MESSAGE_LIMIT': '19', 'MAS_ENABLE_TOOLS': 'false'}), model=object())
    app.agent.system_prompt = 'current application instructions'
    path = tmp_path / 'other.json'
    store().save(sample(), path)
    app.load_session(path)
    assert app.agent.session.history_limit == 37
    assert app.agent.session.active[0].message == 'current application instructions'
    assert any(isinstance(m, System) and m.message == 'system' for m in app.agent.session.history)
    app.new_session()
    assert app.agent.session.history_limit == 19
    assert [m.message for m in app.agent.session.active] == ['current application instructions']


@pytest.mark.parametrize('contents', ['{', '{"version":1,"version":1}', 'NaN', '[]'])
def test_corrupt_json_rejected(tmp_path, contents):
    path = tmp_path / 'corrupt.json'
    path.write_text(contents)
    with pytest.raises(ValueError):
        store().load(path)


def test_standalone_tool_calls_and_reused_completed_ids(tmp_path):
    session = Session(history_limit=None)
    for _ in range(2):
        session.add(ToolCall('same', 'read', '{}'))
        result = Message('tool', 'ok')
        result.tool_call_id = 'same'
        session.add(result)
    path = tmp_path / 'standalone.json'
    store().save(session, path)
    loaded = store().load(path)
    assert isinstance(loaded.active[0], ToolCall)
    assert loaded.active[0].arguments == '{}'
    assert loaded.active[-1].tool_call_id == 'same'


@pytest.mark.parametrize('phase', ['write', 'fsync'])
def test_failed_write_or_flush_preserves_existing_file(tmp_path, monkeypatch, phase):
    path = tmp_path / 'existing.json'
    store().save(sample(), path)
    original = path.read_bytes()
    if phase == 'fsync':
        def fail(*args):
            raise OSError('disk error')
        monkeypatch.setattr(os, 'fsync', fail)
    else:
        import core.session_store as module
        real = module.tempfile.NamedTemporaryFile
        class BrokenWriter:
            def __init__(self, wrapped):
                self.wrapped = wrapped
                self.name = wrapped.name
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.wrapped.close()
            def write(self, payload):
                self.wrapped.write(payload[:20])
                raise OSError('disk error')
        monkeypatch.setattr(module.tempfile, 'NamedTemporaryFile', lambda **kwargs: BrokenWriter(real(**kwargs)))
    with pytest.raises(OSError, match='disk error'):
        store().save(Session(), path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('arguments', [{1: 'non-string key'}, {'nested': (1, 2)}])
def test_save_rejects_non_json_argument_types_without_loss(tmp_path, arguments):
    session = sample()
    session.active[2].tool_calls[0].arguments = arguments
    with pytest.raises(ValueError):
        store().save(session, tmp_path / 'bad.json')
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['run', 'arun', 'run_stream', 'arun_stream'])
@pytest.mark.parametrize('ids', [('duplicate', 'duplicate'), ('', 'valid')])
async def test_malformed_model_batch_is_rejected_before_effects_and_remains_saveable(tmp_path, mode, ids):
    from core.agent import Agent
    from tests.runtime_fakes import ScriptedModel, execute
    from tools.registry import ToolRegistry
    from tools.decorator import tool
    effects = []
    @tool
    def effect() -> str:
        """Record a test effect."""
        effects.append('effect')
        return 'ok'
    registry = ToolRegistry()
    registry.register(effect)
    agent = Agent(ScriptedModel(AI(tool_calls=[ToolCall(i, 'effect', {}) for i in ids]), AI('recovered')), registry=registry)
    with pytest.raises(ValueError, match='ids'):
        await execute(agent, mode, 'go')
    assert effects == []
    assert [m.role for m in agent.session.active] == ['user']
    assert not agent.run_state.active
    store().save(agent.session, tmp_path / 'after-error.json')
    assert await execute(agent, mode, 'retry') == 'recovered'
    store().save(agent.session, tmp_path / 'after-retry.json')
