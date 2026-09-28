"""Publication feedback observes the current execution without scheduling work."""

import json

import pytest

from core.agent import Agent
from group import DispatchPlan, GroupLimits, GroupRuntime, RunProposal
from group.profiles import CollaborationProfile
from models import AI, ToolCall
from tests.runtime_fakes import ScriptedModel


def receipt(agent, call_id):
    message = next(item for item in agent.session.history
                   if item.role == 'tool' and item.tool_call_id == call_id)
    assert message.tool_success
    return json.loads(message.message)


@pytest.mark.parametrize('required', [True, False])
async def test_live_feedback_distinguishes_trigger_links_from_free_replies(tmp_path, required):
    model = ScriptedModel()
    alice, bob = Agent(model), Agent(ScriptedModel())
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'alice': alice, 'bob': bob},
            worker_safe=True, profile=CollaborationProfile(require_public_reply=required)) as group:
        scope = await group.open_invocation('s')
        background = await group.post(scope, 'Background', key='background')
        first = await group.request(scope, 'First task', key='first', recipients=('alice',))
        second = await group.request(scope, 'Second task', key='second', recipients=('alice',))
        model.responses.append(AI(tool_calls=[
            ToolCall('aside', 'group_post', {'content': 'A free follow-up',
                                          'reply_to': background.message_id}),
            ToolCall('answer', 'group_post', {'content': 'First answer',
                                           'reply_to': first.message_id}),
            ToolCall('yield', 'group_yield', {}),
        ]))
        assignments = await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('alice', 'Answer assigned tasks',
                              first.opportunity_ids + second.opportunity_ids),)))
        await group.launch_ready(scope)
        await group.wait_idle()

        aside, answer = receipt(alice, 'aside'), receipt(alice, 'answer')
        before, after = aside['feedback'], answer['feedback']
        assert before['available'] and before['execution_state'] == 'running'
        assert before['require_public_reply'] is required
        assert before['assignment_id'] == assignments[0]
        assert before['trigger_count'] == 2 and before['linked_trigger_count'] == 0
        assert before['reply_matches_trigger'] is False
        assert after['trigger_count'] == 2 and after['linked_trigger_count'] == 1
        assert after['unlinked_trigger_count'] == 1
        assert after['unlinked_trigger_ids'] == [second.message_id]
        assert after['unlinked_trigger_ids_complete'] is True
        assert after['reply_matches_trigger'] is True
        assert 'completed' not in after
        assert after['revision'] >= before['revision']
        assert len(model.requests) == 1  # Yield cannot be blocked by missing evidence.
        assert not bob.llm.requests
        snapshot = await group.snapshot(scope)
        assert snapshot.pending_count == 0 and snapshot.admitted_count == 1
        evidence = (await group.scheduling_view(scope)).evidence
        assert [item.satisfied for item in evidence] == [True, False]
        assert (await group.message(scope, aside['message_id'])).reply_to == background.message_id
        assert (await group.message(scope, answer['message_id'])).reply_to == first.message_id


async def test_manual_assignment_without_triggers_has_no_invented_response_work(tmp_path):
    model = ScriptedModel(AI(tool_calls=[
        ToolCall('post', 'group_post', {'content': 'Voluntary contribution'}),
        ToolCall('yield', 'group_yield', {}),
    ]))
    agent = Agent(model)
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'alice': agent},
                                         worker_safe=True) as group:
        scope = await group.open_invocation('s')
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('alice', 'Contribute freely', origin_key='manual'),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        feedback = receipt(agent, 'post')['feedback']
        assert feedback['trigger_count'] == feedback['unlinked_trigger_count'] == 0
        assert feedback['unlinked_trigger_ids'] == []
        assert feedback['require_public_reply'] is False
        assert not (await group.opportunities(scope)).items
        assert len(model.requests) == 1


@pytest.mark.parametrize('failure', ['query', 'budget'])
async def test_optional_feedback_failure_preserves_committed_publication(tmp_path, monkeypatch, failure):
    from group import feedback, tools

    def unavailable(*args, **kwargs):
        raise RuntimeError('Optional observation unavailable')

    if failure == 'query':
        monkeypatch.setattr(feedback, 'publication', unavailable)
    else:
        monkeypatch.setattr(tools, '_retrieval_fit', unavailable)
    model = ScriptedModel(AI(tool_calls=[
        ToolCall('post', 'group_post', {'content': 'Published once'}),
        ToolCall('yield', 'group_yield', {}),
    ]))
    agent = Agent(model)
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'alice': agent},
                                         worker_safe=True) as group:
        scope = await group.open_invocation('s')
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('alice', 'Contribute', origin_key='manual'),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        result = receipt(agent, 'post')
        assert result['accepted'] is True
        assert result.get('feedback', {}).get('available') is not True
        history = (await group.history(scope)).items
        assert len(history) == 1 and history[0].id == result['message_id']
        assert history[0].content == 'Published once'
        assert len(model.requests) == 1
        assert (await group.assignments(scope)).items[0].outcome == 'tool_stop'
        assert not group.store.failed


