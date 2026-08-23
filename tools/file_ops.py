"""文件操作工具。

提供 LLM 读写文件的基础能力，避免每次都走 terminal 的 cat/sed/echo：
    - read_file:    读取文件（支持部分读取、带行号）
    - write_file:   写入文件（覆盖或追加）
    - str_replace:  精确字符串替换（支持多处）
    - list_dir:     列出目录内容

所有工具均返回字符串，失败也不抛异常（返回错误信息字符串），
由 ToolRegistry 统一兜底。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path

from tools.decorator import tool

DEFAULT_READ_FILE_MAX_CHARS = 12000
_WORKSPACE_ROOTS: ContextVar[tuple[str, ...] | None] = ContextVar(
    "workspace_roots",
    default=None,
)


def workspace_roots_from_value(value: str | Iterable[str] | None) -> tuple[str, ...]:
    """Normalize workspace roots from config/env compatible values."""
    if value is None:
        return ()
    if isinstance(value, str):
        candidates = [part for part in value.split(os.pathsep) if part.strip()]
    else:
        candidates = [str(part) for part in value if str(part).strip()]
    return tuple(candidates)


@contextmanager
def workspace_roots_context(roots: str | Iterable[str] | None) -> Iterator[None]:
    """Temporarily use explicit workspace roots for workspace tools."""
    normalized = workspace_roots_from_value(roots)
    if not normalized:
        yield
        return
    token = _WORKSPACE_ROOTS.set(normalized)
    try:
        yield
    finally:
        _WORKSPACE_ROOTS.reset(token)


def _workspace_roots() -> list[Path]:
    """Return allowed workspace roots for file tools.

    Configure with MAS_WORKSPACE_ROOTS using os.pathsep separators. The default
    keeps the current local-dev behavior usable while preventing accidental
    writes into system paths.
    """
    configured = _WORKSPACE_ROOTS.get()
    if configured is not None:
        candidates = list(configured)
    else:
        raw = os.environ.get("MAS_WORKSPACE_ROOTS") or os.environ.get("WorkspaceRoots")
        if raw:
            candidates = [p for p in raw.split(os.pathsep) if p.strip()]
        else:
            candidates = [str(Path.home())]
    return [Path(os.path.expanduser(p)).resolve() for p in candidates]


def _resolve(path: str) -> Path:
    """把路径解析为绝对 Path（支持 ~ 和相对路径）。"""
    return Path(os.path.expanduser(path)).resolve()


def _is_within_workspace(path: Path) -> bool:
    for root in _workspace_roots():
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _workspace_error(path: Path) -> str:
    roots = ", ".join(str(root) for root in _workspace_roots())
    return f"[错误] 路径不在允许的工作区内: {path}。允许范围: {roots}"


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
) -> str:
    """读取文本文件内容，带行号显示（类似 cat -n）。

    Args:
        path:        文件路径，支持 ~ 和相对路径
        line_offset: 起始行号（从1开始），默认1
        n_lines:     读取的行数，默认1000（最大1000行）
        max_chars:   本次返回的最大字符数；0 表示读取 MAS_READ_FILE_MAX_CHARS
    """
    try:
        p = _resolve_checked(path)
        if isinstance(p, str):
            return p
        if not p.exists():
            return f"[错误] 文件不存在: {p}"
        if not p.is_file():
            return f"[错误] 不是文件: {p}"
        if p.stat().st_size == 0:
            return "(空文件)"

        # 限制参数范围
        line_offset = max(1, line_offset)
        n_lines = max(1, min(n_lines, 1000))

        with p.open("r", encoding="utf-8") as f:
            # 跳过前 N-1 行
            for _ in range(line_offset - 1):
                if not f.readline():
                    break
            # 读取目标行
            lines = []
            for _ in range(n_lines):
                line = f.readline()
                if not line:
                    break
                lines.append(line)
            # 读满 n_lines 时试探是否还有更多内容
            has_more = len(lines) == n_lines and bool(f.readline())

        if not lines:
            return f"(超出文件末尾，文件共 {line_offset - 1} 行)"

        # 带行号格式化
        numbered = []
        for i, line in enumerate(lines):
            num = line_offset + i
            # 去掉末尾换行后再加，避免双换行
            numbered.append(f"{num:>6}\t{line.rstrip(chr(10))}")
        text = "\n".join(numbered)

        total_hint = ""
        if has_more:
            last_line = line_offset + len(lines) - 1
            total_hint = f"\n... (已读至第 {last_line} 行，后续还有内容)"
        text = text + total_hint
        char_limit = _read_file_max_chars(max_chars)
        if char_limit > 0 and len(text) > char_limit:
            return (
                text[:char_limit]
                + f"\n... (输出已截断，共 {len(text)} 字符；"
                "可用 line_offset/n_lines 继续分页读取)"
            )
        return text
    except UnicodeDecodeError:
        return f"[错误] 文件不是文本或编码不是 UTF-8: {path}"
    except Exception as e:
        return f"[错误] {type(e).__name__}: {e}"


def _read_file_max_chars(max_chars: int = 0) -> int:
    if max_chars:
        return max(0, int(max_chars))
    raw = os.environ.get("MAS_READ_FILE_MAX_CHARS", "")
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            return DEFAULT_READ_FILE_MAX_CHARS
    return DEFAULT_READ_FILE_MAX_CHARS


@tool
def write_file(
    path: str,
    content: str,
    mode: str = "overwrite",
) -> str:
    """写入文本文件。

    Args:
        path:    文件路径，支持 ~ 和相对路径。父目录不存在会自动创建。
        content: 要写入的文本内容
        mode:    写入模式："overwrite" 覆盖（默认），"append" 追加到末尾
    """
    try:
        p = _resolve_checked(path)
        if isinstance(p, str):
            return p
        if mode == "append":
            # 追加模式：文件不存在则创建
            if p.exists() and not p.is_file():
                return f"[错误] 目标已存在且不是文件: {p}"
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(content)
            return f"(已追加到 {p}，共 {len(content)} 字符)"
        elif mode == "overwrite":
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("w", encoding="utf-8") as f:
                f.write(content)
            return f"(已写入 {p}，共 {len(content)} 字符)"
        else:
            return f"[错误] 不支持的 mode: {mode!r}，应为 'overwrite' 或 'append'"
    except Exception as e:
        return f"[错误] {type(e).__name__}: {e}"


@tool
def str_replace(
    path: str,
    old: str,
    new: str,
    replace_all: bool = False,
) -> str:
    """替换文件中的字符串。

    Args:
        path:        文件路径
        old:         要被替换的字符串（必须精确匹配，可多行）
        new:         替换为的新字符串
        replace_all: True 替换所有匹配，False 仅替换第一个（默认）
    """
    try:
        p = _resolve_checked(path)
        if isinstance(p, str):
            return p
        if not p.exists():
            return f"[错误] 文件不存在: {p}"
        if not p.is_file():
            return f"[错误] 不是文件: {p}"
        if not old:
            return "[错误] old 不能为空字符串"
        if old == new:
            return "[错误] old 与 new 相同，无需替换"

        with p.open("r", encoding="utf-8") as f:
            content = f.read()

        count = content.count(old)
        if count == 0:
            return f"[错误] 未找到匹配的字符串。文件共 {len(content)} 字符。"

        if replace_all:
            new_content = content.replace(old, new)
            replaced = count
        else:
            new_content = content.replace(old, new, 1)
            replaced = 1

        with p.open("w", encoding="utf-8") as f:
            f.write(new_content)

        suffix = f"（共 {count} 处，已替换全部）" if replace_all else f"（共 {count} 处，已替换第 1 处）"
        return f"(已替换 {p} 中的字符串{suffix})"
    except Exception as e:
        return f"[错误] {type(e).__name__}: {e}"


@tool
def list_dir(path: str = ".") -> str:
    """列出目录内容。

    Args:
        path: 目录路径，默认当前目录
    """
    try:
        p = _resolve_checked(path)
        if isinstance(p, str):
            return p
        if not p.exists():
            return f"[错误] 路径不存在: {p}"
        if not p.is_dir():
            return f"[错误] 不是目录: {p}"

        entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name))
        if not entries:
            return f"(空目录: {p})"

        lines = []
        for entry in entries:
            if entry.is_dir():
                lines.append(f"  {entry.name}/")
            elif entry.is_symlink():
                lines.append(f"  {entry.name} -> {entry.resolve().name}")
            else:
                size = entry.stat().st_size
                lines.append(f"  {entry.name}  ({size} 字节)")
        return f"{p}:\n" + "\n".join(lines)
    except Exception as e:
        return f"[错误] {type(e).__name__}: {e}"
