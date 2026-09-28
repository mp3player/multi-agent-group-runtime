"""Bounded factual status reads use the real Group, worker, tools and store."""

import asyncio
from threading import Event
from types import SimpleNamespace

import pytest

from cli.group import Console
from core.agent import Agent
from core.agent_runtime.options import AgentOptions
from group import CollaborationProfile, DispatchPlan, GroupRuntime, OnDemandStrategy, RunProposal
from group.diagnostics import status as observe
from group.errors import GroupError
from models import AI, ToolCall
from tests.runtime_fakes import ScriptedModel


def reply(*message_ids, yield_run=True):
    calls = [ToolCall(f'post-{index}', 'group_post',
                     {'content': 'Public answer', 'reply_to': message_id})
             for index, message_id in enumerate(message_ids)]
    if yield_run:
        calls.append(ToolCall('yield', 'group_yield', {}))
    return AI(tool_calls=calls)


async def admit(group, scope, *receipts, member='alice', origin=None):
    return (await group.commit_plan(DispatchPlan(scope, (await group.snapshot(scope)).revision,
        runs=(RunProposal(member, 'Process assigned work',
                          tuple(oid for receipt in receipts for oid in receipt.opportunity_ids),
                          origin_key=origin),))))[0]


async def test_cli_identifies_settled_missing_response_even_with_pending_zero(tmp_path, capsys):
    model = ScriptedModel(AI('Private answer'))
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'alice': Agent(model)},
            worker_safe=True, strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        request = await group.request(scope, 'Respond publicly', key='request')
        await group.drive(scope)
        assignment = (await group.assignments(scope)).items[0]
        await Console(SimpleNamespace(runtime=group, scope=scope)).handle('/status')
        output = capsys.readouterr().out
        assert 'pending=0' in output
        assert request.message_id in output and request.opportunity_ids[0] in output
        assert assignment.id in output and 'alice' in output and 'settled' in output
        assert 'missing' in output and 'unresolved=1' in output
        assert len(model.requests) == 1


async def test_multi_trigger_evidence_and_elsewhere_publication_remain_distinct(tmp_path, capsys):
    model = ScriptedModel()
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'alice': Agent(model)},
            worker_safe=True, strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        background = await group.post(scope, 'Background', key='background')
        first = await group.request(scope, 'First', key='first')
        second = await group.request(scope, 'Second', key='second')
        assignment = await admit(group, scope, first, second)
        model.responses.append(reply(first.message_id, background.message_id))
        await group.launch_ready(scope)
        await group.wait_idle()
        observed = await observe(group, scope)
        rows = {row.message_id: row for row in observed.details}
        assert observed.snapshot.pending_count == 0 and observed.missing_reply_count == 1
        assert observed.detail_count == 2 and observed.omitted_count == 0
        assert rows[first.message_id].assignment_id == rows[second.message_id].assignment_id == assignment
        assert rows[first.message_id].evidence.satisfied
        missing = rows[second.message_id]
        assert missing.evidence.reply_id is None and not missing.evidence.satisfied
        assert missing.other_publication_id is not None
        publication = await group.message(scope, missing.other_publication_id)
        assert publication.run_id == assignment and publication.reply_to == missing.other_reply_to
        assert publication.reply_to != second.message_id
        authoritative = (await group.scheduling_view(scope)).evidence
        assert {row.opportunity_id: row for row in authoritative} == {
            row.opportunity_id: row.evidence for row in observed.details}
        await Console(SimpleNamespace(runtime=group, scope=scope)).handle('/status')
        output = capsys.readouterr().out
        assert 'other_publication_sample=' in output and 'other_reply_to=' in output
        assert 'wrong' not in output and 'incorrect' not in output
        assert len(model.requests) == 1


