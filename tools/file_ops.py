"""File operation tools.

Give the LLM direct file access without shell commands such as cat/sed/echo:
    - read_file:    Read files with line numbers and partial reads.
    - write_file:   Write files by overwriting or appending.
    - str_replace:  Replace exact strings, optionally at every occurrence.
    - list_dir:     List directory contents

All tools return strings. Failures use the ToolFailure string subtype,
with ToolRegistry providing a shared exception boundary.
"""

from __future__ import annotations

import os
from pathlib import Path

from tools.decorator import tool
from tools.results import ToolFailure
from tools.file_io import (
    MAX_DIRECTORY_CHARS, MAX_DIRECTORY_ENTRIES, MAX_READ_CHARS, MAX_TEXT_BYTES,
    atomic_write, encode_text, read_editable_text, read_page,
)
from tools.workspace import (
    current_workspace, _is_within_workspace, _resolve, _workspace_error,
)


def _resolve_checked(path: str) -> Path | str:
    p = _resolve(path)
    if not _is_within_workspace(p):
        return _workspace_error(p)
    return p


@tool
def read_file(
    path: str,
    line_offset: int = 1,
    n_lines: int = 1000,
    max_chars: int = 0,
    char_offset: int = 0,
) -> str:
    """Read a text file with line numbers, similar to cat -n.

    Args:
        path:        File path supporting ~ and relative paths.
        line_offset: Starting line number, counted from 1; defaults to 1.
        n_lines:     Number of lines to read; defaults to 1000, with a maximum of 1000.
        max_chars:   Maximum output characters; 0 uses workspace settings, with a hard limit of 65536.
        char_offset: Unicode characters to skip in the starting line, counted from 0, for continuing long lines.
    """
    try:
        if type(line_offset) is not int or type(n_lines) is not int:
            raise TypeError('line_offset/n_lines must be integers')
        if type(char_offset) is not int or char_offset < 0:
            raise ValueError('char_offset must be a nonnegative integer')
        char_limit = _read_file_max_chars(max_chars)
        p = _resolve_checked(path)
        if isinstance(p, str):
            return p
        if not p.exists():
            return ToolFailure(f"[Error] File does not exist: {p}")
        if not p.is_file():
            return ToolFailure(f"[Error] Not a file: {p}")
        if p.stat().st_size == 0:
            return "(Empty file)"

        # Clamp parameters to their supported ranges.
        line_offset = max(1, line_offset)
        n_lines = max(1, min(n_lines, 1000))

        with p.open("r", encoding="utf-8") as f:
            return read_page(
                f, line_offset=line_offset, char_offset=char_offset,
                n_lines=n_lines, max_chars=char_limit,
            )
    except UnicodeDecodeError:
        return ToolFailure(f"[Error] File is not text or is not encoded as UTF-8: {path}")
    except Exception as e:
        return ToolFailure(f"[Error] {type(e).__name__}: {e}")


def _read_file_max_chars(max_chars: int = 0) -> int:
    if type(max_chars) is not int or max_chars < 0:
        raise ValueError('max_chars must be a nonnegative integer')
    requested = max_chars or current_workspace().read_file_max_chars
    return min(requested, MAX_READ_CHARS) if requested else MAX_READ_CHARS


@tool
def write_file(
    path: str,
    content: str,
    mode: str = "overwrite",
) -> str:
    """Write a text file.

    Args:
        path:    File path supporting ~ and relative paths. Missing parent directories are created.
        content: Text content to write.
        mode:    Write mode: "overwrite" replaces the file (default); "append" adds to the end.
    """
    try:
        encoded = encode_text(content)
        p = _resolve_checked(path)
        if isinstance(p, str):
            return p
        if p.exists() and not p.is_file():
            if mode == 'overwrite' and p.is_dir():
                raise IsADirectoryError(f'Target is a directory: {p}')
            return ToolFailure(f"[Error] Target already exists and is not a file: {p}")
        if mode == "append":
            # Append mode creates the file if it does not exist.
            p.parent.mkdir(parents=True, exist_ok=True)
            # Validation precedes opening. Append is not transactional if the
            # OS fails partway through a write (for example on a full disk).
            with p.open("ab") as f:
                f.write(encoded)
            return f"(Appended {len(content)} characters to {p})"
        elif mode == "overwrite":
            atomic_write(p, encoded)
            return f"(Wrote {len(content)} characters to {p})"
        else:
            return ToolFailure(f"[Error] Unsupported mode: {mode!r}; expected 'overwrite' or 'append'")
    except Exception as e:
        return ToolFailure(f"[Error] {type(e).__name__}: {e}")


