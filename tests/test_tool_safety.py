import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.builtin import terminal
from tools.audit import ToolAuditLog, audit_sink_from_path
from tools.file_ops import read_file
from tools.permissions import ToolPermission


def _set_workspace(value: str) -> str | None:
    old = os.environ.get("MAS_WORKSPACE_ROOTS")
    os.environ["MAS_WORKSPACE_ROOTS"] = value
    return old


def _restore_workspace(value: str | None) -> None:
    if value is None:
        os.environ.pop("MAS_WORKSPACE_ROOTS", None)
    else:
        os.environ["MAS_WORKSPACE_ROOTS"] = value


def test_terminal_rejects_cwd_outside_workspace() -> None:
    old = _set_workspace(str(Path.cwd()))
    try:
        result = terminal("pwd", cwd="/")

        assert "路径不在允许的工作区内" in result
    finally:
        _restore_workspace(old)


def test_terminal_rejects_obvious_absolute_path_outside_workspace() -> None:
    old = _set_workspace(str(Path.cwd()))
    try:
        result = terminal("cat /etc/passwd")

        assert "路径不在允许的工作区内" in result
    finally:
        _restore_workspace(old)


def test_read_file_truncates_large_output() -> None:
    old_workspace = _set_workspace(str(Path.cwd()))
    old_limit = os.environ.get("MAS_READ_FILE_MAX_CHARS")
    os.environ["MAS_READ_FILE_MAX_CHARS"] = "80"
    path = Path("tmp_read_limit_test.txt")
    try:
        path.write_text("\n".join(f"line {idx}" for idx in range(50)), encoding="utf-8")

        result = read_file(str(path), n_lines=50)

        assert len(result) < 180
        assert "输出已截断" in result
        assert "line_offset/n_lines" in result
    finally:
        if path.exists():
            path.unlink()
        if old_limit is None:
            os.environ.pop("MAS_READ_FILE_MAX_CHARS", None)
        else:
            os.environ["MAS_READ_FILE_MAX_CHARS"] = old_limit
        _restore_workspace(old_workspace)


def test_tool_audit_jsonl_sink_appends_records(tmp_path) -> None:
    path = tmp_path / "audit" / "tools.jsonl"
    log = ToolAuditLog(sink=audit_sink_from_path(path))

    log.append(
        tool="terminal",
        caller="A",
        permission=ToolPermission(side_effect="external_effect"),
        arguments={"cmd": "pwd"},
        result="ok",
        error=False,
    )

    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert '"tool": "terminal"' in content
    assert '"side_effect": "external_effect"' in content
    assert '"decision": {"allowed": true' in content


if __name__ == "__main__":
    test_terminal_rejects_cwd_outside_workspace()
    test_terminal_rejects_obvious_absolute_path_outside_workspace()
    test_read_file_truncates_large_output()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        test_tool_audit_jsonl_sink_appends_records(Path(tmp))
