"""Bounded local text I/O and atomic whole-file replacement."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
from typing import TextIO


MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_READ_CHARS = 64 * 1024
MAX_SCAN_CHARS = 64 * 1024 * 1024
READ_CHUNK_CHARS = 8192
MAX_DIRECTORY_ENTRIES = 1000
MAX_DIRECTORY_CHARS = 64 * 1024


def encode_text(content: str) -> bytes:
    if not isinstance(content, str):
        raise TypeError('Text content must be a str')
    # Check characters first so encoding cannot allocate an unbounded buffer.
    if len(content) > MAX_TEXT_BYTES:
        raise ValueError(f'Text exceeds the {MAX_TEXT_BYTES}-byte limit')
    encoded = content.encode('utf-8')
    if len(encoded) > MAX_TEXT_BYTES:
        raise ValueError(f'Text exceeds the {MAX_TEXT_BYTES}-byte limit')
    return encoded


def read_editable_text(path: Path) -> tuple[str, int]:
    if path.stat().st_size > MAX_TEXT_BYTES:
        raise ValueError(f'File to edit exceeds the {MAX_TEXT_BYTES}-byte limit; use a tool that processes chunks')
    content = bytearray()
    with path.open('rb') as handle:
        while True:
            part = handle.read(min(READ_CHUNK_CHARS, MAX_TEXT_BYTES - len(content) + 1))
            if not part:
                break
            content.extend(part)
            if len(content) > MAX_TEXT_BYTES:
                raise ValueError(f'File to edit exceeds the {MAX_TEXT_BYTES}-byte limit; use a tool that processes chunks')
    # Match read_file and the previous text-mode replacement contract:
    # universal newlines are shown, matched and serialized as LF. Normalize
    # bytes before decoding so the returned size budgets exactly that text.
    content = content.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
    return content.decode('utf-8'), len(content)


def atomic_write(path: Path, content: bytes) -> None:
    """Stage in the same directory; a failed write/replace leaves bytes intact.

    Existing permission bits are preserved. New files use mkstemp's private
    permissions (0600 on POSIX). This is atomic visibility, not crash durability;
    inode identity, hard links, ACLs and other extended metadata are not retained.
    """
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.mas-write-', dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(content)
        if mode is not None:
            staged.chmod(mode)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


class BoundedTextReader:
    """Bound each allocation and the work spent locating a requested page."""

    def __init__(self, handle: TextIO):
        self.handle = handle
        self.scanned = 0

    def readline(self, size: int = READ_CHUNK_CHARS) -> str:
        size = min(size, READ_CHUNK_CHARS, MAX_SCAN_CHARS - self.scanned + 1)
        part = self.handle.readline(size)
        self.scanned += len(part)
        if self.scanned > MAX_SCAN_CHARS:
            raise ValueError(f'Locating the page exceeds the {MAX_SCAN_CHARS}-character scan limit; use a tool that processes chunks')
        return part

    def skip_line(self) -> bool:
        seen = False
        while True:
            part = self.readline()
            if not part:
                return seen
            seen = True
            if part.endswith('\n'):
                return True


def read_page(handle: TextIO, *, line_offset: int, char_offset: int, n_lines: int, max_chars: int) -> str:
    reader = BoundedTextReader(handle)
    skipped = 0
    for _ in range(line_offset - 1):
        if not reader.skip_line():
            return f'(Past the end of the file; the file has {skipped} lines)'
        skipped += 1
    to_skip = char_offset
    while to_skip:
        part = reader.readline(to_skip)
        if not part or part.endswith('\n'):
            raise ValueError('char_offset is outside the character range of the selected line')
        to_skip -= len(part)

    lines: list[str] = []
    used = 0
    line_number, column = line_offset, char_offset
    for _ in range(n_lines):
        first = reader.readline(1)
        if not first:
            break
        prefix = f'{line_number:>6}\t'
        available = max_chars - used - (1 if lines else 0) - len(prefix)
        if available < 1:
            if not lines:
                raise ValueError(f'max_chars is too small; at least {len(prefix) + 1} characters are required')
            return _page_result(lines, line_number, column)
        pieces: list[str] = []
        part = first
        while True:
            complete = part.endswith('\n')
            body = part[:-1] if complete else part
            if len(body) > available:
                pieces.append(body[:available])
                column += available
                lines.append(prefix + ''.join(pieces))
                return _page_result(lines, line_number, column)
            pieces.append(body)
            column += len(body)
            available -= len(body)
            if complete or not part:
                break
            # One extra character distinguishes an exact boundary from a
            # truncated line. It is not included in the continuation offset.
            part = reader.readline(available + 1)
        rendered = prefix + ''.join(pieces)
        used += len(rendered) + (1 if lines else 0)
        lines.append(rendered)
        line_number += 1
        column = 0
        if not part:
            break
    else:
        if reader.readline(1):
            return _page_result(lines, line_number, 0)
    return '\n'.join(lines) if lines else f'(Past the end of the file; the file has {skipped} lines)'


def _page_result(lines: list[str], line_number: int, column: int) -> str:
    return ('\n'.join(lines) + '\n... (Output truncated; use line_offset/n_lines; '
            f'resume at line_offset={line_number}, char_offset={column})')
