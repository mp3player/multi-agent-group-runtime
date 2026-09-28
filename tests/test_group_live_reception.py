"""Opt-in finite local-model collaboration with delayed complete reception."""

import os

import pytest

from application.agent_config import AgentAppConfig
from core.agent import Agent
from core.agent_runtime.options import AgentOptions
from core.llm import LLMClient
from group import GroupLimits, GroupRuntime, OnDemandStrategy

pytestmark = pytest.mark.skipif(os.environ.get('MAS_RUN_LIVE_GROUP_TESTS') != '1',
    reason='Set MAS_RUN_LIVE_GROUP_TESTS=1 to use the configured model provider')


class RecordingModel(LLMClient):
    def __init__(self, config):
        super().__init__(base_url=config.base_url, api_key=config.api_key, model=config.model,
            timeout=min(config.timeout, 60), trust_env=config.trust_env,
            stream_compatibility=config.stream_compatibility)
        self.inputs = []

    def invoke(self, messages, **kwargs):
        self.inputs.append(tuple((message.role, str(message.message)) for message in messages))
        return super().invoke(messages, **kwargs)


async def test_delayed_peer_uses_early_constraints_after_multi_page_collaboration(tmp_path):
    config = AgentAppConfig.from_env()
    assert config.agent.context_window, 'Configure MAS_CONTEXT_WINDOW for live acceptance'
    models = {name: RecordingModel(config.llm) for name in ('planner', 'reviewer', 'integrator')}
    members = {name: Agent(model, options=AgentOptions(
        max_turns=6, max_tokens=config.llm.max_tokens, temperature=config.llm.temperature,
        context_window=config.agent.context_window, context_input_limit=config.agent.context_input_limit,
        run_timeout=90), system_prompt=(
            f'You are {name}, an equal peer collaborating on a reliable local task queue design. '
            'Treat runtime reception markers as bookkeeping. Carry out only your current assigned trigger. '
            'Reply publicly using group_post with reply_to equal to the trigger message ID, then group_yield. '
            'Only request another peer when the current trigger explicitly asks for that exact handoff. '
            'Keep each public message below 180 words. Never ask for an acknowledgment.'
        )) for name, model in models.items()}
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', members, worker_safe=True,
            strategy=OnDemandStrategy(), limits=GroupLimits(page_size=2, max_page_size=3,
                max_runs=8, invocation_timeout=240)) as group:
        scope = await group.open_invocation('collaboration')
        constraint = 'QUEUE-731: the queue must preserve accepted tasks across process restarts and deduplicate retries.'
        await group.post(scope, constraint, key='initial-constraint')
        for index in range(8):
            await group.post(scope, f'Planning note {index}: local operation, bounded worker capacity, observable failures.', key=f'n{index}')
        await group.request(scope,
            'Draft a design using the original queue requirement. Publish it linked to this trigger. '
            'Then send exactly one group_request to reviewer asking for two failure cases and remedies '
            'using the full shared design context. Do not request integrator. End your current work.',
            key='plan', recipients=('planner',))
        first = await group.drive(scope)
        assert first.status == 'waiting' and first.admitted_count == 2
        assert models['integrator'].inputs == []
        for index in range(8):
            await group.post(scope, f'Integration note {index}: include failure handling and a recovery procedure.', key=f'i{index}')
        await group.drive(scope)
        assert models['integrator'].inputs == []
        await group.request(scope,
            'Integrate the earlier planner proposal and reviewer critique into one concrete design. '
            'Begin with the original requirement identifier and state its exact durability/deduplication constraints. '
            'Include one recovery sequence. Publish your reply linked to this trigger; do not request peers.',
            key='integrate', recipients=('integrator',))
        second = await group.drive(scope)
        assert second.status == 'waiting' and second.admitted_count == 3
        assert any(constraint in text for _, text in models['integrator'].inputs[0])
        history = []
        after = 0
        while True:
            page = await group.history(scope, after=after)
            history.extend(page.items)
            if page.exhausted:
                break
            after = page.next_cursor
        replies = [item.content for item in history if item.sender == 'integrator']
        assert replies and 'QUEUE-731' in '\n'.join(replies)
        await group.request(scope,
            'Review the integrated design already in shared history. State one concrete acceptance check '
            'for restart recovery and one for duplicate retries. Reply publicly; do not request peers.',
            key='acceptance', recipients=('planner',))
        final = await group.drive(scope)
        assert final.status == 'waiting' and final.admitted_count == 4
        assert all(item.satisfied for item in (await group.scheduling_view(scope)).evidence)
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)
        for member in members:
            assert (await group.reception(scope, member)).pending_count == 0
        print({'assignments': final.admitted_count,
               'provider_calls': {name: len(model.inputs) for name, model in models.items()},
               'delayed_original_visible': True, 'final_reception_complete': True})
