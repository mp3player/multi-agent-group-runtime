"""Standalone entrypoints do not import the legacy Group runtime."""
import asyncio
import builtins
import os
from pathlib import Path
import subprocess
import sys

import pytest
from application.agent_config import AgentAppConfig
from application.agent_service import AgentAppService
from models import AI


@pytest.mark.parametrize('module', ['cli.agent', 'main'])
def test_help_with_group_imports_blocked(module):
    code = '''
import importlib.abc, runpy, sys
blocked = ('core.group', 'domain.group', 'application.group', 'application.member_config',
           'core.member_config', 'prompting.group', 'application.config', 'application.builders',
           'core.config', 'cli.group')
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith(blocked):
            raise AssertionError('legacy import: ' + fullname)
sys.meta_path.insert(0, Block())
sys.argv = ['entry', '--help']
runpy.run_module(sys.argv_module, run_name='__main__')
'''.replace('sys.argv_module', repr(module))
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '--prompt' in result.stdout


class Model:
    context_window = 100000

    def __init__(self):
        self.closed = False
        self.loops = []
    async def ainvoke(self, messages, **kwargs):
        self.loops.append(asyncio.get_running_loop())
        return {'choices': [{'message': {'role': 'assistant', 'content': 'reply'}}]}
    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_commands_snapshot_new_history_usage_audit_and_cleanup(tmp_path, monkeypatch, capsys):
    from cli.agent import interact
    model = Model()
    app = AgentAppService(AgentAppConfig.from_mapping({'MAS_ENABLE_TOOLS': 'false', 'MAS_HISTORY_MESSAGE_LIMIT': '19'}), model=model, owns_model=True, use_stream=False)
    system = app.agent.system_builder.build()
    path = tmp_path / 'session with spaces.json'
    commands = iter(['hello', f'/save {path}', '/new', f'/load {path}', '/history', '/tools', '/usage', '/audit', '/stream', '/stream', 'again', '/new', '/exit'])
    monkeypatch.setattr(builtins, 'input', lambda _: next(commands))
    await interact(app)
    output = capsys.readouterr().out
    assert 'reply' in output and 'history:' in output and 'registered tools:' in output
    assert path.is_file()
    assert app.agent.session.history_limit == 19
    assert app.agent.session.active[0].message == system
    assert len(app.agent.session.active) == 1
    assert len(set(model.loops)) == 1
    assert model.closed and app.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('interrupt', [EOFError, KeyboardInterrupt])
async def test_input_interruption_closes_app(monkeypatch, interrupt):
    from cli.agent import interact
    model = Model()
    app = AgentAppService(AgentAppConfig(), model=model, owns_model=True)
    def stop(_):
        raise interrupt()
    monkeypatch.setattr(builtins, 'input', stop)
    await interact(app)
    assert app.closed and model.closed


def test_cli_omitted_override_preserves_environment_limits(monkeypatch):
    from cli.agent import create_service, parser
    monkeypatch.setattr('application.agent_config.load_project_env', lambda _: None)
    monkeypatch.setenv('BaseURL', 'http://127.0.0.1:1/v1')
    monkeypatch.setenv('BaseKey', 'test-only')
    monkeypatch.setenv('BaseModel', 'fixture')
    monkeypatch.setenv('MAS_DEFAULT_MAX_TURNS', '7')
    monkeypatch.setenv('MAS_ENABLE_TOOLS', 'false')
    app = create_service(parser().parse_args([]))
    assert app.config.agent.max_turns == 7
    assert not app.config.tools.enable_workspace_tools
    asyncio.run(app.aclose())


@pytest.mark.asyncio
async def test_cancelled_stream_closes_provider_and_application():
    from cli.agent import interact
    from models import Chunk
    class StreamingModel(Model):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.stream_closed = False
        async def astream(self, messages, **kwargs):
            try:
                yield Chunk(message='partial')
                self.started.set()
                await asyncio.Event().wait()
            finally:
                self.stream_closed = True
    model = StreamingModel()
    app = AgentAppService(AgentAppConfig(), model=model, owns_model=True)
    task = asyncio.create_task(interact(app, prompt='hello'))
    await model.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert model.stream_closed and model.closed and app.closed
    assert not app.agent.run_state.active


@pytest.mark.asyncio
async def test_failed_one_shot_closes_app():
    from cli.agent import interact
    class FailedModel(Model):
        async def ainvoke(self, messages, **kwargs):
            raise RuntimeError('provider failed')
    model = FailedModel()
    app = AgentAppService(AgentAppConfig(), model=model, owns_model=True, use_stream=False)
    with pytest.raises(RuntimeError, match='provider failed'):
        await interact(app, prompt='hello')
    assert model.closed and app.closed


@pytest.mark.asyncio
async def test_stream_displays_mixed_reasoning_and_answer(capsys):
    from cli.agent import interact
    from models import Chunk
    class MixedModel(Model):
        async def astream(self, messages, **kwargs):
            yield Chunk(message='final answer', reasoning='reasoning text')
    model = MixedModel()
    app = AgentAppService(AgentAppConfig(), model=model, owns_model=True)
    await interact(app, prompt='hello')
    output = capsys.readouterr().out
    assert 'final answer' in output and 'reasoning text' in output
