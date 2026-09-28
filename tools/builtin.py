"""Built-in terminal tool."""

from __future__ import annotations

import re
import shlex
from urllib.parse import unquote, urlsplit

from tools.decorator import tool
from tools.workspace import _is_within_workspace, _resolve, _workspace_error, current_workspace
from tools.results import ToolFailure
from tools.process_io import run_shell_bounded


TERMINAL_MAX_CHARS = 20_000


@tool
def terminal(command: str, timeout: int = 30, cwd: str = ".") -> str:
    """Run a shell command and return its output.

    Args:
        command: Shell command to execute.
        timeout: Timeout in seconds; defaults to 30.
        cwd: Working directory within an allowed workspace; defaults to the Agent working directory.
    """
    try:
        workdir = _resolve(cwd)
        if not _is_within_workspace(workdir):
            return _workspace_error(workdir)
        if not workdir.exists() or not workdir.is_dir():
            return ToolFailure(f"[Error] cwd is not a valid directory: {workdir}")
        unsafe = _terminal_workspace_violation(command)
        if unsafe:
            return unsafe
        timeout = max(1, min(int(timeout), 120))
        result = run_shell_bounded(command, cwd=str(workdir), timeout=timeout)
        output = result.stdout.decode('utf-8', errors='replace')
        stderr = result.stderr.decode('utf-8', errors='replace')
        # Give each populated pipe a share so verbose stdout cannot hide errors.
        stdout_limit = TERMINAL_MAX_CHARS // 2 if output and stderr else TERMINAL_MAX_CHARS
        stderr_limit = TERMINAL_MAX_CHARS - min(len(output), stdout_limit)
        truncated = (
            len(output) > stdout_limit or len(stderr) > stderr_limit
            or result.stdout_bytes > len(result.stdout) or result.stderr_bytes > len(result.stderr)
        )
        output = output[:stdout_limit]
        stderr = stderr[:stderr_limit]
        if stderr:
            output += (f"\n[stderr]\n{stderr}" if output else stderr)
        if truncated:
            output += (f'\n... (Output truncated; read {result.stdout_bytes} bytes from stdout, '
                       f'{result.stderr_bytes} bytes from stderr)')
        if result.timed_out:
            return ToolFailure(f"[Timeout] Command did not complete within {timeout} seconds" + (f'\n{output}' if output else ''))
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        output = output.strip()
        if result.returncode != 0:
            return ToolFailure(output)
        return output or "(No output)"
    except Exception as e:
        return ToolFailure(f"[Execution failed] {type(e).__name__}: {e}")


def _terminal_workspace_violation(command: str) -> str:
    """Reject obvious command references outside configured workspace roots.

    This is a guardrail, not a full shell sandbox. The explicit workspace
    setting allow_unsafe_terminal bypasses it for trusted local development.
    """
    if current_workspace().allow_unsafe_terminal:
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
    # URI slashes are not local filesystem arguments. Mask complete URI spans
    # before the fallback scan so https://host/path is not read as /host/path.
    def mask_uri(match: re.Match[str]) -> str:
        uri = urlsplit(match.group())
        if uri.scheme.lower() == "file":
            tokens.append(unquote(uri.path))
        return ""

    path_text = re.sub(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s;&|<>`'\"]+", mask_uri, command)
    tokens.extend(re.findall(r"(?<![\w:/])(?:~|/)[^\s;&|<>`'\"]+", path_text))
    return [
        token.rstrip("),.]")
        for token in tokens
        if token not in ("/", "~") and not _looks_like_option(token)
    ]


def _looks_like_option(token: str) -> bool:
    return token.startswith("-") or token.startswith("//")
