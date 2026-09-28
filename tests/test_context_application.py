"""Application-owned context settings, archive access and idle lifecycle."""
import asyncio
from dataclasses import replace
from pathlib import Path
import stat

import pytest

from application.agent_builder import build_agent
from application.agent_config import AgentAppConfig
from application.agent_service import AgentAppService
from core.agent_runtime.run_state import AgentBusyError
from core.llm import LLMClient
from models import AI, Chunk, User
from tools.registry import ToolCallError, ToolRegistry, ToolRegistryError


class ContextAppModel:
    context_window = 100000

    def __init__(self):
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append(messages)
        summary = bool(messages and messages[0].message.startswith('CONTEXT_COMPACTION'))
        response = 'Private historical summary.' if summary else 'Final answer.'
        return {'choices': [{'message': AI(response).to_dict(),
                             'finish_reason': 'stop'}]}

    async def ainvoke(self, messages, **kwargs):
        return self.invoke(messages, **kwargs)

    async def astream(self, messages, **kwargs):
        response = self.invoke(messages, **kwargs)
        yield Chunk(message=response['choices'][0]['message']['content'])
        yield Chunk(finish_reason='stop')


def app_config(tmp_path, **extra):
    prompts = tmp_path / 'prompts'
    prompts.mkdir(exist_ok=True)
    (prompts / 'order.txt').write_text('', encoding='utf-8')
    return AgentAppConfig.from_mapping({
        'MAS_ENABLE_TOOLS': 'false',
        'MAS_PROMPTS_DIR': str(prompts),
        'MAS_SKILLS_DIR': str(tmp_path / 'skills'),
        'MAS_CONTEXT_ARCHIVE_DIR': str(tmp_path / 'archives'),
        'MAS_CONTEXT_WINDOW': '100000',
        **extra,
    })


def add_old_context(app):
    app.agent.session.add(User('old goal ' + 'details ' * 400))
    app.agent.session.add(AI('old evidence ' + 'observed ' * 400))
    app.agent.session.add(User('current protected goal'))


def test_context_config_is_explicit_and_composed(tmp_path, monkeypatch):
    monkeypatch.setenv('MAS_CONTEXT_WINDOW', '7')
    config = app_config(tmp_path, MAS_CONTEXT_WINDOW='64000',
                        MAS_CONTEXT_INPUT_LIMIT='32000',
                        MAS_CONTEXT_ARCHIVE_QUOTA_BYTES='1048576')
    agent = build_agent(config, model=ContextAppModel())
    assert agent.options.context_window == 64000
    assert agent.options.context_input_limit == 32000
    assert agent.session.archive_store.root == tmp_path / 'archives'
    assert agent.session.archive_store.quota_bytes == 1048576
    assert stat.S_IMODE(agent.session.archive_store.root.stat().st_mode) == 0o700


@pytest.mark.parametrize('name', ['MAS_CONTEXT_WINDOW', 'MAS_CONTEXT_INPUT_LIMIT',
                                 'MAS_CONTEXT_ARCHIVE_QUOTA_BYTES'])
@pytest.mark.parametrize('value', ['0', '-1', '1.5', 'true'])
def test_invalid_context_numbers_fail_at_configuration_boundary(name, value):
    with pytest.raises(ValueError, match=name):
        AgentAppConfig.from_mapping({name: value})


