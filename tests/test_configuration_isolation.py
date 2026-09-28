"""Configuration belongs to the caller, not the surrounding process."""
from pathlib import Path
import os
import subprocess
import sys

import pytest

from application.agent_builder import build_agent
from application.agent_config import AgentAppConfig
from core.llm import LLMClient
from core.llm_runtime.errors import LLMError
from core.system_builder import SystemBuilder
from models import AI, ToolCall
from tests.runtime_fakes import ScriptedModel


@pytest.mark.parametrize('provided', [{}, {'BaseURL': '', 'BaseKey': 'explicit'},
                                     {'BaseURL': 'http://127.0.0.1:1/v1', 'BaseKey': ''}])
def test_explicit_missing_model_config_does_not_inherit_environment(monkeypatch, provided):
    monkeypatch.setenv('BaseURL', 'http://127.0.0.1:1/v1')
    monkeypatch.setenv('BaseKey', 'ambient-key')
    monkeypatch.setenv('BaseModel', 'ambient-model')
    with pytest.raises(LLMError):
        build_agent(AgentAppConfig.from_mapping(provided))


def test_explicit_model_constructor_never_loads_dotenv():
    script = '''
import dotenv
def forbidden(*args, **kwargs):
    raise AssertionError('explicit constructor loaded dotenv')
dotenv.load_dotenv = forbidden
from core.llm import LLMClient
client = LLMClient(base_url='http://127.0.0.1:1/v1', api_key='explicit-key', model='')
assert client.model == ''
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, 'PYTHONPATH': '', 'BaseModel': 'ambient-model'})
    assert result.returncode == 0, result.stderr


def test_model_environment_factory_is_explicit_and_captures_settings(monkeypatch):
    monkeypatch.setenv('BaseURL', 'http://127.0.0.1:1/v1/')
    monkeypatch.setenv('BaseKey', 'factory-key')
    monkeypatch.setenv('BaseModel', 'factory-model')
    monkeypatch.setenv('MAS_LLM_TIMEOUT', '7')
    client = LLMClient.from_env()
    monkeypatch.setenv('BaseModel', 'later-model')
    assert client.base_url == 'http://127.0.0.1:1/v1'
    assert client.api_key == 'factory-key'
    assert client.model == 'factory-model'
    assert client.timeout == 7


def test_prompt_defaults_do_not_inherit_ambient_skill_paths(monkeypatch, tmp_path):
    monkeypatch.setenv('MAS_SKILLS_DIR', str(tmp_path / 'ambient'))
    monkeypatch.setenv('SkillsDir', str(tmp_path / 'legacy'))
    builder = SystemBuilder()
    assert builder.skills_dir == Path(__file__).resolve().parents[1] / 'skills'


def test_unconfigured_agent_cannot_gain_roots_from_environment(monkeypatch, tmp_path):
    target = tmp_path / 'outside.txt'
    target.write_text('outside workspace', encoding='utf-8')
    monkeypatch.setenv('MAS_WORKSPACE_ROOTS', str(tmp_path))
    agent = build_agent(AgentAppConfig.from_mapping({}), model=ScriptedModel(AI('done')))
    result = agent.tool_executor.execute([ToolCall('r', 'read_file', {'path': str(target)})])[0]
    assert not result.tool_success
    assert 'Path is outside the allowed workspace' in result.message


def test_agents_keep_distinct_tool_settings_after_environment_changes(monkeypatch, tmp_path):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    (first / 'data.txt').write_text('A' * 500, encoding='utf-8')
    (second / 'data.txt').write_text('B' * 500, encoding='utf-8')
    agents = []
    for root, limit, unsafe in ((first, '40', '0'), (second, '120', '1')):
        config = AgentAppConfig.from_mapping({
            'MAS_WORKSPACE_ROOTS': str(root), 'MAS_READ_FILE_MAX_CHARS': limit,
            'MAS_ALLOW_UNSAFE_TERMINAL': unsafe,
        })
        agents.append(build_agent(config, model=ScriptedModel(AI('done'))))
    monkeypatch.setenv('MAS_WORKSPACE_ROOTS', str(tmp_path / 'other'))
    monkeypatch.setenv('MAS_READ_FILE_MAX_CHARS', '250')
    monkeypatch.setenv('MAS_ALLOW_UNSAFE_TERMINAL', '1')
    monkeypatch.chdir(second)
    first_read = agents[0].tool_executor.execute([ToolCall('a', 'read_file', {'path': 'data.txt'})])[0]
    second_read = agents[1].tool_executor.execute([ToolCall('b', 'read_file', {'path': 'data.txt'})])[0]
    assert first_read.tool_success and second_read.tool_success
    assert len(first_read.message.split('\n...')[0]) == 40
    assert len(second_read.message.split('\n...')[0]) == 120
    assert 'AAAA' in first_read.message and 'BBBB' in second_read.message
    command = {'command': "printf '/mas-outside-config-probe'"}
    denied = agents[0].tool_executor.execute([ToolCall('c', 'terminal', command)])[0]
    allowed = agents[1].tool_executor.execute([ToolCall('d', 'terminal', command)])[0]
    assert not denied.tool_success
    assert allowed.tool_success and allowed.message == '/mas-outside-config-probe'


def test_from_env_captures_legacy_workspace_and_tool_options(monkeypatch, tmp_path):
    monkeypatch.delenv('MAS_WORKSPACE_ROOTS', raising=False)
    monkeypatch.setenv('WorkspaceRoots', str(tmp_path))
    monkeypatch.setenv('MAS_READ_FILE_MAX_CHARS', '75')
    monkeypatch.setenv('MAS_ALLOW_UNSAFE_TERMINAL', '1')
    config = AgentAppConfig.from_env()
    assert config.tools.workspace_roots == str(tmp_path)
    assert config.tools.read_file_max_chars == 75
    assert config.tools.allow_unsafe_terminal is True


def test_injected_tool_runtime_preserves_policy_after_registry_replacement(tmp_path):
    from core.agent import Agent
    from tools.runtime import ToolRuntime
    from tools.registry import ToolRegistry
    from tools.permissions import ToolPermissionPolicy
    from tools.workspace import Workspace
    from tools.workspace_binding import bind_workspace_tools
    registry = ToolRegistry()
    bind_workspace_tools(registry)
    runtime = ToolRuntime(registry, workspace=Workspace(roots=(str(tmp_path),)),
                          permission_policy=ToolPermissionPolicy.from_config(
                              enforce=True, rules='workspace_mutating=deny'))
    agent = Agent(ScriptedModel(AI('done')), tool_runtime=runtime)
    replacement = ToolRegistry()
    bind_workspace_tools(replacement)
    agent.registry = replacement
    result = agent.tool_executor.execute([ToolCall('w', 'write_file', {'path': 'blocked.txt', 'content': 'x'})])[0]
    assert not result.tool_success
    assert not (tmp_path / 'blocked.txt').exists()
    assert len(replacement.tool_audit_log.records()) == 1
    assert agent.registry is runtime.registry is agent.tool_executor.registry


def test_conflicting_injected_registry_is_rejected():
    from core.agent import Agent
    from tools.registry import ToolRegistry
    from tools.runtime import ToolRuntime
    with pytest.raises(ValueError, match='registry'):
        Agent(ScriptedModel(AI('done')), registry=ToolRegistry(),
              tool_runtime=ToolRuntime(ToolRegistry()))
