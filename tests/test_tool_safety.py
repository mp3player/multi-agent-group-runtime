import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.builtin import terminal
from tools.audit import ToolAuditLog, audit_sink_from_path
from tools.file_ops import read_file
from tools.permissions import ToolPermission
from tools.workspace import Workspace, workspace_context


def test_terminal_rejects_cwd_outside_workspace() -> None:
    with workspace_context(Workspace(roots=(str(Path.cwd()),))):
        result = terminal("pwd", cwd="/")

        assert "Path is outside the allowed workspace" in result


def test_terminal_rejects_obvious_absolute_path_outside_workspace() -> None:
    with workspace_context(Workspace(roots=(str(Path.cwd()),))):
        result = terminal("cat /etc/passwd")

        assert "Path is outside the allowed workspace" in result


def test_read_file_truncates_large_output() -> None:
    path = Path("tmp_read_limit_test.txt")
    try:
        path.write_text("\n".join(f"line {idx}" for idx in range(50)), encoding="utf-8")

        with workspace_context(Workspace(roots=(str(Path.cwd()),), read_file_max_chars=80)):
            result = read_file(str(path), n_lines=50)

        assert len(result) < 180
        assert "Output truncated" in result
        assert "line_offset/n_lines" in result
    finally:
        if path.exists():
            path.unlink()


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