async def test_pending_and_manual_no_trigger_rows_do_not_invent_missing_replies(tmp_path):
    model = ScriptedModel(AI('Manual private answer'))
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'alice': Agent(model)},
            worker_safe=True, strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        request = await group.request(scope, 'Pending', key='request', recipients=('alice',))
        assignment = await admit(group, scope, origin='manual')
        queued = await observe(group, scope)
        assert queued.snapshot.pending_count == 1 and queued.snapshot.queued_count == 1
        assert queued.missing_reply_count == 0
        pending = next(row for row in queued.details if row.opportunity_id)
        manual = next(row for row in queued.details if row.opportunity_id is None)
        assert pending.message_id == request.message_id and pending.member_id == 'alice'
        assert pending.assignment_id is None and pending.opportunity_state == 'pending'
        assert pending.evidence is None
        assert manual.assignment_id == assignment and manual.assignment_state == 'queued'
        assert manual.message_id is None and manual.evidence is None
        await group.launch_ready(scope)
        await group.wait_idle()
        settled = await observe(group, scope)
        manual = next(row for row in settled.details if row.opportunity_id is None)
        assert manual.assignment_state == 'settled' and manual.evidence is None
        assert settled.missing_reply_count == 0 and len(model.requests) == 1


async def test_profile_without_reply_requirement_reports_private_execution_without_obligation(tmp_path, capsys):
    model = ScriptedModel(AI('Private result'))
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'alice': Agent(model)},
            worker_safe=True, profile=CollaborationProfile(require_public_reply=False)) as group:
        scope = await group.open_invocation('s')
        request = await group.request(scope, 'Answer privately', key='request')
        await admit(group, scope, request)
        await group.launch_ready(scope)
        await group.wait_idle()
        result = await observe(group, scope)
        assert result.require_public_reply is False and result.missing_reply_count == 0
        assert result.details[0].evidence.reply_id is None
        await Console(SimpleNamespace(runtime=group, scope=scope)).handle('/status')
        output = capsys.readouterr().out
        assert 'not-required' in output and 'missing' not in output and 'unresolved=0' in output


async def test_blocked_preparation_is_attributed_without_inference_or_settled_evidence(tmp_path, capsys):
    model = ScriptedModel()
    agent = Agent(model, options=AgentOptions(context_window=256, max_tokens=64))
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'alice': agent},
            worker_safe=True, strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        request = await group.request(scope, 'x' * 3000, key='request')
        assignment = await admit(group, scope, request)
        await group.launch_ready(scope)
        await group.wait_idle()
        result = await observe(group, scope)
        row = result.details[0]
        assert row.assignment_id == assignment and row.assignment_state == 'blocked'
        assert row.error == (await group.assignments(scope)).items[0].error and row.evidence is None
        assert result.snapshot.blocked_count == result.error_count == 1
        assert result.missing_reply_count == 0 and not model.requests
        await Console(SimpleNamespace(runtime=group, scope=scope)).handle('/status')
        output = capsys.readouterr().out
        assert request.message_id in output and 'blocked' in output and 'Model input budget' in output


async def test_linked_publication_is_provisional_then_unsatisfied_if_run_fails(tmp_path):
    entered, release = Event(), Event()

    class FailingModel(ScriptedModel):
        def invoke(self, *args, **kwargs):
            if self.requests:
                entered.set()
                assert release.wait(5)
                raise RuntimeError('Provider stopped after publication')
            return super().invoke(*args, **kwargs)

    model = FailingModel()
    group = await GroupRuntime.create(tmp_path / 'g.sqlite', {'alice': Agent(model)},
                                     worker_safe=True, strategy=OnDemandStrategy())
    try:
        scope = await group.open_invocation('s')
        request = await group.request(scope, 'Answer', key='request')
        model.responses.append(reply(request.message_id, yield_run=False))
        await admit(group, scope, request)
        await group.launch_ready(scope)
        assert await asyncio.to_thread(entered.wait, 2)
        live = await observe(group, scope)
        row = live.details[0]
        assert row.assignment_state == 'running' and row.reply_id is not None
        assert row.evidence is None and live.missing_reply_count == 0
        release.set()
        await group.wait_idle()
        failed = await observe(group, scope)
        row = failed.details[0]
        assert row.assignment_state == 'settled' and row.outcome == 'error'
        assert row.reply_id is not None and not row.evidence.satisfied
        assert failed.error_count == failed.missing_reply_count == 1
    finally:
        release.set()
        await group.close()


