"""Tool decorator.

Use @tool to mark a function as a tool. The decorator generates an
OpenAI-compatible tool schema from the function signature and docstring.

Example:
    @tool
    def get_weather(city: str) -> str:
        \"\"\"Get weather for a city.

        Args:
            city: City name, for example Beijing or Shanghai.
        \"\"\"
        ...

Decorated functions expose tool metadata and can be converted to the dict shape
expected by OpenAI-compatible APIs with `to_openai_tool()`.
"""

from __future__ import annotations

import inspect
import re
from typing import Any, Callable, TypeVar, get_args, get_origin, get_type_hints

F = TypeVar("F", bound=Callable[..., Any])

# Python type -> JSON Schema type
_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _python_type_to_json(py_type: Any) -> dict[str, Any]:
    """Convert a Python type annotation into a JSON Schema fragment."""
    origin = get_origin(py_type)
    if origin is None:
        if py_type in _TYPE_MAP:
            return {"type": _TYPE_MAP[py_type]}
        if py_type is Any or py_type is type(None):
            return {}
        # Unknown types fall back to string.
        return {"type": "string"}

    # list[T]
    if origin in (list, tuple, set):
        args = get_args(py_type)
        schema: dict[str, Any] = {"type": "array"}
        if args:
            schema["items"] = _python_type_to_json(args[0])
        return schema

    # dict[K, V]
    if origin is dict:
        return {"type": "object"}

    # Optional / Union: use the first non-None type.
    import typing
    if origin is typing.Union:
        args = [a for a in get_args(py_type) if a is not type(None)]
        if len(args) == 1:
            return _python_type_to_json(args[0])
        # Multiple alternatives: leave the type unconstrained.
        return {}

    return {"type": "string"}


def _parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    """Parse a Google-style docstring.

    Returns (description, {parameter_name: parameter_description}).
    """
    if not doc:
        return "", {}
    lines = inspect.cleandoc(doc).splitlines()
    description_parts: list[str] = []
    params: dict[str, str] = {}
    in_args = False
    current_param: str | None = None
    current_desc: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("args:") or stripped.lower().startswith("arguments:"):
            if description_parts and description_parts[-1] == "":
                description_parts.pop()
            in_args = True
            continue
        if in_args and re.match(r"^\s*\w+\s*:", line):
            # Save the previous parameter.
            if current_param is not None:
                params[current_param] = " ".join(current_desc).strip()
            match = re.match(r"^\s*(\w+)\s*:\s*(.*)$", line)
            if match:
                current_param = match.group(1)
                current_desc = [match.group(2)]
            continue
        if in_args and current_param is not None:
            current_desc.append(stripped)
            continue
        # Description section.
        if not in_args:
            description_parts.append(stripped)

    if current_param is not None:
        params[current_param] = " ".join(current_desc).strip()

    description = "\n".join(description_parts).strip()
    return description, params


class ToolFunction:
    """Wrapper for a function decorated with @tool.

    Attributes:
        func: Original function.
        name: Tool name. Defaults to the function name.
        description: Tool description extracted from the docstring.
        parameters: Parameter JSON Schema.
    """

    def __init__(self, func: Callable[..., Any]) -> None:
        self.func = func
        self.name = func.__name__
        self.description, param_docs = _parse_docstring(func.__doc__)

        sig = inspect.signature(func)
        type_hints = get_type_hints(func)

        properties: dict[str, Any] = {}
        required: list[str] = []
        for pname, param in sig.parameters.items():
            if pname in ("self", "cls"):
                continue
            # *args / **kwargs are not represented in the schema.
            if param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            py_type = type_hints.get(pname, str)
            schema = _python_type_to_json(py_type)
            if pname in param_docs:
                schema["description"] = param_docs[pname]
            properties[pname] = schema
            if param.default is inspect.Parameter.empty:
                required.append(pname)

        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.func(*args, **kwargs)

    # Forward common function attributes for compatibility.
    @property
    def __name__(self) -> str:
        return self.func.__name__

    @property
    def __doc__(self) -> str | None:
        return self.func.__doc__

    @property
    def __wrapped__(self) -> Callable[..., Any]:
        return self.func

    def to_openai_tool(self) -> dict[str, Any]:
        """Convert to the OpenAI-compatible tool definition shape."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def __repr__(self) -> str:
        return f"ToolFunction(name={self.name!r})"


def tool(func: F) -> ToolFunction:
    """Mark a function as a tool and generate its schema."""
    return ToolFunction(func)
