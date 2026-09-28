"""Single-agent prompt module declarations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


PromptScope = Literal["common", "tools", "memory"]


@dataclass(frozen=True, slots=True)
class PromptModuleSpec:
    """Static prompt module declaration."""

    name: str
    scope: PromptScope
    template_path: str
    required_variables: tuple[str, ...] = ()


COMMON_PROMPT_MODULES: tuple[PromptModuleSpec, ...] = (
    PromptModuleSpec("base", "common", "prompts/base.md"),
    PromptModuleSpec("workflow", "common", "prompts/workflow.md"),
    PromptModuleSpec("tool_use", "common", "prompts/tool_use.md"),
    PromptModuleSpec("skills", "common", "prompts/skills.md"),
    PromptModuleSpec("communication", "common", "prompts/communication.md"),
)
