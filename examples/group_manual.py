"""Offline Group example: explicit plans, real Agent/tool execution, no policy."""

import argparse
import asyncio
from pathlib import Path
from tempfile import mkdtemp
from uuid import uuid4

from core.agent import Agent
from group import DispatchPlan, GroupRuntime, RunProposal


class ExampleModel:
    """Deterministic provider boundary so the example needs no model endpoint."""

    model = 'offline-group-example'
    context_window = 32768
    base_url = 'http://unused.invalid'

    def __init__(self, name):
        self.name = name
        self._posted = False

    def invoke(self, messages, **options):
        if self._posted:
            message = {'role': 'assistant', 'content': f'{self.name}: private supporting notes'}
            finish = 'stop'
        else:
            self._posted = True
            message = {'role': 'assistant', 'content': '', 'tool_calls': [{
                'id': 'provider-call-1', 'type': 'function',
                'function': {'name': 'group_post', 'arguments': {
                    'content': 'My public review is complete',
                }},
            }]}
            finish = 'tool_calls'
        return {'choices': [{'message': message, 'finish_reason': finish}]}


async def main(path):
    members = {name: Agent(ExampleModel(name)) for name in ('alice', 'bob')}
    async with await GroupRuntime.create(path, members, worker_safe=True) as group:
        scope = await group.open_invocation(f'example-{uuid4().hex}')
        receipt = await group.request(scope, 'Review the local collaboration foundation',
                                      key='review', recipients=('alice', 'bob'))
        snapshot = await group.snapshot(scope)
        print(f'Accepted: {snapshot.pending_count} pending; {snapshot.active_count} running')
        await group.commit_plan(DispatchPlan(scope, snapshot.revision, runs=(
            RunProposal('alice', 'Review correctness', (receipt.opportunity_ids[0],)),
            RunProposal('bob', 'Review usability', (receipt.opportunity_ids[1],)),
        )))
        await group.launch_ready(scope)
        await group.wait_idle()
        for message in (await group.history(scope)).items:
            print(f'{message.sender}: {message.content}')
        snapshot = await group.snapshot(scope)
        await group.finish(scope, revision=snapshot.revision)
        print(f'Invocation completed; public history retained at {path.resolve()}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path,
                        help='New SQLite path; defaults to a fresh temporary directory')
    path = parser.parse_args().store
    if path is None:
        path = Path(mkdtemp(prefix='mas-group-manual-')) / 'group.sqlite'
    elif path.exists() or path.is_symlink():
        parser.error('The store already exists. This example creates fresh Agent sessions; '
                     'choose a new --store path or omit --store. Existing history is retained.')
    asyncio.run(main(path))
