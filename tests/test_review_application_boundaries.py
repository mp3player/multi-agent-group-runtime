"""Shutdown recovery, portable prompts and public message round-trips."""

import asyncio

import pytest

from application import AgentAppConfig, AgentAppService, build_agent
from core.agent import Agent
from core.session import Session
from models import AI, JsonMessageSerde, Message, ToolCall, User
from tests.runtime_fakes import ScriptedModel


async def test_cancelled_close_waiter_does_not_cancel_shared_cleanup():
    started, finish = asyncio.Event(), asyncio.Event()

    class Model(ScriptedModel):
        closed = False
        closes = 0
        cancelled = False

        async def aclose(self):
            self.closes += 1
            started.set()
            try:
                await finish.wait()
                self.closed = True
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    model = Model()
    app = AgentAppService(AgentAppConfig(), model=model, owns_model=True)
    first = asyncio.create_task(app.aclose())
    second = None
    try:
        await asyncio.wait_for(started.wait(), 2)
        second = asyncio.create_task(app.aclose())
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not app.closed
        assert not model.closed
        assert not model.cancelled
        assert not second.done()
        with pytest.raises(RuntimeError, match='closing'):
            app.run('must not start')
        finish.set()
        await asyncio.wait_for(second, 2)
        await app.aclose()
        assert app.closed and model.closed
        assert model.closes == 1
    finally:
        finish.set()
        await asyncio.gather(first, *([second] if second is not None else []), return_exceptions=True)
        await app.aclose()


async def test_close_failure_blocks_runs_and_can_be_retried():
    class Model(ScriptedModel):
        attempts = 0
        closed = False

        async def aclose(self):
            self.attempts += 1
            if self.attempts == 1:
                raise OSError('cleanup failed')
            self.closed = True

    model = Model()
    app = AgentAppService(AgentAppConfig(), model=model, owns_model=True)
    try:
        with pytest.raises(OSError, match='cleanup failed'):
            await app.aclose()
        assert not app.closed and not model.closed
        with pytest.raises(RuntimeError, match='close_failed'):
            await app.arun('must not start')
        await app.aclose()
        assert app.closed and model.closed
        assert model.attempts == 2
    finally:
        await app.aclose()


def test_default_prompts_ignore_unrelated_cwd_and_explicit_directory_is_honored(tmp_path, monkeypatch):
    prompts = tmp_path / 'prompts'
    prompts.mkdir()
    (prompts / 'base.md').write_text('UNRELATED DIRECTORY')
    (prompts / 'order.txt').write_text('base\n')
    monkeypatch.chdir(tmp_path)
    default_model = ScriptedModel(AI('default ok'))
    agent = build_agent(AgentAppConfig.from_mapping({'MAS_ENABLE_TOOLS': 'false'}), model=default_model)
    assert agent.run('hello') == 'default ok'
    instructions = default_model.requests[0][0]
    assert instructions.role == 'system' and instructions.message
    assert 'UNRELATED DIRECTORY' not in instructions.message
    explicit_model = ScriptedModel(AI('explicit ok'))
    explicit = build_agent(AgentAppConfig.from_mapping({'MAS_PROMPTS_DIR': 'prompts', 'MAS_ENABLE_TOOLS': 'false'}), model=explicit_model)
    assert explicit.run('hello') == 'explicit ok'
    assert explicit_model.requests[0][0].message.startswith('UNRELATED DIRECTORY\n')
    assert 'read_context_archive' in explicit_model.requests[0][0].message


def test_public_message_serde_round_trip_can_continue_a_tool_conversation(tmp_path):
    result = Message('tool', 'operation complete')
    result.tool_call_id = 'work-1'
    transcript = [User('do work'), AI(tool_calls=[ToolCall('work-1', 'work', {})]), result]
    codec = JsonMessageSerde()
    path = tmp_path / 'messages.json'
    codec.save_to_file(transcript, path)
    restored = codec.load_from_file(path)
    session = Session()
    session.add_many(restored)
    model = ScriptedModel(AI('continued'))
    assert Agent(model, session=session).run('continue') == 'continued'
    tool_result = next(message for message in model.requests[0] if message.role == 'tool')
    assert tool_result.tool_call_id == 'work-1'
    assert tool_result.message == 'operation complete'
