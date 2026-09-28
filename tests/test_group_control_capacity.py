"""Control queries must not prevent cancellation, settlement, or runtime close."""

import asyncio
from threading import Event

import pytest

from core.agent import Agent
from group import CollaborationProfile, GroupLimits, GroupRuntime, OnDemandStrategy
from group.errors import QueueCapacityError
from models import AI
from tests.runtime_fakes import ScriptedModel


class BlockingModel(ScriptedModel):
    def __init__(self):
        super().__init__(AI('Completed result'))
        self.entered = Event()
        self.release = Event()

    def invoke(self, *args, **kwargs):
        self.entered.set()
        assert self.release.wait(5), 'Test must release the provider'
        return super().invoke(*args, **kwargs)


class PrivateStrategy(OnDemandStrategy):
    profile = CollaborationProfile(tools=())


@pytest.mark.parametrize('operation', ['timeout', 'settlement', 'close'])
async def test_historical_drivers_cannot_exhaust_lifecycle_capacity(tmp_path, monkeypatch, operation):
    model = BlockingModel()
    path = tmp_path / 'group.sqlite'
    group = await GroupRuntime.create(path, {'a': Agent(model)}, worker_safe=True,
        strategy=PrivateStrategy(), limits=GroupLimits(
            max_active=1, queue_jobs=1, invocation_timeout=0.3 if operation == 'timeout' else 5))
    db_entered, db_release = Event(), Event()
    observing, readers_started, lifecycle_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    tasks = []
    try:
        historical = []
        for index in range(5):
            scope = await group.open_invocation(f'history-{index}')
            await group.finish(scope, revision=(await group.snapshot(scope)).revision)
            historical.append(scope)
        scope = await group.open_invocation('current')
        await group.request(scope, 'Execute once', key='request')
        wait_for_change = group.wait_for_change

        async def observe(revision):
            observing.set()
            return await wait_for_change(revision)

        monkeypatch.setattr(group, 'wait_for_change', observe)
        driver = asyncio.create_task(group.drive(scope))
        tasks.append(driver)
        assert await asyncio.to_thread(model.entered.wait, 2)
        await asyncio.wait_for(observing.wait(), 2)

        def hold(db):
            db_entered.set()
            assert db_release.wait(5), 'Test must release SQLite'

        async def block_store():
            while True:
                try:
                    return await group.store.read(hold)
                except QueueCapacityError:
                    await asyncio.sleep(0)

        blocker = asyncio.create_task(block_store())
        tasks.append(blocker)
        assert await asyncio.to_thread(db_entered.wait, 2)
        read, write = group.store.read, group.store.write
        readers = 0

        async def observed_read(callback, *, control=False):
            nonlocal readers
            if control:
                readers += 1
                if readers >= 5:
                    readers_started.set()
                if group._closing is not None:
                    lifecycle_started.set()
            return await read(callback, control=control)

        async def observed_write(callback, *, control=False):
            if control:
                lifecycle_started.set()
            return await write(callback, control=control)

        monkeypatch.setattr(group.store, 'read', observed_read)
        monkeypatch.setattr(group.store, 'write', observed_write)
        tasks.extend(asyncio.create_task(group.drive(old)) for old in historical)
        await asyncio.wait_for(readers_started.wait(), 2)
        if operation == 'settlement':
            model.release.set()
        elif operation == 'close':
            closing = asyncio.create_task(group.close())
            tasks.append(closing)
        await asyncio.wait_for(lifecycle_started.wait(), 2)
        db_release.set()
        model.release.set()
        await blocker
        if operation == 'close':
            await asyncio.wait_for(closing, 2)
            async with await GroupRuntime.create(path, {'a': Agent(ScriptedModel())}, worker_safe=True) as reopened:
                snapshot = await reopened.snapshot(scope)
                assert snapshot.state == 'terminal' and snapshot.reason == 'cancelled'
                assert snapshot.active_count == 0
        else:
            result = await asyncio.wait_for(driver, 2)
            assert result.status == ('timeout' if operation == 'timeout' else 'waiting')
            snapshot = await group.snapshot(scope)
            assert snapshot.active_count == 0 and snapshot.admitted_count == 1
            assert (await group.assignments(scope)).items[0].state == 'settled'
            assert not group.store.failed
            if operation == 'timeout':
                assert snapshot.state == 'terminal' and snapshot.reason == 'timeout'
            else:
                assert snapshot.state == 'open'
                await group.finish(scope, revision=snapshot.revision)
            await group.close()
    finally:
        db_release.set()
        model.release.set()
        await asyncio.gather(group.close(), return_exceptions=True)
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize('operation', ['cancel', 'settlement'])
async def test_lifecycle_write_retries_rejection_before_admission(tmp_path, monkeypatch, operation):
    model = BlockingModel()
    group = await GroupRuntime.create(tmp_path / 'group.sqlite', {'a': Agent(model)}, worker_safe=True,
                                      strategy=PrivateStrategy())
    rejected = asyncio.Event()
    tasks = []
    try:
        scope = await group.open_invocation('s')
        await group.request(scope, 'Execute once', key='request')
        driver = asyncio.create_task(group.drive(scope))
        tasks.append(driver)
        assert await asyncio.to_thread(model.entered.wait, 2)
        # Wait for launch bookkeeping to finish before injecting admission failure.
        await group.launch_ready(scope)
        write = group.store.write

        async def reject_once(callback, *, control=False):
            if control and not rejected.is_set():
                rejected.set()
                raise QueueCapacityError('Injected rejection before store admission')
            return await write(callback, control=control)

        monkeypatch.setattr(group.store, 'write', reject_once)
        if operation == 'cancel':
            cancelling = asyncio.create_task(group.cancel(scope))
            tasks.append(cancelling)
        else:
            model.release.set()
        await asyncio.wait_for(rejected.wait(), 2)
        model.release.set()
        if operation == 'cancel':
            await asyncio.wait_for(cancelling, 2)
        result = await asyncio.wait_for(driver, 2)
        assert result.status == ('cancelled' if operation == 'cancel' else 'waiting')
        snapshot = await group.snapshot(scope)
        assert snapshot.active_count == 0 and snapshot.admitted_count == 1
        assignment = (await group.assignments(scope)).items[0]
        assert assignment.state == 'settled' and assignment.error is None
        assert len(model.requests) == 1
        await group.close()
    finally:
        model.release.set()
        await asyncio.gather(group.close(), return_exceptions=True)
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_cancelled_waiter_preserves_cancellation_while_control_write_retries(tmp_path, monkeypatch):
    model = BlockingModel()
    group = await GroupRuntime.create(tmp_path / 'group.sqlite', {'a': Agent(model)}, worker_safe=True,
                                      strategy=PrivateStrategy())
    rejected, available = asyncio.Event(), asyncio.Event()
    tasks = []
    try:
        scope = await group.open_invocation('s')
        await group.request(scope, 'Execute once', key='request')
        driver = asyncio.create_task(group.drive(scope))
        tasks.append(driver)
        assert await asyncio.to_thread(model.entered.wait, 2)
        await group.launch_ready(scope)
        write = group.store.write

        async def congested(callback, *, control=False):
            if control and not available.is_set():
                rejected.set()
                raise QueueCapacityError('Injected rejection before store admission')
            return await write(callback, control=control)

        monkeypatch.setattr(group.store, 'write', congested)
        first = asyncio.create_task(group.cancel(scope))
        tasks.append(first)
        await asyncio.wait_for(rejected.wait(), 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        available.set()
        model.release.set()
        await asyncio.wait_for(group.cancel(scope), 2)
        assert (await asyncio.wait_for(driver, 2)).status == 'cancelled'
        snapshot = await group.snapshot(scope)
        assert snapshot.state == 'terminal' and snapshot.reason == 'cancelled'
        assert snapshot.active_count == 0 and snapshot.admitted_count == 1
        assert len(model.requests) == 1
        await group.close()
    finally:
        available.set()
        model.release.set()
        await asyncio.gather(group.close(), return_exceptions=True)
        await asyncio.gather(*tasks, return_exceptions=True)
