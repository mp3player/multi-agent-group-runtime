"""Application isolation, configuration and model resource ownership."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_neutral_imports_and_execution_with_group_blocked():
    script = '''
import importlib.abc, os, sys
blocked = ('core.group', 'domain.group', 'application.group', 'application.member_config',
           'core.member_config', 'prompting.group', 'application.config', 'application.builders', 'core.config')
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(blocked):
            raise AssertionError('Forbidden import: ' + fullname)
sys.meta_path.insert(0, Guard())
os.environ['MAS_GROUP_RUN_TIMEOUT'] = 'broken'
os.environ['MAS_MAX_DISPATCH_ROUNDS'] = 'broken'
from core.agent import Agent
from application.agent_config import AgentAppConfig
from application.agent_service import AgentAppService
from tests.runtime_fakes import ScriptedModel
from models import AI
cfg = AgentAppConfig.from_mapping({'MAS_GROUP_RUN_TIMEOUT': 'broken'})
assert not hasattr(AgentAppConfig.from_env('/dev/null'), 'group')
service = AgentAppService(cfg, model=ScriptedModel(AI('done')))
assert service.agent.run('hello') == 'done'
assert not hasattr(cfg, 'group')
assert not any(name.startswith(blocked) for name in sys.modules)
assert not hasattr(service.agent.system_builder, 'has_group_chat')
assert not hasattr(service.agent.system_builder, 'group_chat')
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, 'PYTHONPATH': ''}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_factory_uses_options_order_paths_and_injected_model(tmp_path):
    from application.agent_config import AgentAppConfig
    from application.agent_builder import build_agent
    from tests.runtime_fakes import ScriptedModel
    from models import AI
    (tmp_path / 'first.md').write_text('FIRST')
    (tmp_path / 'second.md').write_text('SECOND')
    (tmp_path / 'custom.txt').write_text('second\nfirst\n')
    config = AgentAppConfig.from_mapping({
        'MAS_PROMPTS_DIR': str(tmp_path), 'MAS_PROMPT_ORDER_FILE': 'custom.txt',
        'MAS_DEFAULT_MAX_TOKENS': '123', 'MAS_DEFAULT_TEMPERATURE': '0.2',
        'MAS_AGENT_RUN_TIMEOUT': '4', 'MAS_LLM_TIMEOUT': '7',
        'MAS_ENABLE_TOOLS': 'false', 'MAS_WORKSPACE_ROOTS': str(tmp_path),
        'MAS_TOOL_PERMISSION_ENFORCE': 'true', 'MAS_TOOL_PERMISSION_RULES': 'external_effect=deny',
        'MAS_SKILLS_DIR': str(tmp_path / 'skills'),
    })
    class RequestModel(ScriptedModel):
        def invoke(self, messages, **kwargs):
            self.request_options = kwargs
            return super().invoke(messages, **kwargs)
    model = RequestModel(AI('done'))
    agent = build_agent(config, model=model)
    assert agent.llm is model
    assert agent.run('hello') == 'done'
    assert model.request_options['max_tokens'] == 123
    assert model.request_options['temperature'] == 0.2
    assert agent.options.max_tokens == 123
    assert agent.options.temperature == 0.2
    assert agent.run_timeout == 4
    assert config.llm.timeout == 7
    prompt, tool_descriptions = agent.system_builder.build().split('\n\n## Available Tools\n', 1)
    assert prompt == 'SECOND\n\nFIRST'
    assert 'read_context_archive' in tool_descriptions
    assert agent.registry.names() == ['read_context_archive']
    assert agent.tool_executor.runtime.workspace_roots == (str(tmp_path),)
    assert agent.tool_executor.runtime.permission_policy.enforce
    assert agent.tool_executor.runtime.permission_policy.rules == {'external_effect': 'deny'}
    assert config.with_agent_options(max_turns=2).agent.max_turns == 2
    assert Path(AgentAppConfig().skills.skills_dir) == Path(__file__).resolve().parents[1] / 'skills'


@pytest.mark.asyncio
async def test_service_model_ownership_and_closed_operations():
    from application.agent_config import AgentAppConfig
    from application.agent_service import AgentAppService
    from tests.runtime_fakes import ScriptedModel
    from models import AI
    class Model(ScriptedModel):
        closes = 0
        async def aclose(self):
            self.closes += 1
    borrowed = Model(AI('done'))
    service = AgentAppService(AgentAppConfig(), model=borrowed)
    assert service.run('hello') == 'done'
    await service.aclose()
    await service.aclose()
    assert borrowed.closes == 0
    assert service.closed
    for operation in (lambda: service.run('x'), service.clear_session, service.new_session,
                      service.toggle_stream, service.tool_names, service.history_counts,
                      service.history_entries, service.tool_audit_records, service.usage_records,
                      service.usage_summary, service.clear_usage):
        with pytest.raises(RuntimeError, match='closed'):
            operation()
    with pytest.raises(RuntimeError, match='closed'):
        await service.arun('x')
    with pytest.raises(RuntimeError, match='closed'):
        next(service.run_stream('x'))
    with pytest.raises(RuntimeError, match='closed'):
        await anext(service.arun_stream('x'))
    owned = Model()
    async with AgentAppService(AgentAppConfig(), model=owned, owns_model=True):
        pass
    assert owned.closes == 1


@pytest.mark.asyncio
async def test_active_stream_blocks_close_and_iterator_cleanup():
    from application.agent_config import AgentAppConfig
    from application.agent_service import AgentAppService
    from tests.runtime_fakes import ScriptedModel
    from models import AI
    model = ScriptedModel(AI('one'), AI('two'))
    service = AgentAppService(AgentAppConfig(), model=model)
    stream = service.run_stream('hello')
    next(stream)
    with pytest.raises(RuntimeError, match='running'):
        await service.aclose()
    stream.close()
    assert not service.agent.run_state.active
    stream = service.arun_stream('hello')
    await anext(stream)
    with pytest.raises(RuntimeError, match='running'):
        await service.aclose()
    await stream.aclose()
    assert model.streams_closed == 2
    assert not service.agent.run_state.active
    await service.aclose()


@pytest.mark.asyncio
async def test_closing_state_blocks_runs_while_owned_model_awaits():
    from application.agent_config import AgentAppConfig
    from application.agent_service import AgentAppService
    from tests.runtime_fakes import ScriptedModel
    started, finish = asyncio.Event(), asyncio.Event()
    class Model(ScriptedModel):
        async def aclose(self):
            started.set()
            await finish.wait()
    service = AgentAppService(AgentAppConfig(), model=Model(), owns_model=True)
    late_stream = service.run_stream('late')
    closing = asyncio.create_task(service.aclose())
    await started.wait()
    with pytest.raises(RuntimeError, match='closed|closing'):
        service.run('x')
    with pytest.raises(RuntimeError, match='closed|closing'):
        next(late_stream)
    finish.set()
    await closing
    assert service.closed


@pytest.mark.asyncio
async def test_created_model_usage_and_close(monkeypatch):
    from application.agent_config import AgentAppConfig
    from application.agent_service import AgentAppService
    import application.agent_builder as builder
    from tests.runtime_fakes import ScriptedModel
    from models import AI
    class Model(ScriptedModel):
        closes = 0
        def __init__(self, **kwargs):
            super().__init__(AI('done'))
            self.monitor = kwargs['usage_monitor']
            self.timeout = kwargs['timeout']
        def invoke(self, messages, **kwargs):
            result = super().invoke(messages, **kwargs)
            result['usage'] = {'total_tokens': 3}
            if self.monitor:
                self.monitor.record('test', result)
            return result
        async def aclose(self):
            self.closes += 1
    monkeypatch.setattr(builder, 'LLMClient', Model)
    service = AgentAppService(AgentAppConfig.from_mapping({'MAS_LLM_TIMEOUT': '9'}))
    assert await service.arun('hello') == 'done'
    assert service.agent.llm.timeout == 9
    assert service.usage_records()[0].total_tokens == 3
    assert service.usage_summary()['test'].total_tokens == 3
    service.clear_usage()
    assert service.usage_records() == []
    await service.aclose()
    await service.aclose()
    assert service.agent.llm.closes == 1
    disabled = AgentAppService(AgentAppConfig.from_mapping({'MAS_USAGE_ENABLED': 'false'}))
    assert disabled.agent.llm.monitor is None
    await disabled.aclose()


@pytest.mark.asyncio
async def test_service_closes_owned_original_model_after_raw_agent_replacement():
    from application.agent_config import AgentAppConfig
    from application.agent_service import AgentAppService
    from tests.runtime_fakes import ScriptedModel
    class Model(ScriptedModel):
        closes = 0
        async def aclose(self):
            self.closes += 1
    original, replacement = Model(), Model()
    service = AgentAppService(AgentAppConfig(), model=original, owns_model=True)
    service.agent.llm = replacement
    await service.aclose()
    assert original.closes == 1
    assert replacement.closes == 0


@pytest.mark.asyncio
async def test_raw_agent_active_run_blocks_service_close():
    from application.agent_config import AgentAppConfig
    from application.agent_service import AgentAppService
    from tests.runtime_fakes import ScriptedModel
    from models import AI
    service = AgentAppService(AgentAppConfig(), model=ScriptedModel(AI('one')))
    stream = service.agent.run_stream('hello')
    next(stream)
    with pytest.raises(RuntimeError, match='running'):
        await service.aclose()
    stream.close()
    await service.aclose()


@pytest.mark.parametrize('repository', [Path(__file__).resolve().parents[1], Path('/home/coder/project/mas')])
def test_project_local_skills_defaults_and_explicit_overrides(repository, monkeypatch):
    # Evaluate the same module as located in each checkout without modifying either.
    import types
    import application.agent_config as config_module
    relocated = types.ModuleType('_relocated_agent_config')
    relocated.__file__ = str(repository / 'application' / 'agent_config.py')
    monkeypatch.setitem(sys.modules, relocated.__name__, relocated)
    source = Path(config_module.__file__).read_text(encoding='utf-8')
    exec(compile(source, relocated.__file__, 'exec'), relocated.__dict__)
    expected = repository / 'skills'
    assert Path(relocated.AgentAppConfig().skills.skills_dir) == expected
    assert Path(relocated.AgentAppConfig.from_mapping({}).skills.skills_dir) == expected
    assert relocated.SkillsConfig.from_mapping({'MAS_SKILLS_DIR': '/explicit/skills'}).skills_dir == '/explicit/skills'
    assert relocated.SkillsConfig.from_mapping({'SkillsDir': '/legacy/skills'}).skills_dir == '/legacy/skills'
