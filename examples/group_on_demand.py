"""Offline peer collaboration with an explicitly selected on-demand strategy."""

import argparse
import asyncio
import json
from pathlib import Path
from tempfile import mkdtemp
from uuid import uuid4

from core.agent import Agent
from group import GroupRuntime, OnDemandStrategy


class ExampleModel:
    """Deterministic provider exercising real tools, execution and persistence."""

    model = 'offline-on-demand-example'
    context_window = 32768
    base_url = 'http://unused.invalid'

    def __init__(self, name):
        self.name = name
        self.calls = 0

    def invoke(self, messages, **options):
        self.calls += 1
        user = next(message for message in reversed(messages) if message.role == 'user')
        data = json.loads(user.message.rsplit('\n', 1)[1])
        trigger = data['trigger_messages'][0]
        calls = []

        def call(name, **arguments):
            calls.append({'id': str(len(calls)), 'type': 'function',
                          'function': {'name': name, 'arguments': arguments}})

        call('group_post', content=f'{self.name}: reviewed {trigger["content"]}',
             reply_to=trigger['message_id'])
        if trigger['sender'] == 'user' and self.name == 'alice':
            call('group_request', content='Check the failure cases', recipients=['bob'])
            call('group_broadcast', content='Offer one improvement')
        call('group_yield')
        return {'choices': [{'message': {'role': 'assistant', 'content': '', 'tool_calls': calls},
                             'finish_reason': 'tool_calls'}]}


async def main(path):
    models = {name: ExampleModel(name) for name in ('alice', 'bob', 'carol')}
    members = {name: Agent(model) for name, model in models.items()}
    async with await GroupRuntime.create(path, members, worker_safe=True,
                                         strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation(f'example-{uuid4().hex}')
        await group.post(scope, 'Keep replies concise and check failure handling.', key='background')
        assert (await group.drive(scope)).status == 'waiting'
        assert not any(model.calls for model in models.values())
        print('Background stored; no member activated.')
        await group.request(scope, 'Review the collaboration design', key='review', recipients=('alice',))
        result = await group.drive(scope)
        assert result.status == 'waiting' and result.admitted_count == 4
        assert {name: model.calls for name, model in models.items()} == {'alice': 1, 'bob': 2, 'carol': 1}
        for message in (await group.history(scope)).items:
            print(f'{message.sender}: {message.content}')
        print(f'Driver: {result.status}; {result.admitted_count} runs; no reply feedback loop.')
        # The application decides this example is complete after inspecting its results.
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)
        print(f'Explicitly completed; public history retained at {path.resolve()}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path,
                        help='New SQLite path; defaults to a fresh temporary directory')
    path = parser.parse_args().store
    if path is None:
        path = Path(mkdtemp(prefix='mas-group-on-demand-')) / 'group.sqlite'
    elif path.exists() or path.is_symlink():
        parser.error('The store already exists. This example creates fresh Agent sessions; '
                     'choose a new --store path or omit --store. Existing history is retained.')
    asyncio.run(main(path))
