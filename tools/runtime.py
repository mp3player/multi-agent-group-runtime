"""Runtime helpers for executing registered tools."""

from __future__ import annotations

from dataclasses import replace

from models import Message, ToolCall
from tools.audit import ToolAuditLog
from tools.workspace import Workspace, workspace_context, workspace_roots_from_value
from tools.permissions import (
    ToolPermission,
    ToolPermissionDecision,
    ToolPermissionPolicy,
)
from tools.registry import ToolCallError, ToolRegistry
from tools.results import ToolFailure

# Leave room for the built-in 64 Ki-character file page's continuation hint.
DEFAULT_MAX_RESULT_CHARS = 65 * 1024


class ToolRuntime:
    """Execute tool calls and convert results into tool messages."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        audit_log: ToolAuditLog | None = None,
        permission_policy: ToolPermissionPolicy | None = None,
        caller: str = "",
        workspace_roots: tuple[str, ...] | list[str] | str = (),
        workspace: Workspace | None = None,
        max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
    ) -> None:
        if type(max_result_chars) is not int or max_result_chars < 64:
            raise ValueError('max_result_chars must be an integer of at least 64')
        self.max_result_chars = max_result_chars
        self.registry = registry
        self.audit_log = audit_log or registry.tool_audit_log
        self.permission_policy = permission_policy or ToolPermissionPolicy()
        self.caller = caller
        if workspace is not None and workspace_roots_from_value(workspace_roots):
            raise ValueError('use workspace or workspace_roots, not both')
        self.workspace = workspace if workspace is not None else Workspace(
            roots=workspace_roots_from_value(workspace_roots))

    @property
    def workspace_roots(self) -> tuple[str, ...]:
        return self.workspace.roots

    @workspace_roots.setter
    def workspace_roots(self, value: tuple[str, ...] | list[str] | str) -> None:
        self.workspace = replace(self.workspace, roots=workspace_roots_from_value(value), cwd=None)

    def set_registry(self, registry: ToolRegistry) -> None:
        """Replace tools while preserving execution settings and audit routing."""
        if self.audit_log.sink is not None:
            registry.tool_audit_log.set_sink(self.audit_log.sink)
        self.registry = registry
        self.audit_log = registry.tool_audit_log

    def execute(self, tool_calls: list[ToolCall]) -> list[Message]:
        """Execute tool calls and return role=tool result messages."""
        results: list[Message] = []
        for tool_call in tool_calls:
            terminal = self.registry.ends_run(tool_call.name)
            permission = (
                self.registry.tool_metadata(tool_call.name)
                or ToolPermission()
            )
            decision = self.permission_policy.check(
                tool_name=tool_call.name,
                permission=permission,
                caller=self.caller,
                arguments=tool_call.arguments,
            )
            if decision.allowed:
                with workspace_context(self.workspace):
                    result = self.registry.call_tool_call(tool_call)
            else:
                result = ToolCallError(
                    name=tool_call.name,
                    message=f"Permission denied: {decision.reason}",
                    args=tool_call.arguments,
                )
            formatted = self.format_result(result, max_chars=self.max_result_chars)
            self._record_audit(tool_call, result, formatted, permission, decision)
            tool_msg = Message(role="tool", message=formatted)
            tool_msg.tool_call_id = tool_call.id  # type: ignore[attr-defined]
            tool_msg.tool_success = decision.allowed and not isinstance(result, (ToolCallError, ToolFailure))
            tool_msg.ends_run = terminal and tool_msg.tool_success
            results.append(tool_msg)
        return results

    @staticmethod
    def format_result(result: object, *, max_chars: int = DEFAULT_MAX_RESULT_CHARS) -> str:
        """Bound retained results and escape text invalid in UTF-8 requests."""
        text = str(result) if result is not None else "(No return value)"
        truncated = len(text) > max_chars
        # Slice before encoding: a large plugin result must not make another
        # equally large allocation just to sanitize malformed Unicode.
        text = text[:max_chars].encode('utf-8', errors='backslashreplace').decode('utf-8')
        if truncated or len(text) > max_chars:
            marker = '\n[tool result truncated]'
            text = text[:max_chars - len(marker)] + marker
        return text

    def _record_audit(
        self,
        tool_call: ToolCall,
        result: object,
        formatted: str,
        permission: ToolPermission,
        decision: ToolPermissionDecision,
    ) -> None:
        self.audit_log.append(
            tool=tool_call.name,
            caller=self.caller,
            permission=permission,
            decision=decision,
            arguments=tool_call.arguments,
            result=formatted,
            error=isinstance(result, (ToolCallError, ToolFailure))
            or not decision.allowed,
        )


def should_stop_after_tool_calls(results: list[Message]) -> bool:
    """Return True when a tool call intentionally ends the current run."""
    return any(getattr(result, "tool_success", False) and getattr(result, "ends_run", False)
               for result in results)
