"""Bounded POSIX shell I/O and cleanup of the process group we create.

This is process lifecycle management, not a sandbox. Descendants that create
their own sessions are outside this group. Windows needs a separate job-object
implementation; do not silently fall back to killing only its shell.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
import selectors
import signal
import subprocess
import time


PIPE_RETAIN_BYTES = 80_000
PIPE_READ_BYTES = 16_384
PROCESS_TERM_GRACE = 0.2
PROCESS_REAP_GRACE = 0.25


@dataclass
class _Capture:
    data: bytearray = field(default_factory=bytearray)
    total: int = 0

    def add(self, data: bytes) -> None:
        self.total += len(data)
        remaining = PIPE_RETAIN_BYTES - len(self.data)
        if remaining > 0:
            self.data.extend(data[:remaining])


@dataclass(frozen=True)
class ProcessOutput:
    stdout: bytes
    stderr: bytes
    stdout_bytes: int
    stderr_bytes: int
    returncode: int | None
    timed_out: bool


def _signal_group(process: subprocess.Popen, sig: int) -> bool:
    try:
        os.killpg(process.pid, sig)
        return True
    except ProcessLookupError:
        return False


def _stop_group(process: subprocess.Popen) -> None:
    if _signal_group(process, signal.SIGTERM):
        deadline = time.monotonic() + PROCESS_TERM_GRACE
        # Waiting only for the shell would miss children that ignore SIGTERM.
        while time.monotonic() < deadline and _signal_group(process, 0):
            process.poll()
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        _signal_group(process, signal.SIGKILL)
    try:
        process.wait(timeout=PROCESS_REAP_GRACE)
    except subprocess.TimeoutExpired:
        # A process in uninterruptible kernel I/O may not exit even after KILL.
        # Preserve the original interruption and keep cleanup itself bounded.
        logging.getLogger(__name__).warning('Shell did not exit after SIGKILL: pid=%s', process.pid)


def run_shell_bounded(command: str, *, cwd: str, timeout: float) -> ProcessOutput:
    """Drain both pipes, retaining at most PIPE_RETAIN_BYTES from each one."""
    if os.name != 'posix':
        raise NotImplementedError('terminal requires POSIX process groups; this platform is not supported')
    stdout, stderr = _Capture(), _Capture()
    process = subprocess.Popen(
        command, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        bufsize=0, start_new_session=True,
    )
    timed_out = False
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for pipe, capture in ((process.stdout, stdout), (process.stderr, stderr)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, capture)
            while selector.get_map() or process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                if not selector.get_map():
                    process.wait(timeout=remaining)
                    break
                for key, _ in selector.select(min(remaining, 0.05)):
                    try:
                        data = os.read(key.fd, PIPE_READ_BYTES)
                    except BlockingIOError:
                        continue
                    if data:
                        key.data.add(data)
                    else:
                        selector.unregister(key.fileobj)
            process.wait(timeout=max(0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        timed_out = True
        _stop_group(process)
    except BaseException:
        try:
            _stop_group(process)
        except Exception:
            logging.getLogger(__name__).exception('Shell cleanup failed')
        raise
    finally:
        process.stdout.close()
        process.stderr.close()
    return ProcessOutput(
        bytes(stdout.data), bytes(stderr.data), stdout.total, stderr.total,
        process.returncode, timed_out,
    )
