"""Resource limits and failed-effect recovery for local tools (no model I/O)."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import signal
import stat
import sys
import time
import tracemalloc

import pytest

from tools.builtin import terminal
from tools.file_ops import list_dir, read_file, str_replace, write_file
from tools.results import ToolFailure
from tools.workspace import Workspace, workspace_context


@pytest.fixture(autouse=True)
def local_workspace(tmp_path):
    with workspace_context(Workspace(roots=(str(tmp_path),), allow_unsafe_terminal=True)):
        yield


def python_command(code):
    return shlex.quote(sys.executable) + " -c " + shlex.quote(code)


def kill_probe(pid_path):
    if pid_path.exists():
        try:
            os.kill(int(pid_path.read_text()), signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process groups')
def test_timeout_stops_descendants_that_ignore_termination(tmp_path):
    pid_path = tmp_path / 'child.pid'
    code = (
        "import os,pathlib,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "pathlib.Path('child.pid').write_text(str(os.getpid())); "
        "time.sleep(1.4); pathlib.Path('late.txt').write_text('late'); time.sleep(3)"
    )
    try:
        started = time.monotonic()
        result = terminal(python_command(code) + ' & wait', timeout=1)
        assert isinstance(result, ToolFailure) and 'Timeout' in result
        assert time.monotonic() - started < 2
        time.sleep(0.5)
        assert not (tmp_path / 'late.txt').exists()
    finally:
        kill_probe(pid_path)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX terminal')
def test_terminal_drains_both_pipes_without_retaining_all_output():
    code = "import os; os.write(1,b'O'*(8*1024*1024)); os.write(2,b'E'*(8*1024*1024))"
    tracemalloc.start()
    try:
        result = terminal(python_command(code), timeout=5)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert not isinstance(result, ToolFailure)
    assert peak < 4 * 1024 * 1024
    assert len(result) < 20500
    assert 'OOOO' in result and '[stderr]' in result and 'EEEE' in result
    assert 'truncated' in result


@pytest.mark.skipif(os.name != 'posix', reason='POSIX signals')
def test_keyboard_interrupt_stops_descendants_and_is_not_swallowed(tmp_path):
    pid_path = tmp_path / 'child.pid'
    code = (
        "import os,pathlib,time; pathlib.Path('child.pid').write_text(str(os.getpid())); "
        "time.sleep(0.8); pathlib.Path('late.txt').write_text('late'); time.sleep(2)"
    )

    def interrupt(*args):
        raise KeyboardInterrupt

    old_handler = signal.signal(signal.SIGALRM, interrupt)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.3)
        with pytest.raises(KeyboardInterrupt):
            terminal(python_command(code) + ' & wait', timeout=3)
        time.sleep(0.6)
        assert not (tmp_path / 'late.txt').exists()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        kill_probe(pid_path)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX terminal')
def test_terminal_preserves_stderr_and_failure_status():
    result = terminal("printf 'out'; printf 'err' >&2; exit 7")
    assert isinstance(result, ToolFailure)
    assert result == 'out\n[stderr]\nerr\n[exit code: 7]'


@pytest.mark.parametrize('position', ['requested', 'skipped', 'lookahead'])
def test_read_file_does_not_materialize_giant_lines(tmp_path, position):
    giant = 'G' * (8 * 1024 * 1024)
    content = {'requested': giant, 'skipped': giant + '\ntail\n', 'lookahead': 'head\n' + giant}[position]
    (tmp_path / 'large.txt').write_text(content)
    tracemalloc.start()
    try:
        result = read_file('large.txt', line_offset=2 if position == 'skipped' else 1, n_lines=1, max_chars=100)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert not isinstance(result, ToolFailure)
    assert peak < 2 * 1024 * 1024
    assert len(result) < 400
    assert ('tail' if position == 'skipped' else 'head' if position == 'lookahead' else 'GGGG') in result


def test_read_file_continuation_advances_by_unicode_characters(tmp_path):
    content = '\u7532\u4e59🙂\u4e19\u4e01🌍' * 20
    (tmp_path / 'unicode.txt').write_text(content)
    parts = []
    offset = 0
    for _ in range(20):
        result = read_file('unicode.txt', n_lines=1, max_chars=30, char_offset=offset)
        assert not isinstance(result, ToolFailure)
        parts.append(result.split('\n...')[0].split('\t', 1)[1])
        continuation = re.search(r'line_offset=(\d+), char_offset=(\d+)', result)
        if continuation is None:
            break
        assert int(continuation[1]) == 1
        next_offset = int(continuation[2])
        assert next_offset > offset
        offset = next_offset
    else:
        pytest.fail('continuation did not reach EOF')
    assert ''.join(parts) == content


def test_negative_read_limit_cannot_disable_the_limit(tmp_path):
    (tmp_path / 'data.txt').write_text('X' * 1000)
    assert isinstance(read_file('data.txt', max_chars=-1), ToolFailure)


def test_read_file_enforces_hard_output_limit(tmp_path):
    (tmp_path / 'data.txt').write_text('X' * (1024 * 1024))
    result = read_file('data.txt', max_chars=10**9)
    assert not isinstance(result, ToolFailure)
    assert len(result) < 66000
    assert 'truncated' in result


@pytest.mark.parametrize('content', [123, {}, '\ud800'])
def test_invalid_write_preserves_existing_contents(tmp_path, content):
    target = tmp_path / 'existing.txt'
    target.write_text('original')
    result = write_file('existing.txt', content)
    assert isinstance(result, ToolFailure)
    assert target.read_bytes() == b'original'


def test_invalid_append_does_not_create_directories(tmp_path):
    result = write_file('absent/new.txt', '\ud800', mode='append')
    assert isinstance(result, ToolFailure)
    assert not (tmp_path / 'absent').exists()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX named pipe')
def test_overwrite_does_not_replace_a_non_regular_file(tmp_path):
    target = tmp_path / 'pipe'
    os.mkfifo(target)
    result = write_file('pipe', 'data')
    assert isinstance(result, ToolFailure)
    assert stat.S_ISFIFO(target.stat().st_mode)


def test_invalid_replacement_preserves_existing_contents(tmp_path):
    target = tmp_path / 'existing.txt'
    target.write_text('original')
    result = str_replace('existing.txt', 'original', '\ud800')
    assert isinstance(result, ToolFailure)
    assert target.read_bytes() == b'original'


@pytest.mark.parametrize('operation', ['write', 'replace'])
def test_atomic_commit_failure_preserves_contents_and_removes_staging(tmp_path, monkeypatch, operation):
    target = tmp_path / 'existing.txt'
    target.write_text('original')

    def fail_commit(*args, **kwargs):
        raise OSError('simulated commit failure')

    monkeypatch.setattr(os, 'replace', fail_commit)
    result = write_file('existing.txt', 'changed') if operation == 'write' else str_replace('existing.txt', 'original', 'changed')
    assert isinstance(result, ToolFailure)
    assert target.read_bytes() == b'original'
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize('operation', ['write', 'replace'])
def test_successful_atomic_update_preserves_permissions(tmp_path, operation):
    target = tmp_path / 'existing.txt'
    target.write_text('original')
    target.chmod(0o640)
    result = write_file('existing.txt', 'changed') if operation == 'write' else str_replace('existing.txt', 'original', 'changed')
    assert not isinstance(result, ToolFailure)
    assert target.read_text() == 'changed'
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_replacement_rejects_large_files_without_modifying_them(tmp_path):
    target = tmp_path / 'large.txt'
    with target.open('wb') as handle:
        handle.write(b'a')
        handle.truncate(9 * 1024 * 1024)
    result = str_replace('large.txt', 'a', 'b')
    assert isinstance(result, ToolFailure)
    assert 'limit' in result
    with target.open('rb') as handle:
        assert handle.read(1) == b'a'


def test_replacement_rejects_expansion_before_allocating_it(tmp_path):
    target = tmp_path / 'data.txt'
    target.write_text('a' * 10000)
    tracemalloc.start()
    try:
        result = str_replace('data.txt', 'a', 'x' * 1000, replace_all=True)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert isinstance(result, ToolFailure)
    assert peak < 2 * 1024 * 1024
    assert target.read_text() == 'a' * 10000


def test_replacement_checks_encoded_expansion_before_allocating_it(tmp_path):
    target = tmp_path / 'data.txt'
    target.write_text('a' * 10000)
    tracemalloc.start()
    try:
        result = str_replace('data.txt', 'a', '🙂' * 300, replace_all=True)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert isinstance(result, ToolFailure)
    assert peak < 2 * 1024 * 1024
    assert target.read_text() == 'a' * 10000


@pytest.mark.parametrize('newline', [b'\r\n', b'\r'])
def test_replacement_matches_the_normalized_text_shown_by_read_file(tmp_path, newline):
    target = tmp_path / 'newlines.txt'
    target.write_bytes(b'first' + newline + b'second' + newline)
    assert read_file('newlines.txt') == '     1\tfirst\n     2\tsecond'
    result = str_replace('newlines.txt', 'first\nsecond', 'changed')
    assert not isinstance(result, ToolFailure)
    assert target.read_bytes() == b'changed\n'


def test_replacement_byte_budget_uses_normalized_newline_size(tmp_path):
    target = tmp_path / 'newlines.txt'
    target.write_bytes(b'a\r\n' * (1024 * 1024))
    # The normalized 2 MiB input expands to exactly 8 MiB. Counting original
    # CRLF bytes would incorrectly predict 9 MiB and reject a permitted edit.
    result = str_replace('newlines.txt', 'a', '1234567', replace_all=True)
    assert not isinstance(result, ToolFailure)
    assert target.stat().st_size == 8 * 1024 * 1024
    with target.open('rb') as handle:
        assert handle.read(16) == b'1234567\n1234567\n'


def test_directory_listing_has_an_explicit_entry_limit(tmp_path):
    for index in range(1001):
        (tmp_path / f'entry-{index:04d}').touch()
    result = list_dir('.')
    assert isinstance(result, ToolFailure)
    assert 'limit' in result
    assert len(result) < 1000


def test_directory_limit_also_bounds_underlying_enumeration_memory(tmp_path):
    for index in range(8000):
        (tmp_path / (f'{index:04d}-' + 'x' * 230)).touch()
    tracemalloc.start()
    try:
        result = list_dir('.')
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert isinstance(result, ToolFailure)
    assert peak < 2 * 1024 * 1024
