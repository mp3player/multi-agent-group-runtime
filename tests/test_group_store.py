"""Storage ownership and cancellation must preserve accepted transactions."""

import asyncio
import threading

import pytest


async def test_transaction_rolls_back_and_reopens_durably(tmp_path):
    from group.store import GroupStore

    path = tmp_path / 'group.sqlite'
    store = await GroupStore.open(path)
    await store.write(lambda db: db.execute('CREATE TABLE example(value TEXT)'))
    await store.write(lambda db: db.execute("INSERT INTO example VALUES ('kept')"))

    def broken(db):
        db.execute("INSERT INTO example VALUES ('discarded')")
        raise ValueError('reject whole transaction')

    with pytest.raises(ValueError):
        await store.write(broken)
    await store.close()
    store = await GroupStore.open(path)
    try:
        assert await store.read(lambda db: db.execute('SELECT value FROM example').fetchall()) == [('kept',)]
        assert await store.read(lambda db: db.execute('PRAGMA synchronous').fetchone()[0]) == 2
    finally:
        await store.close()


async def test_cancelled_waiter_does_not_erase_commit_and_control_has_capacity(tmp_path):
    from group.errors import CapacityError
    from group.store import GroupStore

    store = await GroupStore.open(tmp_path / 'group.sqlite', queue_limit=1, control_limit=1)
    entered, release = threading.Event(), threading.Event()
    await store.write(lambda db: db.execute('CREATE TABLE example(value TEXT)'))

    def slow(db):
        entered.set()
        release.wait(5)
        db.execute("INSERT INTO example VALUES ('committed')")

    task = asyncio.create_task(store.write(slow))
    await asyncio.to_thread(entered.wait, 2)
    assert entered.is_set()
    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(CapacityError):
            await store.read(lambda db: None)
        control = asyncio.create_task(store.read(lambda db: db.execute('SELECT value FROM example').fetchall(), control=True))
        await asyncio.sleep(0)
        release.set()
        assert await control == [('committed',)]
    finally:
        release.set()
        await store.close()


async def test_second_owner_is_rejected_and_close_can_be_waited_again(tmp_path):
    from group.errors import StoreBusyError
    from group.store import GroupStore

    path = tmp_path / 'group.sqlite'
    first = await GroupStore.open(path)
    try:
        with pytest.raises(StoreBusyError):
            await GroupStore.open(path)
    finally:
        await first.close()
        await first.close()
    second = await GroupStore.open(path)
    await second.close()


async def test_control_reads_cannot_consume_reserved_write_capacity(tmp_path):
    from group.errors import QueueCapacityError
    from group.store import GroupStore

    store = await GroupStore.open(tmp_path / 'group.sqlite', queue_limit=1, control_limit=1)
    entered, release = threading.Event(), threading.Event()
    await store.write(lambda db: db.execute('CREATE TABLE example(value TEXT)'))

    def hold(db):
        entered.set()
        assert release.wait(5)

    blocker = asyncio.create_task(store.read(hold))
    tasks = [blocker]
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        reading = asyncio.create_task(store.read(lambda db: None, control=True))
        tasks.append(reading)
        await asyncio.sleep(0)
        # Cancelling a waiter must not release an accepted transaction's slot.
        reading.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reading
        with pytest.raises(QueueCapacityError):
            await store.read(lambda db: None, control=True)
        with pytest.raises(QueueCapacityError):
            await store.read(lambda db: None)
        writing = asyncio.create_task(store.write(
            lambda db: db.execute("INSERT INTO example VALUES ('settled')").rowcount, control=True))
        tasks.append(writing)
        await asyncio.sleep(0)
        with pytest.raises(QueueCapacityError):
            await store.write(lambda db: None, control=True)
        release.set()
        await blocker
        assert await writing == 1
        assert await store.read(lambda db: db.execute('SELECT value FROM example').fetchall()) == [('settled',)]
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await store.close()
