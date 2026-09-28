"""Model-facing receipts identify the accepted outgoing operation and audience."""

import json

import pytest

from core.agent import Agent
from group import DispatchPlan, GroupRuntime, OnDemandStrategy, RunProposal
from models import AI, ToolCall
from tests.runtime_fakes import ScriptedModel


@pytest.mark.parametrize('tool_name,arguments,kind,recipients,opportunity_count', [
    ('group_post', {'recipients': ['bob']}, 'outgoing_post_receipt', ('bob',), 0),
    ('group_request', {'recipients': ['bob']}, 'outgoing_request_receipt', ('bob',), 1),
    ('group_request', {}, 'outgoing_request_receipt', (), 1),
    ('group_broadcast', {}, 'outgoing_request_receipt', ('bob', 'carol'), 2),
])
async def test_receipt_describes_the_committed_message_without_claiming_new_work(
        tmp_path, tool_name, arguments, kind, recipients, opportunity_count):
    model = ScriptedModel(AI(tool_calls=[
        ToolCall('publish', tool_name, {'content': 'Review the queue', **arguments}),
        ToolCall('yield', 'group_yield', {}),
    ]))
    alice = Agent(model)
    peers = {'bob': Agent(ScriptedModel()), 'carol': Agent(ScriptedModel())}
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'alice': alice, **peers},
            worker_safe=True, strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('alice', 'Publish once', origin_key='original'),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        receipt = json.loads(next(message.message for message in alice.session.history
                                  if message.role == 'tool' and message.tool_call_id == 'publish'))
        assert receipt['kind'] == kind
        message = await group.message(scope, receipt['message_id'])
        assert receipt['sender'] == message.sender == 'alice'
        assert tuple(receipt['recipients']) == message.recipients == recipients
        assert receipt['accepted'] is True and receipt['invocation_id'] == scope
        opportunities = (await group.opportunities(scope)).items
        assert len(opportunities) == opportunity_count
        assert set(receipt['opportunity_ids']) == {item.id for item in opportunities}
        assert all(item.message_id == message.id for item in opportunities)
        assert (await group.snapshot(scope)).admitted_count == 1
        assert not any(peer.llm.requests for peer in peers.values())


async def test_broadcast_receipt_remains_complete_with_maximum_escaped_member_names(tmp_path):
    names = tuple(f'{index:02d}' + '\\' * 126 for index in range(32))
    model = ScriptedModel(AI(tool_calls=[
        ToolCall('broadcast', 'group_broadcast', {'content': 'Review once'}),
        ToolCall('yield', 'group_yield', {}),
    ]))
    members = {name: Agent(model if index == 0 else ScriptedModel()) for index, name in enumerate(names)}
    for agent in members.values():
        agent.runtime.tool_runtime.max_result_chars = 13696
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', members, worker_safe=True,
                                        strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal(names[0], 'Invite all peers', origin_key='original'),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        result = next(message.message for message in members[names[0]].session.history
                      if message.role == 'tool' and message.tool_call_id == 'broadcast')
        receipt = json.loads(result)
        assert receipt['kind'] == 'outgoing_request_receipt'
        assert receipt['sender'] == names[0] and tuple(receipt['recipients']) == names[1:]
        assert len(receipt['opportunity_ids']) == 31
        assert len(result) <= 13696
