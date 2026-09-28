"""Interactive Group composition uses the real runtime and offline model boundary."""

import asyncio
import importlib
import json
from threading import Event

import pytest

from application.agent_config import AgentAppConfig
from core.llm_runtime import to_dict_list
from group import GroupLimits
from group.errors import QueueCapacityError, StoreFailedError
from group.tools import execution_context


@pytest.fixture
def configured_models(tmp_path, monkeypatch):
    import application.agent_builder as builders
    models = []

    class Model:
        def __init__(self, **kwargs):
            self.model = kwargs['model']
            self.base_url = kwargs['base_url']
            self.requests = []
            self.seen = set()
            self.closed = False
            models.append(self)

        def invoke(self, messages, **kwargs):
            self.requests.append((to_dict_list(messages), kwargs.get('tools')))
            bound = execution_context.get()
            current = next(m for m in reversed(messages) if m.role == 'user')
            task = json.loads(current.message.rsplit('\n', 1)[1])['trigger_messages'][0]
            if task['content'] == 'provider failure':
                raise RuntimeError('Provider unavailable for this test')
            if bound.assignment_id not in self.seen:
                self.seen.add(bound.assignment_id)
                calls = [('group_members', {})]
            else:
                member_result = next(m for m in reversed(messages) if m.role == 'tool')
                names = [member['id'] for member in json.loads(member_result.message)]
                calls = [('group_post', {
                    'content': f'{len(names)} members: {", ".join(names)}. From {bound.member_id}.',
                    'reply_to': task['message_id']}), ('group_yield', {})]
                if task['content'] == 'ask bob' and bound.member_id == 'alice':
                    calls.insert(1, ('group_request', {'content': 'Review this answer', 'recipients': ['bob']}))
            return {'choices': [{'finish_reason': 'tool_calls', 'message': {
                'role': 'assistant', 'content': 'Private reasoning is not public.',
                'tool_calls': [{'id': f'{bound.assignment_id}-{len(self.requests)}-{index}',
                    'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}
                    for index, (name, arguments) in enumerate(calls)]}}]}

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(builders, 'LLMClient', Model)
    config = AgentAppConfig.from_mapping({
        'BaseURL': 'http://unused.invalid/v1', 'BaseKey': 'test', 'BaseModel': 'offline',
        'MAS_CONTEXT_WINDOW': '131072', 'MAS_CONTEXT_ARCHIVE_DIR': str(tmp_path / 'archives'),
        'MAS_PROMPTS_DIR': str(tmp_path / 'prompts'), 'MAS_SKILLS_DIR': str(tmp_path / 'skills'),
    })
    return config, models


def modules():
    return importlib.import_module('application.group_chat'), importlib.import_module('cli.group')


def set_input(monkeypatch, cli, function):
    async def read(self, prompt):
        return function(prompt)
    monkeypatch.setattr(cli.TerminalInput, 'read', read)


async def test_group_chat_binds_members_tools_and_closes_owned_resources(tmp_path, configured_models):
    app, _ = modules()
    config, models = configured_models
    chat = await app.GroupChat.create(config, store=tmp_path / 'chat.sqlite')
    try:
        assert chat.scope is None
        assert [member.id for member in await chat.runtime.members()] == ['alice', 'bob', 'carol']
        agents = [worker.agent for worker in chat.runtime.workers.values()]
        assert len({id(agent.session) for agent in agents}) == 3
        assert len({id(agent.registry) for agent in agents}) == 3
        for agent in agents:
            assert agent.registry.has('group_members') and agent.registry.has('group_request')
            assert not agent.registry.has('terminal')
        await chat.send('How many members are in this group?')
        result = await chat.runtime.drive(chat.scope)
        assert result.status == 'waiting' and result.admitted_count == 1
        assert not result.missing_reply_ids
        messages = [m async for m in chat.history()]
        assert any('3 members: alice, bob, carol' in m.content and m.sender == 'alice' for m in messages)
        assert not models[1].requests and not models[2].requests
        for messages, tools in models[0].requests:
            assert 'group_members' in {tool['function']['name'] for tool in tools}
            assert chat.runtime.profile.instructions in messages[0]['content']
    finally:
        await chat.close()
    assert all(model.closed for model in models)
    assert all(not agent.registry.has('group_members') for agent in agents)
    await chat.close()


async def test_interactive_routing_passive_posts_and_paginated_history(tmp_path, configured_models, monkeypatch, capsys):
    app, cli = modules()
    config, models = configured_models
    chat = await app.GroupChat.create(config, store=tmp_path / 'chat.sqlite',
                                     limits=GroupLimits(page_size=1))
    commands = iter(['/members', '/post Background only', 'hello', '@bob hello',
                     '@all hello', '/history', '/status', '/finish', '/exit'])
    def read_input(prompt):
        command = next(commands)
        if command == 'hello':
            assert not any(model.requests for model in models)
        return command
    set_input(monkeypatch, cli, read_input)
    try:
        assert await cli.interact(chat) == 0
        messages = [m async for m in chat.history()]
        external = [m for m in messages if m.sender == 'user']
        assert [(m.content, m.recipients) for m in external] == [
            ('Background only', ()), ('hello', ()), ('hello', ('bob',)),
            ('hello', ('alice', 'bob', 'carol'))]
        assert [len(model.requests) for model in models] == [4, 4, 2]
        assert (await chat.runtime.snapshot(chat.scope)).reason == 'completed'
    finally:
        await chat.close()
    output = capsys.readouterr().out
    assert '[alice]' in output and '[bob]' in output and '[carol]' in output
    assert '3 members' in output and 'Background only' in output
    assert 'Private reasoning' not in output


async def test_cli_drives_peer_created_handoff(tmp_path, configured_models, capsys):
    app, cli = modules()
    config, models = configured_models
    async with await app.GroupChat.create(config, store=tmp_path / 'chat.sqlite') as chat:
        assert await cli.interact(chat, prompt='ask bob') == 0
        assert [len(model.requests) for model in models] == [2, 2, 0]
        assert (await chat.runtime.snapshot(chat.scope)).state == 'open'
    assert '[bob]' in capsys.readouterr().out
    assert all(model.closed for model in models)


async def test_cli_reports_provider_failure_without_replaying(tmp_path, configured_models, capsys):
    app, cli = modules()
    config, models = configured_models
    async with await app.GroupChat.create(config, store=tmp_path / 'chat.sqlite') as chat:
        assert await cli.interact(chat, prompt='provider failure') == 1
        assignments = (await chat.runtime.assignments(chat.scope)).items
        assert len(assignments) == 1 and assignments[0].error
        assert len(models[0].requests) == 1
    output = capsys.readouterr().out
    assert 'needs_input' in output and 'Provider unavailable' in output


@pytest.mark.parametrize('members', [('alice', 'alice'), ('user',), ('all',), ('bad name',), ()])
async def test_invalid_members_do_not_create_store_or_models(tmp_path, configured_models, members):
    app, _ = modules()
    config, models = configured_models
    path = tmp_path / 'chat.sqlite'
    with pytest.raises(ValueError):
        await app.GroupChat.create(config, members=members, store=path)
    assert not models and not path.exists()


async def test_existing_store_is_preserved_before_model_creation(tmp_path, configured_models):
    app, _ = modules()
    config, models = configured_models
    path = tmp_path / 'chat.sqlite'
    path.write_bytes(b'Existing user data')
    with pytest.raises(FileExistsError):
        await app.GroupChat.create(config, store=path)
    assert path.read_bytes() == b'Existing user data' and not models


async def test_group_create_failure_closes_built_providers(tmp_path, configured_models, monkeypatch):
    app, _ = modules()
    config, models = configured_models
    async def fail(*args, **kwargs):
        raise RuntimeError('Store creation failed')
    monkeypatch.setattr(app.GroupRuntime, 'create', fail)
    with pytest.raises(RuntimeError, match='Store creation failed'):
        await app.GroupChat.create(config, store=tmp_path / 'chat.sqlite')
    assert len(models) == 3 and all(model.closed for model in models)


async def test_later_discussion_keeps_same_member_sessions(tmp_path, configured_models):
    app, _ = modules()
    config, models = configured_models
    async with await app.GroupChat.create(config, store=tmp_path / 'chat.sqlite') as chat:
        await chat.send('hello')
        await chat.runtime.drive(chat.scope)
        first = chat.scope
        await chat.runtime.cancel(first)
        await chat.send('hello', recipients=('bob',))
        await chat.runtime.drive(chat.scope)
        assert first != chat.scope
        assert len([m async for m in chat.history()]) == 4
        assert any('Historical public source data' in m['content']
                   for m in models[1].requests[0][0] if m['role'] == 'user')


async def test_cli_unknown_target_and_empty_commands_do_not_start_inference(tmp_path, configured_models, monkeypatch, capsys):
    app, cli = modules()
    config, models = configured_models
    commands = iter(['@nobody hello', '@bob', '/post', '/broadcast', '/unknown', '/exit'])
    set_input(monkeypatch, cli, lambda _: next(commands))
    async with await app.GroupChat.create(config, store=tmp_path / 'chat.sqlite') as chat:
        assert await cli.interact(chat) == 0
        assert not any(model.requests for model in models)
        assert chat.scope is None
    assert capsys.readouterr().out.count('[error]') == 5


async def test_display_failure_settles_active_execution_before_returning(tmp_path, configured_models, monkeypatch):
    app, cli = modules()
    config, models = configured_models
    entered, release = Event(), Event()
    chat = await app.GroupChat.create(config, store=tmp_path / 'chat.sqlite')
    original = models[0].invoke
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(models[0], 'invoke', blocked)
    driver = failed = None
    try:
        await chat.send('hello')
        driver = asyncio.create_task(chat.runtime.drive(chat.scope))
        assert await asyncio.to_thread(entered.wait, 2)
        console = cli.Console(chat)
        async def fail():
            raise ValueError('Display failed')
        monkeypatch.setattr(console, 'emit_new', fail)
        failed = asyncio.create_task(console.drive())
        async def settle():
            while not failed.done():
                if (await chat.runtime.snapshot(chat.scope)).state == 'closing':
                    release.set()
                await asyncio.sleep(0)
            with pytest.raises(ValueError, match='Display failed'):
                await failed
            assert (await chat.runtime.snapshot(chat.scope)).active_count == 0
        await asyncio.wait_for(settle(), 2)
    finally:
        release.set()
        await chat.close()
        await asyncio.gather(*(t for t in (driver, failed) if t is not None), return_exceptions=True)


@pytest.mark.parametrize('failure', [OSError('History unavailable'), StoreFailedError('Store failed')])
async def test_fatal_console_errors_exit_instead_of_reading_more_input(tmp_path, configured_models, monkeypatch, failure):
    app, cli = modules()
    config, models = configured_models
    commands = iter(['hello', '/exit'])
    reads = []
    def read_input(prompt):
        reads.append(prompt)
        return next(commands)
    async def fail(*args):
        raise failure
    set_input(monkeypatch, cli, read_input)
    monkeypatch.setattr(cli.Console, 'handle', fail)
    with pytest.raises(type(failure), match=str(failure)):
        async with await app.GroupChat.create(config, store=tmp_path / 'chat.sqlite') as chat:
            await cli.interact(chat)
    assert len(reads) == 1 and all(model.closed for model in models)


async def test_one_shot_exit_does_not_invoke_provider(tmp_path, configured_models):
    app, cli = modules()
    config, models = configured_models
    async with await app.GroupChat.create(config, store=tmp_path / 'chat.sqlite') as chat:
        assert await cli.interact(chat, prompt='/exit') == 0
    assert all(model.closed and not model.requests for model in models)


async def test_failed_cancellation_exits_after_forced_close(tmp_path, configured_models, monkeypatch):
    app, cli = modules()
    config, models = configured_models
    commands = iter(['hello', '/exit'])
    reads = []
    def read_input(prompt):
        reads.append(prompt)
        return next(commands)
    original = cli.Console.emit_new
    displays = 0
    async def fail_display(console):
        nonlocal displays
        displays += 1
        if displays > 1:
            raise ValueError('Display failed')
        await original(console)
    async def fail_cancel(*args):
        raise QueueCapacityError('Cancellation admission is full')
    set_input(monkeypatch, cli, read_input)
    monkeypatch.setattr(cli.Console, 'emit_new', fail_display)
    async with await app.GroupChat.create(config, store=tmp_path / 'chat.sqlite') as chat:
        monkeypatch.setattr(chat.runtime, 'cancel', fail_cancel)
        with pytest.raises(QueueCapacityError):
            await cli.interact(chat)
    assert len(reads) == 1 and all(model.closed for model in models)


async def test_expired_idle_discussion_reopens_without_losing_background(tmp_path, configured_models):
    app, _ = modules()
    config, models = configured_models
    async with await app.GroupChat.create(config, store=tmp_path / 'chat.sqlite',
            limits=GroupLimits(invocation_timeout=0.05)) as chat:
        await chat.send('Retained background', passive=True)
        first = chat.scope
        await asyncio.sleep(0.06)
        await chat.send('More background', passive=True)
        assert first != chat.scope
        assert (await chat.runtime.snapshot(first)).reason == 'timeout'
        assert [m.content async for m in chat.history()] == ['Retained background', 'More background']
        assert not any(model.requests for model in models)