async def test_small_feedback_budget_preserves_base_json_instead_of_truncation(tmp_path, monkeypatch):
    from group import tools

    # Model budget estimates can leave little room for optional receipt data.
    monkeypatch.setattr(tools, '_retrieval_fit', lambda bound: lambda text: len(text) <= 350)
    model = ScriptedModel(AI(tool_calls=[
        ToolCall('post', 'group_post', {'content': 'Small output budget'}),
        ToolCall('yield', 'group_yield', {}),
    ]))
    agent = Agent(model)
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'alice': agent},
                                         worker_safe=True) as group:
        scope = await group.open_invocation('s')
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('alice', 'Contribute', origin_key='manual'),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        result = receipt(agent, 'post')
        raw = next(m.message for m in agent.session.history if m.role == 'tool' and m.tool_call_id == 'post')
        assert len(raw) <= 350
        assert result['accepted'] is True and result['opportunity_ids'] == []
        assert (await group.message(scope, result['message_id'])).content == 'Small output budget'


async def test_feedback_limits_ids_without_losing_counts(tmp_path):
    model = ScriptedModel(AI(tool_calls=[
        ToolCall('post', 'group_post', {'content': 'Interim thought'}),
        ToolCall('yield', 'group_yield', {}),
    ]))
    agent = Agent(model)
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'alice': agent}, worker_safe=True,
            profile=CollaborationProfile(require_public_reply=True),
            limits=GroupLimits(page_size=2)) as group:
        scope = await group.open_invocation('s')
        requests = [await group.request(scope, str(i), key=str(i), recipients=('alice',))
                    for i in range(7)]
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('alice', 'Consider all tasks',
                              tuple(r.opportunity_ids[0] for r in requests)),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        result = receipt(agent, 'post')['feedback']
        assert result['trigger_count'] == result['unlinked_trigger_count'] == 7
        assert result['linked_trigger_count'] == 0
        assert result['unlinked_trigger_ids'] == [r.message_id for r in requests[:2]]
        assert result['unlinked_trigger_ids_complete'] is False
        assert len(model.requests) == 1


async def test_feedback_never_counts_another_execution_reply(tmp_path):
    model = ScriptedModel()
    agent = Agent(model)
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'alice': agent},
            worker_safe=True, profile=CollaborationProfile(require_public_reply=True)) as group:
        scope = await group.open_invocation('s')
        request = await group.request(scope, 'A request', key='request', recipients=('alice',))
        model.responses.append(AI(tool_calls=[
            ToolCall('old', 'group_post', {'content': 'Earlier voluntary reply',
                                         'reply_to': request.message_id}),
            ToolCall('yield', 'group_yield', {}),
        ]))
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('alice', 'Volunteer', origin_key='earlier'),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        model.responses.append(AI(tool_calls=[
            ToolCall('current', 'group_post', {'content': 'Unlinked current contribution'}),
            ToolCall('yield-again', 'group_yield', {}),
        ]))
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('alice', 'Answer request', request.opportunity_ids),)))
        await group.launch_ready(scope)
        await group.wait_idle()
        result = receipt(agent, 'current')['feedback']
        assert result['unlinked_trigger_ids'] == [request.message_id]
        assert result['linked_trigger_count'] == 0
        assert not (await group.scheduling_view(scope)).evidence[0].satisfied


async def test_failed_diagnostic_store_preserves_receipt_but_stops_runtime(tmp_path, monkeypatch):
    import sqlite3

    from group import feedback
    from group.errors import StoreFailedError

    def failed_query(db, *args, **kwargs):
        return db.execute('SELECT missing FROM unavailable_feedback_table').fetchone()

    monkeypatch.setattr(feedback, 'publication', failed_query)
    model = ScriptedModel(AI(tool_calls=[
        ToolCall('post', 'group_post', {'content': 'Durably published before diagnostic failure'}),
        ToolCall('yield', 'group_yield', {}),
    ]))
    agent = Agent(model)
    path = tmp_path / 'g.sqlite'
    group = await GroupRuntime.create(path, {'alice': agent}, worker_safe=True)
    try:
        scope = await group.open_invocation('s')
        await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
            runs=(RunProposal('alice', 'Contribute', origin_key='manual'),)))
        with pytest.raises(StoreFailedError):
            await group.launch_ready(scope)
            await group.wait_idle()
        assert group.store.failed
        with pytest.raises(StoreFailedError):
            await group.request(scope, 'Must not be admitted', key='later')
    finally:
        with pytest.raises(StoreFailedError):
            await group.close()
    result = receipt(agent, 'post')
    assert result['accepted'] and result['feedback']['available'] is False
    assert len(model.requests) == 1
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute('SELECT id,content FROM messages').fetchall()
    assert rows == [(result['message_id'], 'Durably published before diagnostic failure')]
