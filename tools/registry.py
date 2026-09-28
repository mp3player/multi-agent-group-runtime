"""Tool registry.

Register, remove and invoke tools, and format ToolCall objects as message text.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Callable

from models import ToolCall
from tools.audit import ToolAuditLog
from tools.decorator import ToolFunction
from tools.permissions import ToolPermission


class ToolRegistryError(RuntimeError):
    """Exception raised by ToolRegistry."""


class ToolCallError:
    """A failed tool call returned as a value to keep the conversation running.

    Returned by call / call_tool_call. Callers can use isinstance to check
    whether the call failed.

    Attributes:
        name:    Tool name.
        message: Failure reason.
        args:    Arguments supplied to the call, as a dict or the original value.
    """

    def __init__(self, name: str, message: str, args: Any = None) -> None:
        self.name = name
        self.message = message
        self.args = args

    def __bool__(self) -> bool:
        # Always false so callers can detect failure with `if not result:`.
        return False

    def __str__(self) -> str:
        return f"[ToolCallError] {self.name}: {self.message}"

    def __repr__(self) -> str:
        return f"ToolCallError(name={self.name!r}, message={self.message!r})"


class ToolRegistry:
    """Registry of available tools.

    - register:   Register a ToolFunction or a plain function.
    - unregister: Remove a tool
    - call:       Call a tool by name
    - tool_call_to_str: Format a ToolCall as text for a message
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolFunction] = {}
        self._permissions: dict[str, ToolPermission] = {}
        self._terminal: dict[str, bool] = {}
        self.tool_audit_log = ToolAuditLog()

    # ----- Registration -----

    def register(
        self,
        tool_or_func: ToolFunction | Callable[..., Any],
        *,
        permission: Any = None,
        name: str | None = None,
        ends_run: bool = False,
    ) -> ToolFunction:
        """Register a tool.

        Accepts either:
            1. A ToolFunction created with the @tool decorator.
            2. A plain Python function, automatically wrapped as a ToolFunction.

        Args:
            tool_or_func: Tool object or plain function.
            permission:   Permission and side-effect metadata; calls are allowed by default.
            name:         Custom tool name; defaults to the function name.
            ends_run:     End the current Agent run after successful execution; defaults to False.

        Returns:
            The registered ToolFunction.
        """
        if isinstance(tool_or_func, ToolFunction):
            tf = tool_or_func
        elif callable(tool_or_func):
            tf = ToolFunction(tool_or_func)
        else:
            raise ToolRegistryError(
                f"Unsupported registration type: {type(tool_or_func)}; "
                "expected a ToolFunction or plain function"
            )

        tool_name = name or tf.name
        if tool_name in self._tools:
            raise ToolRegistryError(f"Tool already exists: {tool_name}")

        self._tools[tool_name] = tf
        self._terminal[tool_name] = ends_run
        self._permissions[tool_name] = ToolPermission.from_value(permission)
        return tf

    # ----- Removal -----

    def unregister(self, name: str) -> ToolFunction:
        """Remove a tool and return its ToolFunction."""
        if name not in self._tools:
            raise ToolRegistryError(f"Tool does not exist: {name}")
        tf = self._tools.pop(name)
        self._permissions.pop(name, None)
        self._terminal.pop(name, None)
        return tf

    # ----- Lookup -----

    def get(self, name: str) -> ToolFunction:
        """Get a tool."""
        if name not in self._tools:
            raise ToolRegistryError(f"Tool does not exist: {name}")
        return self._tools[name]

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """Convert all registered tools to the OpenAI tools schema."""
        schemas = []
        for name, tf in self._tools.items():
            schema = deepcopy(tf.to_openai_tool())
            schema["function"]["name"] = name
            schemas.append(schema)
        return schemas

    def ends_run(self, name: str) -> bool:
        """Whether a successful call to this registration terminates a run."""
        return self._terminal.get(name, False)

    # ----- Invocation -----

    def call(self, name: str, /, **kwargs: Any) -> Any:
        """Call a tool by name.

        Exceptions are caught and returned as ToolCallError values so a failed
        tool call does not terminate the conversation. Callers can check
        isinstance(result, ToolCallError) to detect failure.

        ToolRuntime handles permission decisions; the registry handles invocation and compatibility fallbacks.
        """
        try:
            tf = self.get(name)
        except ToolRegistryError as e:
            return ToolCallError(name=name, message=str(e), args=kwargs)
        try:
            return tf(**kwargs)
        except TypeError as e:
            # Argument mismatch: missing, unexpected or incompatible arguments.
            return ToolCallError(
                name=name, message=f"Invalid arguments: {e}", args=kwargs,
            )
        except Exception as e:
            # Other exceptions raised by the tool.
            return ToolCallError(
                name=name, message=f"Execution failed: {type(e).__name__}: {e}", args=kwargs,
            )

    def call_tool_call(self, tool_call: ToolCall) -> Any:
        """Invoke a tool from a ToolCall object.

        ToolCall arguments may be a dict or a JSON string and are parsed automatically.
        JSON parsing errors, invalid arguments and tool exceptions are caught
        and returned as ToolCallError values instead of being raised.
        """
        args = tool_call.arguments
        if isinstance(args, str):
            try:
                args = json.loads(args) if args else {}
            except json.JSONDecodeError as e:
                return ToolCallError(
                    name=tool_call.name,
                    message=f"Arguments are not valid JSON: {args!r} ({e})",
                    args=tool_call.arguments,
                )
        if not isinstance(args, dict):
            return ToolCallError(
                name=tool_call.name,
                message=f"Arguments must be a dict or JSON string; got: {type(args).__name__}",
                args=args,
            )
        return self.call(tool_call.name, **args)

    # ----- ToolCall formatting -----

    def tool_call_to_str(self, tool_call: ToolCall) -> str:
        """Format a ToolCall as text for a message.

        Example:
            get_weather(city="Beijing")
            search(query="hello", limit=5)

        If the tool is not registered, use the fallback format:
            get_weather({"city": "Beijing"})
        """
        name = tool_call.name
        args = tool_call.arguments
        if isinstance(args, str):
            try:
                args = json.loads(args) if args else {}
            except json.JSONDecodeError:
                args = {"_raw": args}

        # Format arguments using the registered function signature.
        if name in self._tools:
            tf = self._tools[name]
            return _format_call(name, args, tf)
        # Use the fallback format when the tool is not registered.
        return _format_call_raw(name, args)

    # ----- Permission metadata -----

    def set_permission(self, name: str, permission: Any) -> None:
        """Set tool permission metadata; the default policy allows calls."""
        if name not in self._tools:
            raise ToolRegistryError(f"Tool does not exist: {name}")
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
    """Format a call as name(k=v, ...) using the function signature."""
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
    """Format a call without a registered tool signature."""
    parts = [f"{k}={_repr_value(v)}" for k, v in args.items()]
    return f"{name}({', '.join(parts)})"


def _repr_value(v: Any) -> str:
    """Format values for display: quote strings and use repr for other types."""
    if isinstance(v, str):
        return f'"{v}"'
    return repr(v)