def test_default_capacity_stays_unknown_and_archive_root_is_private_state(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    config = AgentAppConfig.from_mapping({})
    assert config.agent.context_window is None
    assert config.agent.context_input_limit is None
    assert Path(config.agent.context_archive_dir) == tmp_path / '.local/state/mas/context'
    assert config.agent.context_archive_quota_bytes == 268435456


@pytest.mark.parametrize('enabled', [False, True])
def test_stream_usage_opt_in_reaches_both_model_factories(tmp_path, monkeypatch, enabled):
    config = app_config(tmp_path, BaseURL='http://127.0.0.1:1/v1', BaseKey='test-only',
                        MAS_LLM_STREAM_USAGE=str(enabled))
    agent = build_agent(config)
    monkeypatch.setattr(AgentAppConfig, 'from_env', classmethod(lambda cls, env_path=None: config))
    client = LLMClient.from_env()
    assert agent.llm.stream_usage is enabled
    assert client.stream_usage is enabled


def test_archive_tool_is_read_only_bounded_and_follows_new_and_loaded_session(tmp_path):
    app = AgentAppService(app_config(tmp_path), model=ContextAppModel())
    assert app.tool_names() == ['read_context_archive']
    assert app.agent.registry.get_permission('read_context_archive').side_effect == 'read_only'
    app.agent.session.add(User('retained archive original ' + 'x' * 13000))
    archive_id = app.agent.session.archive_entries(app.agent.session.context_entries(), reason='test')
    original_session_id = app.agent.session.session_id
    page = app.agent.registry.call('read_context_archive', archive_id=archive_id, limit=80)
    assert page['session_id'] == original_session_id
    assert len(page['content']) == 80 and page['next_offset'] == 80
    second = app.agent.registry.call('read_context_archive', archive_id=archive_id, offset=80, limit=80)
    assert second['offset'] == 80 and second['next_offset'] == 160
    assert isinstance(app.agent.registry.call('read_context_archive', archive_id=archive_id, limit=12001), ToolCallError)
    assert isinstance(app.agent.registry.call('read_context_archive', archive_id='../elsewhere'), ToolCallError)
    with pytest.raises(ToolRegistryError):
        app.agent.registry.register(lambda: 'replaced', name='read_context_archive')
    store = app.agent.session.archive_store
    path = tmp_path / 'snapshot.json'
    app.save_session(path)
    app.new_session()
    assert app.agent.session.archive_store is store
    assert isinstance(app.agent.registry.call('read_context_archive', archive_id=archive_id), ToolCallError)
    app.load_session(path)
    assert app.agent.session.archive_store is store
    assert app.agent.registry.call('read_context_archive', archive_id=archive_id)['session_id'] == original_session_id


@pytest.mark.parametrize('owner', [object(), None])
def test_archive_binding_rejects_name_collision_and_shared_owners(owner):
    from core.agent_runtime.context_management.archive_tool import bind_context_archive_tool
    registry = ToolRegistry()
    registry.register(lambda: 'ordinary', name='read_context_archive')
    with pytest.raises(ToolRegistryError, match='reserved'):
        bind_context_archive_tool(registry, lambda: None, owner=owner)
    registry = ToolRegistry()
    owner = object()
    bind_context_archive_tool(registry, lambda: None, owner=owner)
    bind_context_archive_tool(registry, lambda: None, owner=owner)
    with pytest.raises(ToolRegistryError, match='reserved'):
        bind_context_archive_tool(registry, lambda: None, owner=object())


def test_context_status_explains_legacy_limit_without_requiring_capacity(tmp_path):
    model = ContextAppModel()
    model.context_window = None
    config = app_config(tmp_path)
    config = replace(config, agent=replace(config.agent, context_window=None))
    app = AgentAppService(config, model=model)
    status = app.context_status()
    assert status['capacity'] is None
    assert 'MAS_CONTEXT_WINDOW' in status['reason']
    assert 'MAS_ACTIVE_MESSAGE_LIMIT' in status['migration_notice']
    assert 'no longer' in status['migration_notice']
    assert model.calls == []


@pytest.mark.asyncio
async def test_compacted_snapshot_reconnects_archives_and_missing_files_block_only_inference(tmp_path):
    model = ContextAppModel()
    app = AgentAppService(app_config(tmp_path, MAS_CONTEXT_WINDOW='9000',
                                    MAS_DEFAULT_MAX_TOKENS='256'), model=model)
    add_old_context(app)
    await app.acompact('preserve evidence')
    checkpoint = app.agent.session.checkpoint
    assert checkpoint is not None
    path = tmp_path / 'snapshot.json'
    app.save_session(path)
    store = app.agent.session.archive_store
    attachment = store.root / app.agent.session.session_id / (checkpoint['archive_id'] + '.json')
    attachment.unlink()
    app.load_session(path)
    assert app.agent.session.archive_store is store
    assert app.history_entries()
    assert app.context_status()['capacity'] == 9000
    app.save_session(tmp_path / 'inspectable.json')
    previous_calls = len(model.calls)
    with pytest.raises((ValueError, OSError, RuntimeError), match='archive|attachment|No such file'):
        await app.arun('continue')
    assert len(model.calls) == previous_calls
    await app.aclose()


@pytest.mark.asyncio
async def test_service_compaction_gates_lifecycle_and_closed_state(tmp_path):
    started, finish = asyncio.Event(), asyncio.Event()

    class WaitingModel(ContextAppModel):
        async def ainvoke(self, messages, **kwargs):
            started.set()
            await finish.wait()
            return self.invoke(messages, **kwargs)

    app = AgentAppService(app_config(tmp_path, MAS_CONTEXT_WINDOW='9000',
                                    MAS_DEFAULT_MAX_TOKENS='256'), model=WaitingModel())
    add_old_context(app)
    task = asyncio.create_task(app.acompact())
    await asyncio.wait_for(started.wait(), timeout=1)
    try:
        with pytest.raises(AgentBusyError):
            app.new_session()
        with pytest.raises(AgentBusyError):
            await app.aclose()
    finally:
        finish.set()
        await task
    await app.aclose()
    with pytest.raises(RuntimeError, match='closed'):
        app.context_status()
    with pytest.raises(RuntimeError, match='closed'):
        app.compact()
    with pytest.raises(RuntimeError, match='closed'):
        await app.acompact()