@tool
def str_replace(
    path: str,
    old: str,
    new: str,
    replace_all: bool = False,
) -> str:
    """Replace a string in a file.

    Args:
        path:        File path.
        old:         String to replace; must match exactly and may span multiple lines.
        new:         Replacement string.
        replace_all: True replaces all matches; False replaces only the first (default).
    """
    try:
        old_bytes = len(encode_text(old))
        new_bytes = len(encode_text(new))
        if type(replace_all) is not bool:
            raise TypeError('replace_all must be a bool')
        p = _resolve_checked(path)
        if isinstance(p, str):
            return p
        if not p.exists():
            return ToolFailure(f"[Error] File does not exist: {p}")
        if not p.is_file():
            return ToolFailure(f"[Error] Not a file: {p}")
        if not old:
            return ToolFailure("[Error] old must not be an empty string")
        if old == new:
            return ToolFailure("[Error] old and new are identical; no replacement is needed")

        content, content_bytes = read_editable_text(p)

        count = content.count(old)
        if count == 0:
            return ToolFailure(f"[Error] No matching string found. The file has {len(content)} characters.")

        replacements = count if replace_all else 1
        if content_bytes + replacements * (new_bytes - old_bytes) > MAX_TEXT_BYTES:
            raise ValueError(f'Replacement result exceeds the {MAX_TEXT_BYTES}-byte limit')
        if replace_all:
            new_content = content.replace(old, new)
        else:
            new_content = content.replace(old, new, 1)
        atomic_write(p, encode_text(new_content))

        suffix = f"; replaced all {count} matches" if replace_all else f"; replaced the first of {count} matches"
        return f"(Replaced the string in {p}{suffix})"
    except Exception as e:
        return ToolFailure(f"[Error] {type(e).__name__}: {e}")


@tool
def list_dir(path: str = ".") -> str:
    """List directory contents.

    Args:
        path: Directory path; defaults to the Agent working directory (the first configured workspace root).
    """
    try:
        p = _resolve_checked(path)
        if isinstance(p, str):
            return p
        if not p.exists():
            return ToolFailure(f"[Error] Path does not exist: {p}")
        if not p.is_dir():
            return ToolFailure(f"[Error] Not a directory: {p}")

        entries = []
        # Path.iterdir() materializes directory entries on supported Python
        # versions; stop the OS iterator itself when the entry budget is full.
        with os.scandir(p) as iterator:
            for entry in iterator:
                if len(entries) >= MAX_DIRECTORY_ENTRIES:
                    return ToolFailure(f'[Error] Directory exceeds the {MAX_DIRECTORY_ENTRIES}-entry limit; select a subdirectory or use terminal to filter by name')
                entries.append(Path(entry.path))
        entries.sort(key=lambda x: (not x.is_dir(), x.name))
        if not entries:
            return f"(Empty directory: {p})"

        lines = []
        output_chars = len(str(p)) + 2
        for entry in entries:
            if entry.is_dir():
                lines.append(f"  {entry.name}/")
            elif entry.is_symlink():
                lines.append(f"  {entry.name} -> {entry.resolve().name}")
            else:
                size = entry.stat().st_size
                lines.append(f"  {entry.name}  ({size} bytes)")
            output_chars += len(lines[-1]) + 1
            if output_chars > MAX_DIRECTORY_CHARS:
                return ToolFailure(f'[Error] Directory output exceeds the {MAX_DIRECTORY_CHARS}-character limit; select a subdirectory or use terminal to filter by name')
        return f"{p}:\n" + "\n".join(lines)
    except Exception as e:
        return ToolFailure(f"[Error] {type(e).__name__}: {e}")
