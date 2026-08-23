"""Group tool handler implementations."""

from __future__ import annotations

from dataclasses import dataclass

from domain.group import GroupMember, GroupMessage
from core.group_runtime.ports import GroupToolRuntimePort
from core.group_tool_specs import GROUP_TOOL_EFFECTS


@dataclass(slots=True)
class GroupToolHandlers:
    """Bound group tool handlers for one member."""

    group: GroupToolRuntimePort
    member: GroupMember

    def record(self, name: str) -> None:
        """Record one group tool call."""
        self.group.tool_record_call(
            self.member.name,
            name,
            GROUP_TOOL_EFFECTS[name],
        )

    def group_status(self) -> str:
        """查询当前群组成员状态、未读数量和最近群聊消息。"""
        self.record("group_status")
        lines = [f"群组: {self.group.name}", "成员:"]
        lines.extend(self.group.tool_member_status_lines())
        lines.append("最近消息:")
        recent = self.group.tool_recent_messages(8)
        if recent:
            lines.extend(msg.render() for msg in recent)
        else:
            lines.append("(暂无群聊消息)")
        lines.append("群组记忆摘要:")
        lines.append(self.group.tool_memory_summary())
        return "\n".join(lines)

    def group_send(self, message: str) -> str:
        """向群组发送一条普通可传播消息。

        Args:
            message: 要记录到群组的消息。文本中的成员名只作为普通文本，不触发定向调度。
        """
        self.record("group_send")
        message = message.strip()
        if not message:
            return "[错误] message 不能为空"
        msg = self.group.tool_add_message(
            self.member.name,
            message,
            kind="agent",
            propagate=True,
        )
        return f"(已发送群组消息 #{msg.id})"

    def group_broadcast(self, message: str) -> str:
        """显式广播一条群消息，让所有可用成员从各自角色独立判断并响应。

        Args:
            message: 要广播给所有成员的消息。需要各成员审阅、评论、
                报告或补充时，明确写出期望的输出。
        """
        self.record("group_broadcast")
        message = message.strip()
        if not message:
            return "[错误] message 不能为空"
        msg = self.group.tool_add_message(
            self.member.name,
            "\n".join([
                "Explicit broadcast to all members.",
                "Each member should respond from their own role if they can add "
                "material input; use PASS only when there is genuinely nothing "
                "useful to add.",
                "",
                message,
            ]),
            kind="agent",
            propagate=True,
            dispatch_mode="broadcast",
        )
        return f"(已广播群组消息 #{msg.id})"

    def normalize_targets(self, to: list[str]) -> tuple[list[str], str | None]:
        """Validate and deduplicate directed tool targets."""
        if not isinstance(to, list) or not to:
            return [], "[错误] to 必须是非空成员名数组"
        targets: list[str] = []
        for name in to:
            if not isinstance(name, str):
                return [], "[错误] to 只能包含成员名字符串"
            name = name.strip()
            if not self.group.tool_has_member(name):
                return [], f"[错误] 成员不存在: {name}"
            if not self.group.tool_member_enabled(name):
                return [], f"[错误] 成员已禁用: {name}"
            targets.append(name)
        return list(dict.fromkeys(targets)), None

    def add_directed_message(
        self,
        to: list[str],
        message: str,
    ) -> tuple[str, GroupMessage | None]:
        """Add a propagating directed group message."""
        message = message.strip()
        if not message:
            return "[错误] message 不能为空", None
        targets, error = self.normalize_targets(to)
        if error is not None:
            return error, None
        prefix = f"Directed to: {', '.join(targets)}"
        msg = self.group.tool_add_message(
            self.member.name,
            f"{prefix}\n{message}",
            kind="agent",
            mentions=targets,
            propagate=True,
        )
        return f"(已定向发送给 {', '.join(targets)}，消息 #{msg.id})", msg

    def group_direct(self, to: list[str], message: str) -> str:
        """向指定成员发送定向群消息，并只唤醒这些成员。

        Args:
            to: 目标成员名数组
            message: 发送给目标成员的消息
        """
        self.record("group_direct")
        result, _ = self.add_directed_message(to, message)
        return result

    def group_handoff(self, to: list[str], summary: str, next: str, files: str = "") -> str:
        """交接工作给指定成员，更新群组记忆，并只唤醒这些成员。

        Args:
            to: 接手成员名数组
            summary: 当前成员已完成的内容
            next: 接手成员下一步要做的具体事项
            files: 可选，涉及文件或目录
        """
        self.record("group_handoff")
        summary = summary.strip()
        next = next.strip()
        files = files.strip()
        if not summary:
            return "[错误] summary 不能为空"
        if not next:
            return "[错误] next 不能为空"
        targets, error = self.normalize_targets(to)
        if error is not None:
            return error
        self.group.tool_update_memory(
            "done",
            f"- {self.member.name}: {summary}",
            append=True,
            actor=self.member.name,
        )
        if files:
            self.group.tool_update_memory(
                "scopes",
                f"- {self.member.name}: {files}",
                append=True,
                actor=self.member.name,
            )
        prefix = f"Directed to: {', '.join(targets)}"
        msg = self.group.tool_add_message(
            self.member.name,
            "\n".join([
                prefix,
                f"Handoff summary: {summary}",
                f"Next: {next}",
                *([f"Files: {files}"] if files else []),
            ]),
            kind="agent",
            mentions=targets,
            propagate=True,
        )
        self.group.tool_update_memory(
            "next",
            f"- {self.member.name} -> {', '.join(targets)}: {next}",
            append=True,
            actor=self.member.name,
        )
        return f"(已交接给 {', '.join(targets)}，消息 #{msg.id})"

    def group_pass(self) -> str:
        """表示当前没有需要补充或接续推进的内容。"""
        self.record("group_pass")
        turn_start_id = self.group.tool_turn_start_message_id(self.member.name)
        if (
            turn_start_id is not None
            and self.group.tool_member_messages_after(self.member.name, turn_start_id)
        ):
            return "(本轮已有群组消息，忽略 PASS)"
        msg = self.group.tool_add_message(
            self.member.name,
            "PASS",
            kind="agent",
            propagate=False,
        )
        return f"(已记录 PASS #{msg.id}，不会触发其他成员未读)"

    def group_memory_get(self) -> str:
        """读取完整 group memory。"""
        self.record("group_memory_get")
        return self.group.tool_memory_full(include_empty=True)

    def group_memory_update(self, section: str, content: str) -> str:
        """覆盖更新一个 group memory section，不发送群消息。

        Args:
            section: goal/plan/scopes/decisions/done/next/risks 之一
            content: 新内容
        """
        self.record("group_memory_update")
        return self.group.tool_update_memory(
            section,
            content,
            append=False,
            actor=self.member.name,
        )

    def group_memory_append(self, section: str, content: str) -> str:
        """追加更新一个 group memory section，不发送群消息。

        Args:
            section: goal/plan/scopes/decisions/done/next/risks 之一
            content: 要追加的内容
        """
        self.record("group_memory_append")
        return self.group.tool_update_memory(
            section,
            content,
            append=True,
            actor=self.member.name,
        )

    def group_memory_clear(self, section: str = "") -> str:
        """清空 group memory。section 为空时清空全部，不发送群消息。

        Args:
            section: 可选，goal/plan/scopes/decisions/done/next/risks 之一
        """
        self.record("group_memory_clear")
        return self.group.tool_clear_memory(section, actor=self.member.name)

    def group_note_scope(self, scope: str, files: str = "", next: str = "") -> str:
        """记录当前成员的工作范围，并发送一条可传播群消息。

        Args:
            scope: 当前要承担的具体范围
            files: 可选，涉及文件或目录
            next: 可选，后续待办或交接事项；需要唤醒特定成员时另用 group_handoff 或 group_direct
        """
        self.record("group_note_scope")
        scope = scope.strip()
        files = files.strip()
        next = next.strip()
        if not scope:
            return "[错误] scope 不能为空"
        memory_line = f"- {self.member.name}: {scope}"
        if files:
            memory_line += f" | files: {files}"
        self.group.tool_update_memory(
            "scopes",
            memory_line,
            append=True,
            actor=self.member.name,
        )
        if next:
            self.group.tool_update_memory(
                "next",
                f"- {self.member.name}: {next}",
                append=True,
                actor=self.member.name,
            )
        message = f"Scope: {scope}"
        if files:
            message += f"\nFiles: {files}"
        if next:
            message += f"\nNext: {next}"
        msg = self.group.tool_add_message(
            self.member.name,
            message,
            kind="agent",
            propagate=True,
        )
        return f"(已记录 scope 并发送群组消息 #{msg.id})"

    def group_done(self, summary: str, next: str = "") -> str:
        """记录完成事项，并发送一条可传播群消息。

        Args:
            summary: 已完成的具体内容
            next: 可选，后续待办或交接事项；需要唤醒特定成员时另用 group_handoff 或 group_direct
        """
        self.record("group_done")
        summary = summary.strip()
        next = next.strip()
        if not summary:
            return "[错误] summary 不能为空"
        self.group.tool_update_memory(
            "done",
            f"- {self.member.name}: {summary}",
            append=True,
            actor=self.member.name,
        )
        if next:
            self.group.tool_update_memory(
                "next",
                f"- {self.member.name}: {next}",
                append=True,
                actor=self.member.name,
            )
        message = f"Done: {summary}"
        if next:
            message += f"\nNext: {next}"
        msg = self.group.tool_add_message(
            self.member.name,
            message,
            kind="agent",
            propagate=True,
            dispatch_mode="feedback",
        )
        return f"(已记录 done 并发送群组消息 #{msg.id})"

    def group_report(self, summary: str, findings: str = "", next: str = "") -> str:
        """提交当前分工报告，并回流给广播发起者用于汇总。

        Args:
            summary: 报告摘要或结论
            findings: 可选，关键发现、证据或风险点
            next: 可选，建议的后续动作
        """
        self.record("group_report")
        summary = summary.strip()
        findings = findings.strip()
        next = next.strip()
        if not summary:
            return "[错误] summary 不能为空"
        self.group.tool_update_memory(
            "done",
            f"- {self.member.name}: {summary}",
            append=True,
            actor=self.member.name,
        )
        if next:
            self.group.tool_update_memory(
                "next",
                f"- {self.member.name}: {next}",
                append=True,
                actor=self.member.name,
            )
        message_parts = [f"Report: {summary}"]
        if findings:
            message_parts.append(f"Findings:\n{findings}")
        if next:
            message_parts.append(f"Next: {next}")
        msg = self.group.tool_add_message(
            self.member.name,
            "\n".join(message_parts),
            kind="agent",
            propagate=True,
            dispatch_mode="feedback",
        )
        return f"(已提交 report 并发送反馈消息 #{msg.id})"

    def group_decision(self, decision: str, reason: str = "") -> str:
        """记录协作决策，并发送一条可传播群消息。

        Args:
            decision: 已达成或建议采用的决策
            reason: 可选，简短原因
        """
        self.record("group_decision")
        decision = decision.strip()
        reason = reason.strip()
        if not decision:
            return "[错误] decision 不能为空"
        memory_line = f"- {decision}"
        if reason:
            memory_line += f" | reason: {reason}"
        self.group.tool_update_memory(
            "decisions",
            memory_line,
            append=True,
            actor=self.member.name,
        )
        message = f"Decision: {decision}"
        if reason:
            message += f"\nReason: {reason}"
        msg = self.group.tool_add_message(
            self.member.name,
            message,
            kind="agent",
            propagate=True,
        )
        return f"(已记录 decision 并发送群组消息 #{msg.id})"


__all__ = ["GroupToolHandlers"]
