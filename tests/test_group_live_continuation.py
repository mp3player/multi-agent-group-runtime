"""Opt-in local-provider regression for continuing after an outgoing request.

Run with MAS_RUN_LIVE_GROUP_TESTS=1. The first Alice turn and peer replies are
scripted to isolate the boundary; Alice's subsequent decisions use the real
provider configured in the project .env. No credentials enter saved traces.
"""

import json
import os

import pytest

from application.agent_config import AgentAppConfig
from core.agent import Agent
from core.agent_runtime.options import AgentOptions
from core.llm import LLMClient
from core.llm_runtime import to_dict_list
from group import GroupLimits, GroupRuntime, OnDemandStrategy
from models import AI, ToolCall
from tests.runtime_fakes import ScriptedModel


pytestmark = pytest.mark.skipif(
    os.environ.get('MAS_RUN_LIVE_GROUP_TESTS') != '1',
    reason='Set MAS_RUN_LIVE_GROUP_TESTS=1 to use the configured model provider',
)


def trigger_id(messages):
    prompt = next(message.message for message in reversed(messages) if message.role == 'user')
    return json.loads(prompt.rsplit('\n', 1)[1])['trigger_messages'][0]['message_id']


class RequestThenContinue(LLMClient):
    """Seed an outgoing request without yielding, then observe real decisions."""

    def __init__(self, config, request_tool):
        super().__init__(base_url=config.base_url, api_key=config.api_key, model=config.model,
            timeout=min(config.timeout, 60), trust_env=config.trust_env,
            stream_compatibility=config.stream_compatibility)
        self.request_tool = request_tool
        self.seeded = False
        self.trace = []

    def invoke(self, messages, **kwargs):
        if not self.seeded:
            self.seeded = True
            arguments = {'content': 'Suggest one mitigation for data loss in an in-memory task queue.'}
            if self.request_tool == 'group_request':
                arguments['recipients'] = ['bob']
            response = AI(tool_calls=[
                ToolCall('seed-post', 'group_post', {
                    'content': 'In-memory queues can lose tasks after a crash.',
                    'reply_to': trigger_id(messages),
                }),
                ToolCall('seed-request', self.request_tool, arguments),
            ])
            return {'choices': [{'message': response.to_dict(), 'finish_reason': 'tool_calls'}]}
        response = super().invoke(messages, **kwargs)
        self.trace.append({'messages': to_dict_list(messages), 'response': response})
        return response


class PeerReply(ScriptedModel):
    def invoke(self, messages, **kwargs):
        self.responses.append(AI(tool_calls=[
            ToolCall('reply', 'group_post', {'content': 'Persist tasks before execution.',
                                          'reply_to': trigger_id(messages)}),
            ToolCall('yield', 'group_yield', {}),
        ]))
        return super().invoke(messages, **kwargs)


@pytest.mark.parametrize('request_tool', ['group_request', 'group_broadcast'])
async def test_requester_continuation_does_not_create_unrequested_activations(tmp_path, request_tool):
    config = AgentAppConfig.from_env()
    assert config.agent.context_window, 'Configure MAS_CONTEXT_WINDOW before live acceptance'
    model = RequestThenContinue(config.llm, request_tool)
    alice = Agent(model, options=AgentOptions(
        max_turns=6, max_tokens=config.llm.max_tokens, temperature=config.llm.temperature,
        context_window=config.agent.context_window, context_input_limit=config.agent.context_input_limit,
        run_timeout=90,
    ), system_prompt=(
        'You are alice, an equal peer in a local collaboration test. '
        'Carry out the current trigger request. Use the provided group tools for public replies. '
        'Do not respond to background messages separately. Only request peer work when the current '
        'trigger explicitly asks you to; otherwise publish your answer and call group_yield. '
        'Keep public answers concise, under 80 words.'
    ))
    peers = {'bob': PeerReply(), 'carol': PeerReply()}
    group = await GroupRuntime.create(tmp_path / 'group.sqlite',
        {'alice': alice, **{name: Agent(peer) for name, peer in peers.items()}},
        worker_safe=True, strategy=OnDemandStrategy(), limits=GroupLimits(max_runs=6, invocation_timeout=120))
    try:
        scope = await group.open_invocation('continuation')
        targets = 'each other member' if request_tool == 'group_broadcast' else 'bob'
        request = await group.request(scope,
            'Publish one short risk of an in-memory task queue, linked to this trigger using group_post. '
            f'Then use {request_tool} exactly once to ask {targets} to suggest one mitigation and '
            'publish it linked to that request. You may add relevant public follow-ups. '
            'Do not create further response requests just to acknowledge replies. '
            'Use group_yield when your current work is done.', key='task', recipients=('alice',))
        result = await group.drive(scope)
        history = (await group.history(scope)).items
        request_ids = {item.message_id for item in (await group.opportunities(scope)).items}
        outgoing = [message for message in history if message.sender == 'alice' and message.id in request_ids]
        assert len(outgoing) == 1
        assert outgoing[0].recipients == (('bob', 'carol') if request_tool == 'group_broadcast' else ('bob',))
        assert model.trace, 'The assertion must exercise a real model continuation'
        assert result.status == 'waiting' and not result.missing_reply_ids
        assert result.admitted_count == (3 if request_tool == 'group_broadcast' else 2)
        assignments = (await group.assignments(scope)).items
        alice_run = next(run for run in assignments if run.member_id == 'alice')
        assert alice_run.outcome == 'tool_stop' and alice_run.error is None
        alice_posts = [message for message in history if message.sender == 'alice' and message.id not in request_ids]
        assert any(message.reply_to == request.message_id for message in alice_posts)
        assert all(message.run_id == alice_run.id for message in alice_posts)
        before = (len(model.trace), tuple(len(peer.requests) for peer in peers.values()))
        await group.post(scope, 'No new response requested.', key='passive-follow-up')
        idle = await group.drive(scope)
        assert idle.status == 'waiting' and idle.admitted_count == result.admitted_count
        assert (len(model.trace), tuple(len(peer.requests) for peer in peers.values())) == before
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)
    finally:
        (tmp_path / 'continuation-trace.json').write_text(json.dumps(model.trace, indent=2))
        await group.close()
        await model.aclose()
