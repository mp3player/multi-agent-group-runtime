"""Opt-in finite endurance of shared sessions, real tools, workers, and SQLite.

Set MAS_RUN_GROUP_ENDURANCE_TESTS=1 to run all forty discussions.
"""

import asyncio
from collections import Counter
import json
import os
from threading import Event, Lock
import time

import pytest

from core.agent import Agent
from core.agent_runtime.options import AgentOptions
from core.agent_runtime.run_state import AgentBusyError
from core.context_archive import FileContextArchive
from group import GroupLimits, GroupRuntime, OnDemandStrategy
from group.errors import LifecycleError
from models import AI, ToolCall
from tests.runtime_fakes import ScriptedModel


pytestmark = pytest.mark.skipif(
    os.environ.get('MAS_RUN_GROUP_ENDURANCE_TESTS') != '1',
    reason='Set MAS_RUN_GROUP_ENDURANCE_TESTS=1 to run the finite endurance regression',
)


class ProviderActivity:
    def __init__(self):
        self.lock = Lock()
        self.active = 0
        self.peak = 0
        self.member_active = Counter()
        self.member_peak = Counter()

    def enter(self, member):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.member_active[member] += 1
            self.member_peak[member] = max(self.member_peak[member], self.member_active[member])

    def leave(self, member):
        with self.lock:
            self.active -= 1
            self.member_active[member] -= 1


class EnduranceModel(ScriptedModel):
    """Script only the provider boundary; all collaboration uses real tools."""

    def __init__(self, member, activity):
        super().__init__()
        self.member = member
        self.activity = activity
        self.business_calls = []
        self.summary_calls = 0
        self.entered = Event()
        self.release = Event()

    def invoke(self, messages, **kwargs):
        self.activity.enter(self.member)
        try:
            if messages[0].message.startswith('CONTEXT_COMPACTION'):
                self.summary_calls += 1
                response = AI('Previous discussion records are historical context. '
                              'Only the current assignment contains work to execute.')
            else:
                prompt = next(m.message for m in reversed(messages) if m.role == 'user')
                payload = json.loads(prompt.rsplit('\n', 1)[1])
                assert payload['member_id'] == self.member
                assert len(payload['trigger_messages']) == 1
                trigger = payload['trigger_messages'][0]
                stage = trigger['content']
                self.business_calls.append((payload['invocation_id'], stage, trigger['message_id']))
                if stage == 'fail':
                    raise RuntimeError('injected endurance provider failure')
                if stage == 'cancel':
                    self.entered.set()
                    if not self.release.wait(15):
                        raise TimeoutError('Test did not release the blocked provider')
                calls = [ToolCall('reply', 'group_post', {
                    'content': f'{self.member} answered {stage}',
                    'reply_to': trigger['message_id'],
                })]
                if stage == 'start':
                    assert self.member == 'alice'
                    calls.append(ToolCall('handoff', 'group_request', {
                        'content': 'delegate', 'recipients': ['bob'],
                    }))
                elif stage == 'delegate':
                    assert self.member == 'bob'
                    calls.append(ToolCall('broadcast', 'group_broadcast', {'content': 'review'}))
                elif stage == 'review' and self.member == 'carol':
                    calls.append(ToolCall('handback', 'group_request', {
                        'content': 'merge', 'recipients': ['bob'],
                    }))
                else:
                    assert stage in ('review', 'merge', 'cancel')
                calls.append(ToolCall('yield', 'group_yield', {}))
                response = AI(tool_calls=calls)
            self.responses.append(response)
            result = super().invoke(messages, **kwargs)
            result['choices'][0]['finish_reason'] = 'tool_calls' if response.tool_calls else 'stop'
            return result
        finally:
            self.activity.leave(self.member)


async def wait_snapshot(group, scope, check):
    async def observe():
        while True:
            snapshot = await group.snapshot(scope)
            if check(snapshot):
                return snapshot
            await group.wait_for_change(snapshot.revision)
    return await asyncio.wait_for(observe(), 10)


