"""Prompt rendering helpers for tool registries."""

from __future__ import annotations

from typing import Any


def render_tool_registry(registry: Any | None) -> str:
    """Render the available-tool prompt section for a registry."""
    if registry is None:
        return ""
    tools = registry.to_openai_tools()
    if not tools:
        return ""
    lines = ["## Available Tools"]
    for tool in tools:
        fn = tool.get("function", {})
        name = fn.get("name", "")
        desc = fn.get("description", "")
        params = fn.get("parameters", {}) or {}
        props = params.get("properties", {}) or {}
        required = set(params.get("required", []) or [])
        param_strs = []
        for pname in props:
            mark = "" if pname in required else "?"
            param_strs.append(f"{pname}{mark}")
        sig = f"({', '.join(param_strs)})" if param_strs else "()"
        desc_short = desc.split("\n")[0].strip() if desc else ""
        lines.append(f"- {name}{sig}: {desc_short}")
    return "\n".join(lines)
