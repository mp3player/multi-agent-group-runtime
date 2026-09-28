"""Tool effects, failures and workspace selection through the real runtime."""

from pathlib import Path

import pytest

from models import ToolCall
from tools import ToolRegistry, ToolRuntime, read_file, write_file, str_replace, terminal
from tools.permissions import ToolPermission, ToolPermissionPolicy


def test_tool_can_accept_name_as_an_ordinary_argument():
    def greet(name: str) -> str:
        return f'Hello {name}'

    registry = ToolRegistry()
    registry.register(greet)
    result = ToolRuntime(registry).execute([ToolCall('greet-1', 'greet', {'name': 'Ada'})])[0]
    assert result.message == 'Hello Ada'
    assert result.tool_success
    assert result.tool_call_id == 'greet-1'
    assert not registry.tool_audit_log.records()[0].error


@pytest.mark.parametrize('failure', ['missing_file', 'outside_workspace', 'write_directory', 'missing_match', 'command_exit', 'command_timeout'])
def test_builtin_failures_are_audited_and_do_not_end_runs(tmp_path, failure):
    registry = ToolRegistry()
    text = tmp_path / 'text.txt'
    text.write_text('original')
    cases = {
        'missing_file': (read_file, {'path': str(tmp_path / 'absent.txt')}, 'File does not exist'),
        'outside_workspace': (write_file, {'path': str(tmp_path.parent / 'outside.txt'), 'content': 'forbidden'}, 'Path is outside the allowed workspace'),
        'write_directory': (write_file, {'path': str(tmp_path), 'content': 'invalid'}, 'IsADirectoryError'),
        'missing_match': (str_replace, {'path': str(text), 'old': 'missing', 'new': 'replacement'}, 'No matching string found'),
        'command_exit': (terminal, {'command': 'exit 7', 'cwd': str(tmp_path)}, 'exit code: 7'),
        'command_timeout': (terminal, {'command': 'exec sleep 2', 'timeout': 1, 'cwd': str(tmp_path)}, '[Timeout]'),
    }
    tool, arguments, error_text = cases[failure]
    registry.register(tool, ends_run=True)
    result = ToolRuntime(registry, workspace_roots=(str(tmp_path),)).execute([
        ToolCall('failed-1', tool.name, arguments),
    ])[0]
    assert error_text in result.message
    assert result.tool_success is False
    assert result.ends_run is False
    record = registry.tool_audit_log.records()[0]
    assert record.error is True
    assert text.read_text() == 'original'
    assert not (tmp_path.parent / 'outside.txt').exists()


def test_successful_output_containing_error_text_is_not_a_failure(tmp_path):
    (tmp_path / 'error.txt').write_text('[Error] this is file content')
    registry = ToolRegistry()
    registry.register(read_file, ends_run=True)
    result = ToolRuntime(registry, workspace_roots=(str(tmp_path),)).execute([
        ToolCall('read-1', 'read_file', {'path': str(tmp_path / 'error.txt')}),
    ])[0]
    assert '[Error] this is file content' in result.message
    assert result.tool_success and result.ends_run
    assert not registry.tool_audit_log.records()[0].error


def test_relative_paths_stay_with_each_runtime_after_process_cwd_changes(tmp_path, monkeypatch):
    launch = tmp_path / 'launch'
    first, second, other = launch / 'first', launch / 'second', tmp_path / 'other'
    for directory in (first, second, other):
        directory.mkdir(parents=True)
    monkeypatch.chdir(launch)
    runtimes = []
    for folder in ('first', 'second'):
        registry = ToolRegistry()
        registry.register(write_file)
        registry.register(read_file)
        registry.register(terminal)
        runtimes.append(ToolRuntime(registry, workspace_roots=(folder,)))
    monkeypatch.chdir(other)
    for runtime, want, folder in zip(runtimes, ('first contents', 'second contents'), (first, second)):
        results = runtime.execute([
            ToolCall('write', 'write_file', {'path': 'result.txt', 'content': want}),
            ToolCall('read', 'read_file', {'path': 'result.txt'}),
            ToolCall('cwd', 'terminal', {'command': 'pwd'}),
        ])
        assert all(result.tool_success for result in results)
        assert want in results[1].message
        assert results[2].message == str(folder)
        assert Path.cwd() == other
    assert (first / 'result.txt').read_text() == 'first contents'
    assert (second / 'result.txt').read_text() == 'second contents'
    assert not (other / 'result.txt').exists()


@pytest.mark.parametrize('rule,want_mode,want_approval', [('', 'approval', True), ('external_effect=allow', 'approval', True), ('external_effect=deny', 'deny', False)])
@pytest.mark.parametrize('enforce', [False, True])
def test_explicit_tool_approval_is_enforced_and_audited(tmp_path, rule, want_mode, want_approval, enforce):
    registry = ToolRegistry()
    registry.register(write_file, permission=ToolPermission(side_effect='external_effect', requires_approval=True))
    runtime = ToolRuntime(registry, workspace_roots=(str(tmp_path),),
                          permission_policy=ToolPermissionPolicy.from_config(enforce=enforce, rules=rule))
    result = runtime.execute([ToolCall('write-1', 'write_file', {'path': str(tmp_path / 'written.txt'), 'content': 'written'})])[0]
    record = registry.tool_audit_log.records()[0]
    assert (tmp_path / 'written.txt').exists() is (not enforce)
    assert result.tool_success is (not enforce)
    assert record.error is enforce
    assert record.decision.mode == want_mode
    assert record.decision.requires_approval is want_approval


def test_network_uri_is_not_mistaken_for_a_local_path(tmp_path):
    registry = ToolRegistry()
    registry.register(terminal)
    runtime = ToolRuntime(registry, workspace_roots=(str(tmp_path),))
    result = runtime.execute([ToolCall('url', 'terminal', {'command': "printf '%s' 'https://example.com/v1'", 'cwd': str(tmp_path)})])[0]
    assert result.tool_success
    assert result.message == 'https://example.com/v1'


@pytest.mark.parametrize('host', ['', 'localhost'])
def test_local_file_uri_still_obeys_workspace_guard(tmp_path, host):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'private data.txt'
    outside.write_text('outside content')
    encoded_path = str(outside).replace(' ', '%20')
    registry = ToolRegistry()
    registry.register(terminal)
    result = ToolRuntime(registry, workspace_roots=(str(workspace),)).execute([
        ToolCall('uri', 'terminal', {'command': f"printf '%s' 'file://{host}{encoded_path}'", 'cwd': str(workspace)}),
    ])[0]
    assert not result.tool_success
    assert 'Path is outside the allowed workspace' in result.message
    assert registry.tool_audit_log.records()[0].error
