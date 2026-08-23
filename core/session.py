"""会话管理。

Session 是 list[Message] 的高级封装，管理两类消息：
    1. history：有上限的审计历史（不直接决定 LLM 上下文）
    2. active：当前活跃的消息列表（进入 LLM 调用）

后续可在 Session 上做上下文压缩、消息裁剪等高级操作。
"""

from __future__ import annotations

from typing import Any

from core.config import MASConfig
from models import Message, System


class Session:
    """会话容器。

    属性：
        history: 有上限的审计历史，记录近期消息，不会直接发给 LLM。
        active:  当前活跃消息列表，是实际进入 LLM 调用的内容。
                 可以裁剪、压缩，而不影响 history 的完整性。
    """

    def __init__(self, history_limit: int | None = MASConfig.HISTORY_MESSAGE_LIMIT) -> None:
        self.history: list[Message] = []
        self.active: list[Message] = []
        self.history_limit = history_limit

    # ----- 基本操作 -----

    def add(self, message: Message, *, to_active: bool = True) -> None:
        """添加一条消息。

        参数：
            message:   要添加的消息
            to_active: 是否同时加入 active 列表。
                       True（默认）：history 和 active 都加。
                       False：只加到 history，不进入 LLM。
        """
        self.history.append(message)
        self.prune_history(self.history_limit)
        if to_active:
            self.active.append(message)

    def add_many(self, messages: list[Message], *, to_active: bool = True) -> None:
        """批量添加消息。"""
        for m in messages:
            self.add(m, to_active=to_active)

    # ----- active 管理 -----

    def clear_active(self) -> None:
        """清空 active 列表（history 不受影响）。"""
        self.active.clear()

    def reset_active_from_history(self) -> None:
        """用 history 重建 active（全部历史进入 active）。"""
        self.active = list(self.history)

    # ----- 查询 -----

    def active_messages(self) -> list[Message]:
        """返回当前进入 LLM 的消息列表。"""
        return self.active

    def prune_active(self, max_messages: int | None, *, keep_system: bool = True) -> None:
        """Trim active context to the latest messages.

        `history` is left untouched. This is intentionally message-count based
        rather than token based so it stays dependency-free and predictable.
        """
        if max_messages is None or max_messages <= 0:
            return
        if len(self.active) <= max_messages:
            return
        system_msg: Message | None = None
        rest = self.active
        if keep_system:
            system_msg = next((m for m in self.active if isinstance(m, System)), None)
            rest = [m for m in self.active if m is not system_msg]
        keep_count = max_messages - (1 if system_msg is not None else 0)
        keep_count = max(0, keep_count)
        trimmed = rest[-keep_count:] if keep_count else []
        while trimmed and trimmed[0].role == "tool":
            trimmed.pop(0)
        self.active = ([system_msg] if system_msg is not None else []) + trimmed

    def history_messages(self) -> list[Message]:
        """返回当前保留的审计历史消息列表。"""
        return self.history

    def prune_history(self, max_messages: int | None, *, keep_system: bool = True) -> None:
        """Trim stored history without changing active context."""
        if max_messages is None or max_messages <= 0:
            return
        if len(self.history) <= max_messages:
            return
        system_msg: Message | None = None
        rest = self.history
        if keep_system:
            system_msg = next((m for m in self.history if isinstance(m, System)), None)
            rest = [m for m in self.history if m is not system_msg]
        keep_count = max_messages - (1 if system_msg is not None else 0)
        keep_count = max(0, keep_count)
        trimmed = rest[-keep_count:] if keep_count else []
        self.history = ([system_msg] if system_msg is not None else []) + trimmed

    def __len__(self) -> int:
        """history 的长度。"""
        return len(self.history)

    def __repr__(self) -> str:
        return (
            f"Session(history={len(self.history)}, active={len(self.active)})"
        )
