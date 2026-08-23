"""Runtime components for system prompt composition."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv

from prompting.loader import load_common_prompt_specs
from prompting.renderer import render_group_chat_prompt
from prompting.skills import load_skills_from_dir, render_skills
from prompting.tool_renderer import render_tool_registry


class PromptRuntimeError(RuntimeError):
    """Prompt runtime error."""


class PromptLogger(Protocol):
    def warning(self, msg: str, *args: object) -> None:
        """Log a warning."""
        ...


@dataclass(slots=True)
class PromptEnvironment:
    """Resolve prompt-related paths and load the project environment."""

    prompts_dir: Path
    skills_dir: Path

    _env_loaded = False

    @classmethod
    def create(
        cls,
        prompts_dir: str | Path | None = None,
    ) -> "PromptEnvironment":
        cls.load_env()
        if prompts_dir is None:
            prompts_dir = Path(__file__).resolve().parent.parent / "prompts"
        skills_dir = Path(
            os.environ.get("MAS_SKILLS_DIR")
            or os.environ.get("SkillsDir")
            or "/home/coder/skill"
        )
        return cls(prompts_dir=Path(prompts_dir), skills_dir=skills_dir)

    @classmethod
    def load_env(cls) -> None:
        if cls._env_loaded:
            return
        env_path = Path(__file__).resolve().parent.parent / ".env"
        load_dotenv(env_path, override=False)
        cls._env_loaded = True


@dataclass(slots=True)
class PromptModuleStore:
    """Store static prompt modules and their render order."""

    prompts_dir: Path
    modules: dict[str, str] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)

    def load(self, name: str, *, path: str | Path | None = None) -> str:
        """Load one static module from disk."""
        file_path = Path(path) if path else self.prompts_dir / f"{name}.md"
        if not file_path.exists():
            raise PromptRuntimeError(f"模块文件不存在: {file_path}")
        text = file_path.read_text(encoding="utf-8").strip()
        self.add(name, text)
        return text

    def load_all(self) -> list[str]:
        """Load all ``*.md`` modules from ``prompts_dir``."""
        if not self.prompts_dir.exists():
            return []
        names: list[str] = []
        for md_file in sorted(self.prompts_dir.glob("*.md")):
            name = md_file.stem
            if name not in self.modules:
                self.load(name, path=md_file)
                names.append(name)
        return names

    def load_default(self) -> list[str]:
        """Load modules using the current default order fallback."""
        if not self.prompts_dir.exists():
            return []
        order_file = self.prompts_dir / "order.txt"
        names: list[str] = []
        if order_file.exists():
            for line in order_file.read_text(encoding="utf-8").splitlines():
                name = line.strip()
                if not name or name.startswith("#"):
                    continue
                self.load(name)
                names.append(name)
            return names
        base_file = self.prompts_dir / "base.md"
        if base_file.exists():
            self.load("base")
            return ["base"]
        return self.load_all()

    def add(self, name: str, text: str) -> None:
        """Add a static module."""
        self.modules[name] = text.strip()
        if name not in self.order:
            self.order.append(name)

    def use(self, names: list[str]) -> None:
        """Set enabled static modules and order."""
        for name in names:
            if name not in self.modules:
                raise PromptRuntimeError(f"模块未加载: {name}")
        self.order = list(names)

    def prepend(self, name: str) -> None:
        """Move a loaded module to the front."""
        if name not in self.modules:
            raise PromptRuntimeError(f"模块未加载: {name}")
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

    def render(self) -> list[str]:
        """Return enabled static module texts in order, omitting empty modules."""
        return [
            text for name in self.order
            if (text := self.modules.get(name, ""))
        ]


@dataclass(slots=True)
class PromptState:
    """Dynamic prompt composition state."""

    tool_registry: Any | None = None
    group_chat: dict[str, str] | None = None
    skills_dir: Path = field(default_factory=lambda: Path("/home/coder/skill"))
    skills: list[dict[str, str]] = field(default_factory=list)
    skill_load_errors: list[str] = field(default_factory=list)

    def enable_group_chat(
        self,
        *,
        group_name: str,
        member_name: str,
        member_description: str = "",
    ) -> None:
        self.group_chat = {
            "group_name": group_name,
            "member_name": member_name,
            "member_description": member_description,
        }

    def disable_group_chat(self) -> None:
        self.group_chat = None

    def add_skill(
        self,
        name: str,
        description: str,
        *,
        path: str | None = None,
    ) -> None:
        self.skills.append({
            "name": name,
            "description": description,
            "path": path or "",
        })

    def remove_skill(self, name: str) -> None:
        self.skills = [skill for skill in self.skills if skill["name"] != name]

    def list_skills(self) -> list[str]:
        return [skill["name"] for skill in self.skills]


@dataclass(slots=True)
class PromptRuntime:
    """Compose static and dynamic prompt modules."""

    store: PromptModuleStore
    state: PromptState

    @classmethod
    def create(
        cls,
        prompts_dir: str | Path | None = None,
    ) -> "PromptRuntime":
        environment = PromptEnvironment.create(prompts_dir)
        return cls(
            store=PromptModuleStore(environment.prompts_dir),
            state=PromptState(skills_dir=environment.skills_dir),
        )

    @property
    def prompts_dir(self) -> Path:
        return self.store.prompts_dir

    @prompts_dir.setter
    def prompts_dir(self, value: str | Path) -> None:
        self.store.prompts_dir = Path(value)

    @property
    def modules(self) -> dict[str, str]:
        return self.store.modules

    @modules.setter
    def modules(self, value: dict[str, str]) -> None:
        self.store.modules = value

    @property
    def order(self) -> list[str]:
        return self.store.order

    @order.setter
    def order(self, value: list[str]) -> None:
        self.store.order = value

    @property
    def skills_dir(self) -> Path:
        return self.state.skills_dir

    @skills_dir.setter
    def skills_dir(self, value: str | Path) -> None:
        self.state.skills_dir = Path(value)

    @property
    def skills(self) -> list[dict[str, str]]:
        return self.state.skills

    @skills.setter
    def skills(self, value: list[dict[str, str]]) -> None:
        self.state.skills = value

    @property
    def skill_load_errors(self) -> list[str]:
        return self.state.skill_load_errors

    @skill_load_errors.setter
    def skill_load_errors(self, value: list[str]) -> None:
        self.state.skill_load_errors = value

    @property
    def tool_registry(self) -> Any | None:
        return self.state.tool_registry

    @tool_registry.setter
    def tool_registry(self, value: Any | None) -> None:
        self.state.tool_registry = value

    @property
    def group_chat(self) -> dict[str, str] | None:
        return self.state.group_chat

    @group_chat.setter
    def group_chat(self, value: dict[str, str] | None) -> None:
        self.state.group_chat = value

    @property
    def has_tool_registry(self) -> bool:
        return self.state.tool_registry is not None

    @property
    def has_group_chat(self) -> bool:
        return self.state.group_chat is not None

    def load_default_from_specs(self) -> list[str]:
        return load_common_prompt_specs(self)

    def load(self, name: str, *, path: str | Path | None = None) -> str:
        return self.store.load(name, path=path)

    def load_all(self) -> list[str]:
        return self.store.load_all()

    def load_default(self) -> list[str]:
        return self.store.load_default()

    def add_module(self, name: str, text: str) -> None:
        self.store.add(name, text)

    def use(self, names: list[str]) -> None:
        self.store.use(names)

    def prepend(self, name: str) -> None:
        self.store.prepend(name)

    def remove(self, name: str) -> None:
        self.store.remove(name)

    def has(self, name: str) -> bool:
        return self.store.has(name)

    def list_modules(self) -> list[str]:
        return self.store.list_modules()

    def attach_tool_registry(self, registry: Any) -> None:
        self.state.tool_registry = registry

    def detach_tool_registry(self) -> None:
        self.state.tool_registry = None

    def enable_group_chat(
        self,
        *,
        group_name: str,
        member_name: str,
        member_description: str = "",
    ) -> None:
        self.state.enable_group_chat(
            group_name=group_name,
            member_name=member_name,
            member_description=member_description,
        )

    def disable_group_chat(self) -> None:
        self.state.disable_group_chat()

    def load_skills(
        self,
        skills_dir: str | Path | None = None,
        *,
        logger: PromptLogger | None = None,
    ) -> list[str]:
        if skills_dir is not None:
            self.state.skills_dir = Path(skills_dir)
        self.state.skill_load_errors = []
        result = load_skills_from_dir(self.state.skills_dir, logger=logger)
        self.state.skill_load_errors = result.errors
        if not result.directory_exists:
            return []
        self.state.skills = result.skills
        return result.loaded_names

    def add_skill(
        self,
        name: str,
        description: str,
        *,
        path: str | None = None,
    ) -> None:
        self.state.add_skill(name, description, path=path)

    def remove_skill(self, name: str) -> None:
        self.state.remove_skill(name)

    def list_skills(self) -> list[str]:
        return self.state.list_skills()

    def render_tools(self) -> str:
        return render_tool_registry(self.state.tool_registry)

    def render_skills(self) -> str:
        return render_skills(self.state.skills)

    def render_group_chat(self) -> str:
        if self.state.group_chat is None:
            return ""
        return render_group_chat_prompt(
            self.prompts_dir,
            group_name=self.state.group_chat["group_name"],
            member_name=self.state.group_chat["member_name"],
            member_description=self.state.group_chat["member_description"],
        )

    def build(self) -> str:
        parts: list[str] = []
        parts.extend(self.store.render())
        tools_text = self.render_tools()
        if tools_text:
            parts.append(tools_text)
        skills_text = self.render_skills()
        if skills_text:
            parts.append(skills_text)
        group_text = self.render_group_chat()
        if group_text:
            parts.append(group_text)
        return "\n\n".join(parts)