async def assert_drained(group, scope, agents):
    await group.wait_idle()
    await group.receive(scope)
    snapshot = await group.snapshot(scope)
    assert snapshot.pending_count == snapshot.queued_count == snapshot.active_count == 0
    assert snapshot.blocked_count == 0
    assert all(member.state == 'idle' for member in snapshot.members)
    assert not group._runs
    for member, agent in agents.items():
        assert not agent.run_state.active
        assert (await group.reception(scope, member)).pending_count == 0
    return snapshot


async def test_forty_discussions_reuse_sessions_across_handoffs_failures_and_cancellation(
        tmp_path, record_property):
    activity = ProviderActivity()
    models = {name: EnduranceModel(name, activity) for name in ('alice', 'bob', 'carol')}
    agents = {
        name: Agent(model, options=AgentOptions(max_tokens=512),
                    archive_store=FileContextArchive(tmp_path / name))
        for name, model in models.items()
    }
    sessions = {name: agent.session for name, agent in agents.items()}
    registries = {name: set(agent.registry.names()) for name, agent in agents.items()}
    limits = GroupLimits(max_active=2, max_queued=2, max_pending=4, max_runs=5,
                         page_size=2, invocation_timeout=30)
    group = await GroupRuntime.create(tmp_path / 'endurance.sqlite', agents,
        worker_safe=True, strategy=OnDemandStrategy(), limits=limits)
    tasks = []
    admitted = 0
    outcomes = Counter()
    discussion_seconds = []
    try:
        for index in range(40):
            started = time.monotonic()
            scope = await group.open_invocation(f'discussion-{index}')
            mode = 'fail' if index % 10 == 2 else 'cancel' if index % 10 == 5 else 'start'
            before = {name: len(model.business_calls) for name, model in models.items()}
            await group.post(scope, '@alice @bob @carol please answer this passive note',
                             key='passive', recipients=tuple(agents))
            quiet = await group.drive(scope)
            assert quiet.status == 'waiting' and quiet.admitted_count == 0
            assert before == {name: len(model.business_calls) for name, model in models.items()}
            for model in models.values():
                model.entered.clear()
                model.release.clear()

            request = await group.request(scope, mode, key='request')
            assert await group.request(scope, mode, key='request') == request
            if mode == 'cancel':
                driver = asyncio.create_task(group.drive(scope))
                tasks.append(driver)
                assert await asyncio.to_thread(models['alice'].entered.wait, 10)
                await group.request(scope, 'cancel', key='second', recipients=('bob',))
                assert await asyncio.to_thread(models['bob'].entered.wait, 10)
                await group.request(scope, 'must never run', key='queued', recipients=('carol',))
                await group.request(scope, 'must never run', key='pending', recipients=('alice',))
                busy = await wait_snapshot(group, scope, lambda s:
                    s.active_count == 2 and s.queued_count == 1 and s.pending_count == 1)
                assert busy.admitted_count == 3
                cancellation = asyncio.create_task(group.cancel(scope))
                tasks.append(cancellation)
                closing = await wait_snapshot(group, scope, lambda s: s.state == 'closing')
                assert closing.active_count == 2 and not cancellation.done()
                for member in ('alice', 'bob'):
                    with pytest.raises(AgentBusyError):
                        agents[member].system_prompt = 'Cannot release a blocked worker early'
                with pytest.raises(LifecycleError):
                    await group.open_invocation(f'premature-{index}')
                for model in models.values():
                    model.release.set()
                await asyncio.wait_for(cancellation, 10)
                assert (await asyncio.wait_for(driver, 10)).status == 'cancelled'
                runs = (await group.assignments(scope, limit=10)).items
                assert len(runs) == 3 and all(run.outcome == 'cancelled' for run in runs)
                assert all(m.sender == 'user' for m in (await group.history(scope, limit=20)).items)
                assert {name: len(model.business_calls) - before[name] for name, model in models.items()} == {
                    'alice': 1, 'bob': 1, 'carol': 0,
                }
            else:
                # Let the configured invocation deadline decide runtime timeouts.
                result = await asyncio.wait_for(group.drive(scope), limits.invocation_timeout + 5)
                if mode == 'fail':
                    assert result.status == 'needs_input' and result.admitted_count == 1
                    assert result.missing_reply_ids == request.opportunity_ids
                    run = (await group.assignments(scope)).items[0]
                    assert run.outcome == 'error' and 'injected endurance provider failure' in run.error
                    retry = await group.drive(scope)
                    assert retry.status == 'needs_input' and retry.admitted_count == 1
                    assert {name: len(model.business_calls) - before[name] for name, model in models.items()} == {
                        'alice': 1, 'bob': 0, 'carol': 0,
                    }
                    await group.cancel(scope)
                else:
                    assert result.status == 'waiting' and result.admitted_count == limits.max_runs
                    assert not result.missing_reply_ids
                    observed = Counter((name, call[1]) for name, model in models.items()
                                       for call in model.business_calls[before[name]:])
                    assert observed == Counter({('alice', 'start'): 1, ('bob', 'delegate'): 1,
                                                ('alice', 'review'): 1, ('carol', 'review'): 1,
                                                ('bob', 'merge'): 1})
                    view = await group.scheduling_view(scope)
                    assert len(view.evidence) == 5 and all(item.satisfied for item in view.evidence)
                    assert all(run.state == 'settled' and run.outcome == 'tool_stop' and not run.error
                               for run in view.runs)
                    counts = {name: len(model.business_calls) for name, model in models.items()}
                    await group.post(scope, 'Please answer again: passive follow-up only',
                                     key='tail', recipients=tuple(agents), reply_to=request.message_id)
                    assert (await group.drive(scope)).admitted_count == 5
                    assert counts == {name: len(model.business_calls) for name, model in models.items()}
                    await group.finish(scope, revision=(await group.snapshot(scope)).revision)

            snapshot = await assert_drained(group, scope, agents)
            assert snapshot.state == 'terminal'
            assert snapshot.reason == ('completed' if mode == 'start' else 'cancelled')
            assert all(agent.session is sessions[name] for name, agent in agents.items())
            admitted += snapshot.admitted_count
            outcomes[mode] += 1
            discussion_seconds.append(round(time.monotonic() - started, 6))

        assert outcomes == {'start': 32, 'fail': 4, 'cancel': 4}
        assert admitted == 176  # Includes four queued assignments cancelled before inference.
        assert sum(len(model.business_calls) for model in models.values()) == 172
        assert activity.peak == limits.max_active and activity.active == 0
        assert all(peak == 1 for peak in activity.member_peak.values())
        assert sum(model.summary_calls for model in models.values()) > 0
        assert all(agent.runtime.context_manager.compactions > 0 for agent in agents.values())
        record_property('endurance', json.dumps({
            'discussions': dict(outcomes), 'admitted_assignments': admitted,
            'business_calls': {name: len(model.business_calls) for name, model in models.items()},
            'summary_calls': {name: model.summary_calls for name, model in models.items()},
            'compactions': {name: agent.runtime.context_manager.compactions for name, agent in agents.items()},
            'peak_provider_calls': activity.peak,
            'discussion_seconds': discussion_seconds,
        }, sort_keys=True))
    finally:
        for model in models.values():
            model.release.set()
        await asyncio.wait_for(group.close(), 15)
        await asyncio.gather(*tasks, return_exceptions=True)

    assert not group._runs and not group._operations
    assert not group._drive_tasks and not group._receive_tasks and not group._services
    assert not group._launch_tasks and not group._cancel_tasks and not group._finish_tasks
    assert not group.store._pending and group.store._counts == [0, 0, 0]
    assert group.store._db is None and group.store._lock_file is None
    assert activity.active == 0 and not any(activity.member_active.values())
    for worker in group.workers.values():
        assert worker._closed and worker._active.settled
        assert all(not thread.is_alive() for thread in worker._executor._threads)
    for thread in group.store._executor._threads:
        await asyncio.to_thread(thread.join, 1)
        assert not thread.is_alive()
    for name, agent in agents.items():
        assert set(agent.registry.names()) == registries[name]
        assert not agent.run_state.active
        agent.system_prompt = 'Reusable after all forty discussions and Group close'
