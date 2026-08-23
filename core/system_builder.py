"""System Prompt public facade.

``SystemBuilder`` keeps the existing public API. Prompt module loading, dynamic
module rendering, and final assembly are delegated to ``prompting.runtime``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable, TypeVar

from prompting.runtime import PromptRuntime, PromptRuntimeError
from prompting.skills import parse_skill_frontmatter
from prompting.specs import GROUP_PROMPT_MODULES, PromptModuleSpec

if TYPE_CHECKING:
    from tools.registry import ToolRegistry

T = TypeVar("T")


class SystemBuilderError(RuntimeError):
    """SystemBuilder error."""


class SystemBuilder:
    """System prompt builder facade.

    Loads Markdown modules from the prompts directory, assembles them in a
    chosen order, and optionally appends available tools, skills, and group
    member identity modules.
    """

    def __init__(self, prompts_dir: str | Path | None = None) -> None:
        self.runtime = PromptRuntime.create(prompts_dir)
        self._logger = logging.getLogger("mas.system_builder")

    def _runtime_call(self, func: Callable[..., T], *args: object, **kwargs: object) -> T:
        try:
            return func(*args, **kwargs)
        except PromptRuntimeError as error:
            raise SystemBuilderError(str(error)) from error

    # ----- Public state delegation -----

    @property
    def prompts_dir(self) -> Path:
        return self.runtime.prompts_dir

    @prompts_dir.setter
    def prompts_dir(self, value: str | Path) -> None:
        self.runtime.prompts_dir = value

    @property
    def modules(self) -> dict[str, str]:
        return self.runtime.modules

    @modules.setter
    def modules(self, value: dict[str, str]) -> None:
        self.runtime.modules = value

    @property
    def order(self) -> list[str]:
        return self.runtime.order

    @order.setter
    def order(self, value: list[str]) -> None:
        self.runtime.order = value

    @property
    def skills_dir(self) -> Path:
        return self.runtime.skills_dir

    @skills_dir.setter
    def skills_dir(self, value: str | Path) -> None:
        self.runtime.skills_dir = value

    @property
    def skills(self) -> list[dict[str, str]]:
        return self.runtime.skills

    @skills.setter
    def skills(self, value: list[dict[str, str]]) -> None:
        self.runtime.skills = value

    @property
    def skill_load_errors(self) -> list[str]:
        return self.runtime.skill_load_errors

    @skill_load_errors.setter
    def skill_load_errors(self, value: list[str]) -> None:
        self.runtime.skill_load_errors = value

    @property
    def _tool_registry(self) -> "ToolRegistry | None":
        return self.runtime.tool_registry

    @_tool_registry.setter
    def _tool_registry(self, value: "ToolRegistry | None") -> None:
        self.runtime.tool_registry = value

    @property
    def _group_chat(self) -> dict[str, str] | None:
        return self.runtime.group_chat

    @_group_chat.setter
    def _group_chat(self, value: dict[str, str] | None) -> None:
        self.runtime.group_chat = value

    # ----- Static module loading -----

    def load(self, name: str, *, path: str | Path | None = None) -> str:
        """Load one static module from a Markdown file."""
        return self._runtime_call(self.runtime.load, name, path=path)

    def load_all(self) -> list[str]:
        """Load all Markdown modules under prompts_dir."""
        return self.runtime.load_all()

    def load_default(self) -> list[str]:
        """Load default static modules according to prompts/order.txt."""
        return self.runtime.load_default()

    def load_default_from_specs(self) -> list[str]:
        """Load common prompt modules from the prompt spec registry."""
        return self.runtime.load_default_from_specs()

    def add_module(self, name: str, text: str) -> None:
        """Add a text module directly without loading a file."""
        self.runtime.add_module(name, text)

    # ----- Static module ordering -----

    def use(self, names: list[str]) -> None:
        """Select active static modules and their assembly order."""
        self._runtime_call(self.runtime.use, names)

    def prepend(self, name: str) -> None:
        """Move a loaded static module to the beginning."""
        self._runtime_call(self.runtime.prepend, name)

    def remove(self, name: str) -> None:
        """Remove one static module."""
        self.runtime.remove(name)

    # ----- Available tools, rendered lazily from ToolRegistry -----

    def attach_tool_registry(self, registry: "ToolRegistry") -> None:
        """Attach a tool registry."""
        self.runtime.attach_tool_registry(registry)

    def detach_tool_registry(self) -> None:
        """Detach the tool registry."""
        self.runtime.detach_tool_registry()

    def _render_tools(self) -> str:
        """Render available tools from the attached ToolRegistry."""
        return self.runtime.render_tools()

    # ----- Group chat -----

    def enable_group_chat(
        self,
        *,
        group_name: str,
        member_name: str,
        member_description: str = "",
    ) -> None:
        """Enable the multi-agent group chat system prompt module."""
        self.runtime.enable_group_chat(
            group_name=group_name,
            member_name=member_name,
            member_description=member_description,
        )

    def disable_group_chat(self) -> None:
        """Disable the group chat prompt module."""
        self.runtime.disable_group_chat()

    def _render_group_chat(self) -> str:
        """Render stable multi-agent group chat rules."""
        return self.runtime.render_group_chat()

    def group_prompt_specs(self) -> tuple[PromptModuleSpec, ...]:
        """Return group prompt specs used by group-chat rendering."""
        return GROUP_PROMPT_MODULES

    # ----- Available skills -----

    def load_skills(
        self, skills_dir: str | Path | None = None
    ) -> list[str]:
        """Load all SKILL.md files and extract name plus description."""
        return self.runtime.load_skills(skills_dir, logger=self._logger)

    def add_skill(
        self, name: str, description: str, *, path: str | None = None
    ) -> None:
        """Add one skill manually without loading it from disk."""
        self.runtime.add_skill(name, description, path=path)

    def remove_skill(self, name: str) -> None:
        """Remove one loaded skill."""
        self.runtime.remove_skill(name)

    @staticmethod
    def _parse_skill_frontmatter(text: str) -> tuple[str, str]:
        """Parse SKILL.md YAML frontmatter into (name, description)."""
        return parse_skill_frontmatter(text)

    def _render_skills(self) -> str:
        """Render available skills."""
        return self.runtime.render_skills()

    # ----- Build -----

    def build(self) -> str:
        """Assemble and return the full system prompt."""
        return self.runtime.build()

    # ----- Queries -----

    def has(self, name: str) -> bool:
        return self.runtime.has(name)

    def list_modules(self) -> list[str]:
        return self.runtime.list_modules()

    def list_skills(self) -> list[str]:
        return self.runtime.list_skills()

    @property
    def has_tool_registry(self) -> bool:
        return self.runtime.has_tool_registry

    @property
    def has_group_chat(self) -> bool:
        return self.runtime.has_group_chat

    def __repr__(self) -> str:
        return (
            f"SystemBuilder(modules={self.order}, "
            f"tools={'on' if self.has_tool_registry else 'off'}, "
            f"group={'on' if self.has_group_chat else 'off'}, "
            f"skills={len(self.skills)})"
        )
