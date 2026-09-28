"""Owned idle scheduling survives rejected reads without abandoning its deadline."""

import asyncio
from contextlib import asynccontextmanager
import sqlite3
from threading import Event

import pytest

from core.agent import Agent
from group import CollaborationProfile, GroupLimits, GroupRuntime, OnDemandStrategy
from group import state as sql
from group.errors import QueueCapacityError, StoreFailedError
from models import AI
from tests.runtime_fakes import ScriptedModel


class PrivateStrategy(OnDemandStrategy):
    profile = CollaborationProfile(tools=())


async def admit_application_call(operation):
    """Application calls still handle rejection before admission under load."""
    while True:
        try:
            return await operation()
        except QueueCapacityError:
            await asyncio.sleep(0.005)


@asynccontextmanager
async def pressured_service(tmp_path, monkeypatch, *, control=False, timeout=0.5):
    """Fill a real SQLite admission lane at the service's first idle revision read."""
    model = ScriptedModel(AI('Processed once'))
    group = await GroupRuntime.create(tmp_path / 'group.sqlite', {'a': Agent(model)},
        worker_safe=True, strategy=PrivateStrategy(),
        limits=GroupLimits(queue_jobs=1, invocation_timeout=timeout))
    reached, proceed, attempted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    held, release = Event(), Event()
    original_wait = group.wait_for_change
    blocker = None

    async def gated(revision):
        reached.set()
        await proceed.wait()
        return await original_wait(revision)

    monkeypatch.setattr(group, 'wait_for_change', gated)
    try:
        scope = await group.open_invocation('s')
        service = await group.start_service(scope)
        await asyncio.wait_for(reached.wait(), 2)
        read = group.store.read

        async def observed_read(operation, *, control=False):
            if operation is sql.revision:
                attempted.set()
            return await read(operation, control=control)

        monkeypatch.setattr(group.store, 'read', observed_read)
        def hold(db):
            held.set()
            assert release.wait(5), 'Test must release the database worker'
        blocker = asyncio.create_task(group.store.read(hold, control=control))
        assert await asyncio.to_thread(held.wait, 2)
        proceed.set()
        await asyncio.wait_for(attempted.wait(), 2)
        yield group, scope, service, model, release, blocker
    finally:
        release.set()
        proceed.set()
        if blocker:
            await asyncio.gather(blocker, return_exceptions=True)
        try:
            await group.close()
        except StoreFailedError:
            if not group.store.failed:
                raise


@pytest.mark.parametrize('control', [False, True])
async def test_idle_service_keeps_original_deadline_after_read_pressure(tmp_path, monkeypatch, control):
    async with pressured_service(tmp_path, monkeypatch, control=control) as setup:
        group, scope, service, model, release, blocker = setup
        release.set()
        await blocker
        deadline = (await group.snapshot(scope)).deadline_at
        result = await asyncio.wait_for(service.wait(), 2)
        assert result.status == 'timeout'
        snapshot = await group.snapshot(scope)
        assert snapshot.state == 'terminal' and snapshot.reason == 'timeout'
        assert snapshot.deadline_at == deadline
        assert model.requests == []


async def test_idle_service_processes_new_request_after_normal_queue_pressure(tmp_path, monkeypatch):
    async with pressured_service(tmp_path, monkeypatch, timeout=3) as setup:
        group, scope, service, model, release, blocker = setup
        release.set()
        await blocker
        await asyncio.wait_for(admit_application_call(lambda: group.request(
            scope, 'Process this request once', key='r', recipients=('a',))), 2)
        async def settled():
            while True:
                snapshot = await admit_application_call(lambda: group.snapshot(scope))
                if snapshot.admitted_count == 1 and not snapshot.active_count and not snapshot.queued_count:
                    return snapshot
                await asyncio.sleep(0.005)
        snapshot = await asyncio.wait_for(settled(), 2)
        assert snapshot.assignments.items[0].outcome == 'completed'
        assert len(model.requests) == 1
        await asyncio.wait_for(admit_application_call(lambda: group.cancel(scope)), 2)
        assert (await asyncio.wait_for(service.wait(), 2)).status == 'cancelled'


async def test_deadline_cancels_admission_retries_while_control_read_stays_full(tmp_path, monkeypatch):
    async with pressured_service(tmp_path, monkeypatch, control=True) as setup:
        group, scope, service, model, release, blocker = setup
        timeout_started = asyncio.Event()
        cancel = group._cancel
        async def observe_cancel(scope, reason='cancelled'):
            if reason == 'timeout':
                timeout_started.set()
            return await cancel(scope, reason)
        monkeypatch.setattr(group, '_cancel', observe_cancel)
        # The database is still occupied. Its terminal write must wait, but the
        # service must begin deadline cancellation without waiting for a read slot.
        await asyncio.wait_for(timeout_started.wait(), 1.5)
        release.set()
        await blocker
        assert (await asyncio.wait_for(service.wait(), 2)).status == 'timeout'
        assert model.requests == []


async def test_runtime_close_stops_idle_read_retries_and_joins_accepted_io(tmp_path, monkeypatch):
    async with pressured_service(tmp_path, monkeypatch, control=True, timeout=3) as setup:
        group, scope, service, model, release, blocker = setup
        closing = asyncio.create_task(group.close())
        await asyncio.sleep(0)
        release.set()
        await blocker
        await asyncio.wait_for(closing, 2)
        with pytest.raises(asyncio.CancelledError):
            await service.wait()
        assert model.requests == []


async def test_real_store_failure_is_not_retried_as_read_pressure(tmp_path, monkeypatch):
    async with pressured_service(tmp_path, monkeypatch, timeout=3) as setup:
        group, scope, service, model, release, blocker = setup
        # The failing control write runs after the held read, before any admitted
        # revision read can resume. It represents real persistence failure.
        started = asyncio.Event()
        async def fail_store():
            started.set()
            return await group.store.write(lambda db: db.execute('SELECT missing FROM missing'), control=True)
        failure = asyncio.create_task(fail_store())
        await started.wait()
        release.set()
        await blocker
        with pytest.raises(sqlite3.DatabaseError):
            await failure
        with pytest.raises(StoreFailedError):
            await asyncio.wait_for(service.wait(), 2)
        assert group.store.failed and model.requests == []
