"""CLI context commands share the application's existing event loop."""
import asyncio
import builtins
import json

import pytest

from application.agent_service import AgentAppService
from cli.agent import interact
from tests.test_context_application import ContextAppModel, add_old_context, app_config


@pytest.mark.asyncio
async def test_cli_context_and_compact_keep_summary_out_of_answer_stream(tmp_path, monkeypatch, capsys):
    class LoopModel(ContextAppModel):
        async def ainvoke(self, messages, **kwargs):
            self.loop = asyncio.get_running_loop()
            return self.invoke(messages, **kwargs)

    model = LoopModel()
    app = AgentAppService(app_config(tmp_path, MAS_CONTEXT_WINDOW='9000',
                                    MAS_DEFAULT_MAX_TOKENS='256'), model=model, use_stream=False)
    add_old_context(app)
    commands = iter(['/context', '/compact preserve evidence', '/context', '/exit'])
    monkeypatch.setattr(builtins, 'input', lambda _: next(commands))
    await interact(app)
    output = capsys.readouterr()
    assert 'Private historical summary.' not in output.out
    assert 'context_compaction_start' in output.err
    assert 'context_compaction_end' in output.err
    statuses = [json.loads(line) for line in output.out.splitlines() if line.startswith('{')]
    assert len(statuses) == 2
    assert statuses[0]['capacity'] == 9000
    assert statuses[1]['compactions'] == 1
    assert 'MAS_ACTIVE_MESSAGE_LIMIT' in statuses[1]['migration_notice']
    assert 'preserve evidence' in model.calls[0][1].message
    assert model.loop is asyncio.get_running_loop()
    assert app.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('use_stream', [False, True])
async def test_automatic_compaction_progress_is_on_stderr(tmp_path, capsys, use_stream):
    app = AgentAppService(app_config(tmp_path, MAS_CONTEXT_WINDOW='9000',
                                    MAS_DEFAULT_MAX_TOKENS='256'),
                          model=ContextAppModel(), use_stream=use_stream)
    add_old_context(app)
    await interact(app, prompt='continue this task')
    output = capsys.readouterr()
    assert 'context_compaction_start' in output.err
    assert 'context_compaction_end' in output.err
    assert 'context_compaction_' not in output.out
    assert 'Final answer.' in output.out
    assert 'Private historical summary.' not in output.out
