"""Cancellation at the stdin syscall boundary must not break reader callbacks."""

import asyncio
import os

import pytest

import cli.group as cli


@pytest.mark.parametrize('read_error', [False, True])
async def test_cancellation_during_stdin_read_does_not_complete_cancelled_future(monkeypatch, read_error):
    loop = asyncio.get_running_loop()
    descriptor, writer = os.pipe()
    reader = os.fdopen(descriptor, 'r', encoding='utf-8')
    original_read = os.read
    errors = []
    previous_handler = loop.get_exception_handler()
    task = None

    def read_and_cancel(fd, size):
        if fd != descriptor:
            return original_read(fd, size)
        # Cancellation occurs after readable() checked ready.done(), while the
        # real reader task is awaiting that same future. No timing race/retry.
        task.cancel()
        if read_error:
            raise OSError('Read interrupted during cancellation')
        return original_read(fd, size)

    try:
        monkeypatch.setattr(cli.sys, 'stdin', reader)
        monkeypatch.setattr(cli.os, 'read', read_and_cancel)
        loop.set_exception_handler(lambda _, context: errors.append(context))
        os.write(writer, b'hello \xc3')
        task = asyncio.create_task(cli.TerminalInput().read(''))
        with pytest.raises(asyncio.CancelledError):
            await task
        assert errors == [], errors
        assert loop.remove_reader(descriptor) is False
    finally:
        loop.set_exception_handler(previous_handler)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        reader.close()
        os.close(writer)


async def test_stdin_read_error_still_reaches_the_waiter_and_removes_reader(monkeypatch):
    loop = asyncio.get_running_loop()
    descriptor, writer = os.pipe()
    reader = os.fdopen(descriptor, 'r', encoding='utf-8')
    original_read = os.read
    failure = OSError('Read failed without cancellation')

    def fail_read(fd, size):
        if fd == descriptor:
            raise failure
        return original_read(fd, size)

    try:
        monkeypatch.setattr(cli.sys, 'stdin', reader)
        monkeypatch.setattr(cli.os, 'read', fail_read)
        os.write(writer, b'ready')
        with pytest.raises(OSError) as raised:
            await cli.TerminalInput().read('')
        assert raised.value is failure
        assert loop.remove_reader(descriptor) is False
    finally:
        reader.close()
        os.close(writer)
