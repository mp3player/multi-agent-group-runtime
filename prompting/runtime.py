"""System prompt composition with one owner for static and dynamic state."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

from prompting.loader import load_common_prompt_specs, load_default_prompt_modules
from prompting.skills import load_skills_from_dir, parse_skill_frontmatter, render_skills
from prompting.tool_renderer import render_tool_registry


class SystemBuilderError(RuntimeError):
    """A prompt module could not be loaded or selected."""


class PromptLogger(Protocol):
    def warning(self, msg: str, *args: object) -> None:
        """Log a warning."""
        ...


class SystemBuilder:
    """Load, order, and render Markdown modules, tools, and available skills."""

    def __init__(
        self, prompts_dir: str | Path | None = None,
        *, skills_dir: str | Path | None = None,
    ) -> None:
        project_dir = Path(__file__).resolve().parents[1]
        self.prompts_dir = prompts_dir if prompts_dir is not None else project_dir / "prompts"
        self.skills_dir = skills_dir if skills_dir is not None else project_dir / "skills"
        self.modules: dict[str, str] = {}
        self.order: list[str] = []
        self.skills: list[dict[str, str]] = []
        self.skill_load_errors: list[str] = []
        self._tool_registry: Any | None = None
        self._logger = logging.getLogger("mas.system_builder")

    @classmethod
    def create(
        cls, prompts_dir: str | Path | None = None,
        *, skills_dir: str | Path | None = None,
    ) -> SystemBuilder:
        """Compatibility factory for callers of ``PromptRuntime.create``."""
        return cls(prompts_dir, skills_dir=skills_dir)

    @property
    def prompts_dir(self) -> Path:
        return self._prompts_dir

    @prompts_dir.setter
    def prompts_dir(self, value: str | Path) -> None:
        self._prompts_dir = Path(value)

    @property
    def skills_dir(self) -> Path:
        return self._skills_dir

    @skills_dir.setter
    def skills_dir(self, value: str | Path) -> None:
        self._skills_dir = Path(value)

    def load(self, name: str, *, path: str | Path | None = None) -> str:
        """Load one static module from a Markdown file."""
        file_path = Path(path) if path else self.prompts_dir / f"{name}.md"
        if not file_path.exists():
            raise SystemBuilderError(f"Module file does not exist: {file_path}")
        text = file_path.read_text(encoding="utf-8").strip()
        self.add_module(name, text)
        return text

    def load_all(self) -> list[str]:
        """Load unloaded Markdown modules in filename order."""
        names: list[str] = []
        for md_file in sorted(self.prompts_dir.glob("*.md")):
            name = md_file.stem
            if name not in self.modules:
                self.load(name, path=md_file)
                names.append(name)
        return names

    def load_default(self, *, order_file: str | Path = "order.txt") -> list[str]:
        """Use the order file, available common modules, then all Markdown files."""
        return load_default_prompt_modules(self, order_file=order_file)

    def load_default_from_specs(self) -> list[str]:
        """Explicitly load the complete common bundle; missing modules are errors."""
        return load_common_prompt_specs(self)

    def add_module(self, name: str, text: str) -> None:
        """Add a text module directly without loading a file."""
        self.modules[name] = text.strip()
        if name not in self.order:
            self.order.append(name)

    def use(self, names: list[str]) -> None:
        """Select active static modules and their assembly order."""
        for name in names:
            if name not in self.modules:
                raise SystemBuilderError(f"Module is not loaded: {name}")
        self.order = list(names)

    def prepend(self, name: str) -> None:
        """Move a loaded static module to the beginning."""
        if name not in self.modules:
            raise SystemBuilderError(f"Module is not loaded: {name}")
        if name in self.order:
            self.order.remove(name)
        self.order.insert(0, name)

    def remove(self, name: str) -> None:
        """Remove one static module."""
        self.modules.pop(name, None)
        if name in self.order:
            self.order.remove(name)

    def has(self, name: str) -> bool:
        return name in self.modules

    def list_modules(self) -> list[str]:
        return list(self.order)

    @property
    def tool_registry(self) -> Any | None:
        return self._tool_registry

    @tool_registry.setter
    def tool_registry(self, value: Any | None) -> None:
        self._tool_registry = value

    @property
    def has_tool_registry(self) -> bool:
        return self._tool_registry is not None

    def attach_tool_registry(self, registry: Any) -> None:
        self._tool_registry = registry

    def detach_tool_registry(self) -> None:
        self._tool_registry = None

    def load_skills(
        self, skills_dir: str | Path | None = None,
        *, logger: PromptLogger | None = None,
    ) -> list[str]:
        """Load skill descriptions, preserving manual skills if the directory is absent."""
        if skills_dir is not None:
            self.skills_dir = skills_dir
        self.skill_load_errors = []
        result = load_skills_from_dir(
            self.skills_dir, logger=logger if logger is not None else self._logger,
        )
        self.skill_load_errors = result.errors
        if not result.directory_exists:
            return []
        self.skills = result.skills
        return result.loaded_names

    def add_skill(
        self, name: str, description: str, *, path: str | None = None,
    ) -> None:
        """Add one skill manually without loading it from disk."""
        self.skills.append({"name": name, "description": description, "path": path or ""})

    def remove_skill(self, name: str) -> None:
        self.skills = [skill for skill in self.skills if skill["name"] != name]

    def list_skills(self) -> list[str]:
        return [skill["name"] for skill in self.skills]

    def render_tools(self) -> str:
        return render_tool_registry(self._tool_registry)

    def render_skills(self) -> str:
        return render_skills(self.skills)

    # Keep private hooks for older callers while dispatching public overrides.
    _parse_skill_frontmatter = staticmethod(parse_skill_frontmatter)

    def _render_tools(self) -> str:
        return self.render_tools()

    def _render_skills(self) -> str:
        return self.render_skills()

    def build(self) -> str:
        """Assemble enabled static modules, then tools, then skills."""
        parts = [text for name in self.order if (text := self.modules.get(name, ""))]
        parts.extend(text for text in (self._render_tools(), self._render_skills()) if text)
        return "\n\n".join(parts)

    def __repr__(self) -> str:
        return (
            f"SystemBuilder(modules={self.order}, "
            f"tools={'on' if self.has_tool_registry else 'off'}, "
            f"skills={len(self.skills)})"
        )


# Import compatibility names share the implementation and exception identity.
PromptRuntime = SystemBuilder
PromptRuntimeError = SystemBuilderError
