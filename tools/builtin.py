"""一些内置工具示例。"""

from __future__ import annotations

import os
import re
import shlex
import subprocess

from tools.decorator import tool
from tools.file_ops import _is_within_workspace, _resolve, _workspace_error


@tool
def get_weather(city: str) -> str:
    """获取指定城市的天气。

    Args:
        city: 城市名称，如：北京、上海
    """
    return "[暂未实现] get_weather 只是示例工具，当前没有接入天气数据源。"


@tool
def search(query: str, limit: int = 5) -> str:
    """搜索互联网内容。

    Args:
        query: 搜索关键词
        limit: 返回结果数量，默认5条
    """
    return "[暂未实现] search 只是示例工具，当前没有接入搜索服务。"


@tool
def calculate(expression: str) -> str:
    """计算一个数学表达式。

    Args:
        expression: 数学表达式，如 "1+2*3"
    """
    return "[暂未实现] calculate 只是示例工具，当前没有接入表达式求值器。"


@tool
def terminal(command: str, timeout: int = 30, cwd: str = ".") -> str:
    """在终端执行 shell 命令并返回输出。

    Args:
        command: 要执行的 shell 命令
        timeout: 超时时间（秒），默认30秒
        cwd: 命令执行目录，必须位于允许的工作区内，默认当前目录
    """
    try:
        workdir = _resolve(cwd)
        if not _is_within_workspace(workdir):
            return _workspace_error(workdir)
        if not workdir.exists() or not workdir.is_dir():
            return f"[错误] cwd 不是有效目录: {workdir}"
        unsafe = _terminal_workspace_violation(command)
        if unsafe:
            return unsafe
        timeout = max(1, min(int(timeout), 120))
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(workdir),
            capture_output=True,
            text=False,  # 以字节接收，手动解码避免 UnicodeDecodeError
            timeout=timeout,
        )
        # 手动解码：优先 utf-8，失败则用 replace 兜底
        def _decode(b: bytes) -> str:
            if not b:
                return ""
            return b.decode("utf-8", errors="replace")

        output = _decode(result.stdout or b"")
        stderr = _decode(result.stderr or b"")
        if stderr:
            output += (f"\n[stderr]\n{stderr}" if output else stderr)
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        output = output.strip()
        # 截断超长输出，避免 OOM / 刷屏
        max_chars = 20000
        if len(output) > max_chars:
            output = output[:max_chars] + f"\n... (输出已截断，共 {len(output)} 字符)"
        return output or "(无输出)"
    except subprocess.TimeoutExpired:
        return f"[超时] 命令在 {timeout} 秒内未完成"
    except Exception as e:
        return f"[执行失败] {type(e).__name__}: {e}"


def _terminal_workspace_violation(command: str) -> str:
    """Reject obvious command references outside configured workspace roots.

    This is a guardrail, not a full shell sandbox. Set MAS_ALLOW_UNSAFE_TERMINAL=1
    to bypass it in trusted local development.
    """
    if os.environ.get("MAS_ALLOW_UNSAFE_TERMINAL") == "1":
        return ""
    paths = set(_absolute_path_tokens(command))
    for path_text in paths:
        path = _resolve(path_text)
        if not _is_within_workspace(path):
            return _workspace_error(path)
    return ""


def _absolute_path_tokens(command: str) -> list[str]:
    tokens: list[str] = []
    try:
        shell_tokens = shlex.split(command)
    except ValueError:
        shell_tokens = command.split()
    for token in shell_tokens:
        if token.startswith(("/", "~")):
            tokens.append(token)
    tokens.extend(re.findall(r"(?<![\w:])(?:~|/)[^\s;&|<>`'\"]+", command))
    return [
        token.rstrip("),.]")
        for token in tokens
        if token not in ("/", "~") and not _looks_like_option(token)
    ]


def _looks_like_option(token: str) -> bool:
    return token.startswith("-") or token.startswith("//")