@pytest.mark.parametrize('first_failed', [False, True])
async def test_later_reply_only_satisfies_original_after_explicit_resolution(tmp_path, first_failed):
    model = ScriptedModel() if first_failed else ScriptedModel(AI('Private result'))
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'alice': Agent(model)},
            worker_safe=True, strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        request = await group.request(scope, 'Answer', key='request')
        await group.drive(scope)
        original = (await group.assignments(scope)).items[0]
        repair = await admit(group, scope, origin='repair')
        model.responses.append(reply(request.message_id))
        await group.launch_ready(scope)
        await group.wait_idle()
        before = await observe(group, scope)
        assert before.missing_reply_count == 1
        row = next(row for row in before.details if row.opportunity_id)
        assert row.reply_id is None and not row.evidence.satisfied
        publication = next(m for m in (await group.history(scope)).items if m.run_id == repair)
        await group.resolve_response(scope, request.opportunity_ids[0], publication.id, key='resolve')
        after = await observe(group, scope)
        row = next(row for row in after.details if row.opportunity_id)
        assert after.missing_reply_count == 0 and row.evidence.satisfied
        assert row.evidence.assignment_id == original.id and row.evidence.outcome == original.outcome
        assert row.evidence.error == original.error
        assert row.evidence == (await group.scheduling_view(scope)).evidence[0]
        assert row.evidence.reply_id is None and row.evidence.resolved_by == repair
        assert row.evidence.resolution_reply_id == publication.id


async def test_status_is_one_read_only_observation_with_bounded_details_and_safe_cli_text(
        tmp_path, monkeypatch, capsys):
    member = 'alice\u202e' + 'x' * 100

    class FailingModel(ScriptedModel):
        def invoke(self, messages, **kwargs):
            self.requests.append(messages)
            raise RuntimeError('bad\n[forged]\x1b[31m\r\t\x7f\u202e' + 'X' * 1500)

    model = FailingModel()
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {member: Agent(model)},
            worker_safe=True, strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        requests = [await group.request(scope, 'Request', key=f'request-{index}') for index in range(28)]
        await admit(group, scope, *requests, member=member)
        await group.launch_ready(scope)
        await group.wait_idle()
        before = await group.store.read(lambda db: tuple(db.iterdump()))
        read = group.store.read
        reads = 0

        async def counted_read(*args, **kwargs):
            nonlocal reads
            reads += 1
            return await read(*args, **kwargs)

        monkeypatch.setattr(group.store, 'read', counted_read)
        result = await observe(group, scope, limit=3)
        assert reads == 1
        assert len(result.details) == 3 and result.detail_count == 28 and result.omitted_count == 25
        assert result.missing_reply_count == 28 and result.error_count == 1
        assert all(len(row.error) <= 1024 for row in result.details)
        assert result.snapshot.revision == (await group.snapshot(scope)).revision
        await Console(SimpleNamespace(runtime=group, scope=scope)).handle('/status')
        output = capsys.readouterr().out
        assert 'omitted=8' in output and 'unresolved=28' in output
        assert 'truncated' in output and '\\n[forged]\\u001b' in output
        assert not any(ord(char) < 32 and char != '\n' or ord(char) in (127, 0x202e) for char in output)
        assert len(output.splitlines()) <= 24 and len(output) < 24000
        assert len(model.requests) == 1
        assert await group.store.read(lambda db: tuple(db.iterdump())) == before
        for invalid in (0, -1, True, 101, '3'):
            with pytest.raises(GroupError):
                await observe(group, scope, limit=invalid)


async def test_status_prioritizes_recent_unresolved_request_over_old_successes(tmp_path):
    model = ScriptedModel()
    async with await GroupRuntime.create(tmp_path / 'g.sqlite', {'alice': Agent(model)},
            worker_safe=True, strategy=OnDemandStrategy()) as group:
        scope = await group.open_invocation('s')
        first = await group.request(scope, 'First', key='first')
        model.responses.append(reply(first.message_id))
        await group.drive(scope)
        second = await group.request(scope, 'Second', key='second')
        model.responses.append(AI('Private result'))
        await group.drive(scope)
        observed = await observe(group, scope, limit=1)
        assert observed.details[0].message_id == second.message_id
        assert observed.missing_reply_count == 1 and observed.omitted_count == 1
        assert observed.detail_count == 2 and not observed.details[0].evidence.satisfied
