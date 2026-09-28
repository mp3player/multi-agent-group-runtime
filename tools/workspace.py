"""Explicit local tool settings and per-execution workspace context."""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
import os
from pathlib import Path

from tools.results import ToolFailure
from core.defaults import READ_FILE_MAX_CHARS

DEFAULT_READ_FILE_MAX_CHARS = READ_FILE_MAX_CHARS


def workspace_roots_from_value(value: str | Iterable[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    candidates = value.split(os.pathsep) if isinstance(value, str) else value
    return tuple(str(part) for part in candidates if str(part).strip())


@dataclass(frozen=True, slots=True)
class Workspace:
    """Settings captured at construction; no environment is read by tools.

    Explicit roots select the first root as the default working directory.
    With no roots, file bounds default to home and cwd to the construction cwd.
    """

    roots: tuple[str, ...] = ()
    cwd: str | None = None
    read_file_max_chars: int = DEFAULT_READ_FILE_MAX_CHARS
    allow_unsafe_terminal: bool = False

    def __post_init__(self) -> None:
        explicit = workspace_roots_from_value(self.roots)
        roots = tuple(str(Path(root).expanduser().resolve()) for root in explicit)
        cwd = self.cwd if self.cwd is not None else (roots[0] if roots else str(Path.cwd()))
        object.__setattr__(self, 'roots', roots or (str(Path.home().resolve()),))
        object.__setattr__(self, 'cwd', str(Path(cwd).expanduser().resolve()))
        if type(self.read_file_max_chars) is not int or self.read_file_max_chars < 0:
            raise ValueError('read_file_max_chars must be a nonnegative integer')


_WORKSPACE: ContextVar[Workspace | None] = ContextVar('tool_workspace', default=None)


def current_workspace() -> Workspace:
    configured = _WORKSPACE.get()
    return configured if configured is not None else Workspace()


@contextmanager
def workspace_context(workspace: Workspace) -> Iterator[None]:
    token = _WORKSPACE.set(workspace)
    try:
        yield
    finally:
        _WORKSPACE.reset(token)


@contextmanager
def workspace_roots_context(
    roots: str | Iterable[str] | None, *, cwd: str | None = None,
) -> Iterator[None]:
    """Convenience scope for direct file/terminal tool calls."""
    settings = replace(current_workspace(), roots=workspace_roots_from_value(roots), cwd=cwd)
    with workspace_context(settings):
        yield


def _workspace_roots() -> list[Path]:
    return [Path(root) for root in current_workspace().roots]


def _resolve(path: str) -> Path:
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = Path(current_workspace().cwd) / resolved
    return resolved.resolve()


def _is_within_workspace(path: Path) -> bool:
    for root in _workspace_roots():
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _workspace_error(path: Path) -> ToolFailure:
    roots = ', '.join(current_workspace().roots)
    return ToolFailure(f'[Error] Path is outside the allowed workspace: {path}. Allowed roots: {roots}')
