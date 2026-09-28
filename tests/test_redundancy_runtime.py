"""Regressions for unnecessary runtime work and explicit CLI logging."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from core.agent import Agent
from core.session import Session
from models import User


def test_new_task_does_not_build_an_unused_active_view():
    class TrackedSession(Session):
        active_view_calls = 0

        def active_messages(self):
            self.active_view_calls += 1
            return super().active_messages()

    class Model:
        context_window = 100000

        def invoke(self, messages, **kwargs):
            assert [message.message for message in messages] == ['earlier', 'current']
            messages[0].message = 'provider-local mutation'
            return {'choices': [{'message': {'content': 'done'}}]}

    session = TrackedSession()
    session.add(User('earlier'))
    agent = Agent(Model(), session=session)
    assert agent.run('current') == 'done'
    assert [message.message for message in session.history] == ['earlier', 'current', 'done']
    assert session.active_view_calls == 0


def _run_isolated(code, tmp_path):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    return subprocess.run(
        [sys.executable, '-c', code], cwd=tmp_path, env=env,
        text=True, capture_output=True, check=True,
    )


def test_importing_logger_does_not_create_files(tmp_path):
    _run_isolated('import core.logger', tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('level, debug_visible', [('debug', True), ('ERROR', False)])
def test_cli_honors_log_level_on_stderr(tmp_path, level, debug_visible):
    result = _run_isolated(f'''
import logging
from types import SimpleNamespace
import cli.agent as cli
from application.agent_config import AgentAppConfig

config = AgentAppConfig.from_mapping({{"MAS_LOG_LEVEL": {level!r}}})
cli.AgentAppConfig.from_env = lambda: config
cli.AgentAppService = lambda config, **kwargs: SimpleNamespace(config=config)
async def interact(service, **kwargs):
    logging.getLogger("mas.test").debug("debug-marker")
    logging.getLogger("mas.test").error("error-marker")
cli.interact = interact
cli.cli_main([])
''', tmp_path)
    assert 'error-marker' in result.stderr
    assert ('debug-marker' in result.stderr) is debug_visible
    assert result.stdout == ''
    assert list(tmp_path.iterdir()) == []
