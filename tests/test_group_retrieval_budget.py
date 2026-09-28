"""Group retrieval pages must leave useful room in a small model context."""

import asyncio
import json

import pytest

from core.agent import Agent
from core.agent_runtime.options import AgentOptions
from group import GroupLimits, GroupRuntime
from group.errors import LifecycleError
from group.tools import ExecutionContext, execution_context, group_history, group_message
from models import Message
from tests.runtime_fakes import ScriptedModel


async def query(group, function, **kwargs):
    def read():
        token = execution_context.set(ExecutionContext(group, 's', 'reader', 'read-test'))
        try:
            return function(**kwargs)
        finally:
            execution_context.reset(token)
    return await asyncio.to_thread(read)


def assert_bounded(agent, result):
    manager = agent.runtime.context_manager
    budget = manager.model_budget().input_budget(agent.runtime.max_tokens, extra_reserve=manager.extra_reserve)
    message = Message(role='tool', message=result)
    message.tool_call_id = 'retrieval-test'
    assert manager.counter.estimate([message]).tokens <= budget // 4
    assert len(result) <= agent.runtime.tool_runtime.max_result_chars


@pytest.mark.parametrize('original', ['x' * 6000, '\u03bb"\\\n\x01' * 1200], ids=['ascii', 'escaped-unicode'])
async def test_retrieval_pages_fit_input_share_and_reconstruct_all_originals(tmp_path, original):
    agent = Agent(ScriptedModel(), options=AgentOptions(context_window=32000, context_input_limit=12000,
                                                       max_tokens=2048))
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'reader': agent}, worker_safe=True,
                                      limits=GroupLimits(page_size=100))
    try:
        await group.open_invocation('s')
        receipts = [await group.post('s', f'{index}:{original}', key=str(index)) for index in range(12)]
        # The corrected reserve and explicit input limit are part of the target.
        agent.runtime.context_manager.extra_reserve = 18000
        raw = await query(group, group_history, limit=100)
        assert_bounded(agent, raw)
        page = json.loads(raw)
        assert 0 < len(page['items']) < len(receipts)
        high_water = page['high_water']
        await group.post('s', 'outside the original snapshot', key='later')
        seen = []
        while True:
            for item in page['items']:
                source = (await group.message('s', item['id'])).content
                assert source.startswith(item['content'])
                assert item['next_content_offset'] == len(item['content'])
                assert item['content_complete'] == (item['content'] == source)
                seen.append(item['id'])
            if page['exhausted']:
                break
            previous = page['next_cursor']
            raw = await query(group, group_history, after=previous, high_water=high_water, limit=100)
            assert_bounded(agent, raw)
            page = json.loads(raw)
            assert page['next_cursor'] > previous
        assert seen == [receipt.message_id for receipt in receipts]
        rebuilt, offset = '', 0
        while True:
            raw = await query(group, group_message, message_id=receipts[0].message_id, offset=offset, limit=16000)
            assert_bounded(agent, raw)
            chunk = json.loads(raw)
            rebuilt += chunk['content']
            if chunk['exhausted']:
                break
            assert chunk['next_offset'] > offset
            offset = chunk['next_offset']
        assert rebuilt == '0:' + original
        end = json.loads(await query(group, group_message, message_id=receipts[0].message_id, offset=len(rebuilt)))
        assert end['content'] == '' and end['exhausted'] is True
    finally:
        await group.close()


async def test_message_query_rejects_a_nonfinal_chunk_that_cannot_advance(tmp_path):
    from group import state as sql
    agent = Agent(ScriptedModel(), options=AgentOptions(context_window=16000))
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'reader': agent}, worker_safe=True)
    try:
        await group.open_invocation('s')
        receipt = await group.post('s', 'xx', key='one')
        envelope = {'message_id': receipt.message_id, 'content': '', 'next_offset': 0, 'exhausted': False}
        agent.runtime.tool_runtime.max_result_chars = len(sql.json_text(envelope))
        with pytest.raises(LifecycleError, match='(advance|progress|content)'):
            await query(group, group_message, message_id=receipt.message_id, limit=1)
    finally:
        await group.close()
