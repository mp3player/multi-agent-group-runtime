"""Runtime helpers for executing registered tools."""

from __future__ import annotations

from models import Message, ToolCall
from tools.audit import ToolAuditLog
from tools.file_ops import workspace_roots_context, workspace_roots_from_value
from tools.permissions import (
    ToolPermission,
    ToolPermissionDecision,
    ToolPermissionPolicy,
)
from tools.registry import ToolCallError, ToolRegistry


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
    ) -> None:
        self.registry = registry
        self.audit_log = audit_log or registry.tool_audit_log
        self.permission_policy = permission_policy or ToolPermissionPolicy()
        self.caller = caller
        self.workspace_roots = workspace_roots_from_value(workspace_roots)

    def execute(self, tool_calls: list[ToolCall]) -> list[Message]:
        """Execute tool calls and return role=tool result messages."""
        results: list[Message] = []
        for tool_call in tool_calls:
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
                with workspace_roots_context(self.workspace_roots):
                    result = self.registry.call_tool_call(tool_call)
            else:
                result = ToolCallError(
                    name=tool_call.name,
                    message=f"权限拒绝: {decision.reason}",
                    args=tool_call.arguments,
                )
            formatted = self.format_result(result)
            self._record_audit(tool_call, result, formatted, permission, decision)
            tool_msg = Message(role="tool", message=formatted)
            tool_msg.tool_call_id = tool_call.id  # type: ignore[attr-defined]
            results.append(tool_msg)
        return results

    @staticmethod
    def format_result(result: object) -> str:
        """Format one raw tool result for a role=tool message."""
        if isinstance(result, ToolCallError):
            return str(result)
        return str(result) if result is not None else "(无返回值)"

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
            error=isinstance(result, ToolCallError)
            or not decision.allowed,
        )


def should_stop_after_tool_calls(tool_calls: list[ToolCall]) -> bool:
    """Return True when a tool call intentionally ends the current run."""
    return any(tool_call.name == "group_pass" for tool_call in tool_calls)
