"""Tool 注册表。

负责工具的注册、删除、调用，以及把 ToolCall 转成字符串用于拼接到 message。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from models import ToolCall
from tools.audit import ToolAuditLog
from tools.decorator import ToolFunction
from tools.permissions import ToolPermission


class ToolRegistryError(RuntimeError):
    """ToolRegistry 相关异常。"""


class ToolCallError:
    """工具调用失败的结果（不抛异常，避免崩会话）。

    作为 call / call_tool_call 的返回值，调用方可通过 isinstance 检查
    是否调用失败。

    属性：
        name:    工具名
        message: 失败原因
        args:    调用时传入的参数（dict 或原始值）
    """

    def __init__(self, name: str, message: str, args: Any = None) -> None:
        self.name = name
        self.message = message
        self.args = args

    def __bool__(self) -> bool:
        # 始终为 False，方便 `if not result:` 判断失败
        return False

    def __str__(self) -> str:
        return f"[ToolCallError] {self.name}: {self.message}"

    def __repr__(self) -> str:
        return f"ToolCallError(name={self.name!r}, message={self.message!r})"


class ToolRegistry:
    """工具注册表。

    - register:   注册工具（支持 ToolFunction 或普通 function）
    - unregister: 删除工具
    - call:       按名调用工具
    - tool_call_to_str: 把 ToolCall 转成字符串，便于拼接到 message
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolFunction] = {}
        self._permissions: dict[str, ToolPermission] = {}
        self.tool_audit_log = ToolAuditLog()

    # ----- 注册 -----

    def register(
        self,
        tool_or_func: ToolFunction | Callable[..., Any],
        *,
        permission: Any = None,
        name: str | None = None,
    ) -> ToolFunction:
        """注册一个工具。

        支持两种入参：
            1. 已用 @tool 装饰的 ToolFunction
            2. 普通 Python function（自动包装为 ToolFunction）

        参数：
            tool_or_func: 工具对象或普通函数
            permission:   权限/side-effect metadata（默认不拦截调用）
            name:         自定义工具名（默认取函数名）

        返回：
            注册后的 ToolFunction 对象
        """
        if isinstance(tool_or_func, ToolFunction):
            tf = tool_or_func
        elif callable(tool_or_func):
            tf = ToolFunction(tool_or_func)
        else:
            raise ToolRegistryError(
                f"不支持注册的类型: {type(tool_or_func)}，"
                "需要 ToolFunction 或普通函数"
            )

        tool_name = name or tf.name
        if tool_name in self._tools:
            raise ToolRegistryError(f"工具已存在: {tool_name}")

        self._tools[tool_name] = tf
        self._permissions[tool_name] = ToolPermission.from_value(permission)
        return tf

    # ----- 删除 -----

    def unregister(self, name: str) -> ToolFunction:
        """删除工具，返回被移除的 ToolFunction。"""
        if name not in self._tools:
            raise ToolRegistryError(f"工具不存在: {name}")
        tf = self._tools.pop(name)
        self._permissions.pop(name, None)
        return tf

    # ----- 查询 -----

    def get(self, name: str) -> ToolFunction:
        """获取工具。"""
        if name not in self._tools:
            raise ToolRegistryError(f"工具不存在: {name}")
        return self._tools[name]

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """把所有已注册工具转成 OpenAI 接口的 tools 列表。"""
        return [tf.to_openai_tool() for tf in self._tools.values()]

    # ----- 调用 -----

    def call(self, name: str, **kwargs: Any) -> Any:
        """按名调用工具。

        异常会被捕获并转为 ToolCallError 返回，避免错误的 tool call
        把会话崩掉。调用方可通过 isinstance(result, ToolCallError)
        或检查返回值类型来判断是否调用失败。

        权限决策由 ToolRuntime 负责；Registry 保持纯调用和兼容兜底。
        """
        try:
            tf = self.get(name)
        except ToolRegistryError as e:
            return ToolCallError(name=name, message=str(e), args=kwargs)
        try:
            return tf(**kwargs)
        except TypeError as e:
            # 参数不匹配：缺参数 / 多余参数 / 类型不符等
            return ToolCallError(
                name=name, message=f"参数错误: {e}", args=kwargs,
            )
        except Exception as e:
            # 工具内部抛出的其他异常
            return ToolCallError(
                name=name, message=f"执行失败: {type(e).__name__}: {e}", args=kwargs,
            )

    def call_tool_call(self, tool_call: ToolCall) -> Any:
        """根据 ToolCall 对象调用工具。

        ToolCall 的 arguments 可以是 dict 或 JSON 字符串，会自动解析。
        任何异常（JSON 解析、参数错误、工具内部异常）都会被捕获并返回
        ToolCallError，不会抛出。
        """
        args = tool_call.arguments
        if isinstance(args, str):
            try:
                args = json.loads(args) if args else {}
            except json.JSONDecodeError as e:
                return ToolCallError(
                    name=tool_call.name,
                    message=f"参数不是合法 JSON: {args!r} ({e})",
                    args=tool_call.arguments,
                )
        if not isinstance(args, dict):
            return ToolCallError(
                name=tool_call.name,
                message=f"参数必须是 dict 或 JSON 字符串，实际类型: {type(args).__name__}",
                args=args,
            )
        return self.call(tool_call.name, **args)

    # ----- ToolCall 转字符串 -----

    def tool_call_to_str(self, tool_call: ToolCall) -> str:
        """把 ToolCall 转成字符串，便于拼接到 message。

        格式示例：
            get_weather(city="北京")
            search(query="hello", limit=5)

        若工具未注册，退化为：
            get_weather({"city": "北京"})
        """
        name = tool_call.name
        args = tool_call.arguments
        if isinstance(args, str):
            try:
                args = json.loads(args) if args else {}
            except json.JSONDecodeError:
                args = {"_raw": args}

        # 尝试用注册表的签名格式化参数
        if name in self._tools:
            tf = self._tools[name]
            return _format_call(name, args, tf)
        # 工具未注册，退化为 JSON 形式
        return _format_call_raw(name, args)

    # ----- 权限（留坑） -----

    def set_permission(self, name: str, permission: Any) -> None:
        """设置工具权限 metadata（默认策略不拦截调用）。"""
        if name not in self._tools:
            raise ToolRegistryError(f"工具不存在: {name}")
        self._permissions[name] = ToolPermission.from_value(permission)

    def get_permission(self, name: str) -> ToolPermission | None:
        return self._permissions.get(name)

    def tool_metadata(self, name: str) -> ToolPermission | None:
        """Return read-only metadata recorded for a tool."""
        return self.get_permission(name)

    def tool_metadata_map(self) -> dict[str, ToolPermission]:
        """Return a shallow copy of all registered tool metadata."""
        return dict(self._permissions)

    def tool_permissions(self) -> dict[str, ToolPermission]:
        """Return a shallow copy of legacy permission metadata."""
        return self.tool_metadata_map()


def _format_call(name: str, args: dict[str, Any], tf: ToolFunction) -> str:
    """按函数签名格式化为 name(k=v, ...) 形式。"""
    import inspect

    sig = inspect.signature(tf.func)
    parts: list[str] = []
    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        if pname in args:
            parts.append(f"{pname}={_repr_value(args[pname])}")
    return f"{name}({', '.join(parts)})"


def _format_call_raw(name: str, args: dict[str, Any]) -> str:
    """工具未注册时退化的格式化。"""
    parts = [f"{k}={_repr_value(v)}" for k, v in args.items()]
    return f"{name}({', '.join(parts)})"


def _repr_value(v: Any) -> str:
    """值的可读表示：字符串加引号，其他用 repr。"""
    if isinstance(v, str):
        return f'"{v}"'
    return repr(v)
