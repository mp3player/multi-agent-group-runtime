"""Prompt spec loading helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from prompting.specs import COMMON_PROMPT_MODULES, PromptModuleSpec


class PromptModuleLoader(Protocol):
    """Minimal loader surface required by prompt specs."""

    prompts_dir: Path

    def load(self, name: str, *, path: str | Path | None = None) -> str:
        """Load one prompt module."""
        ...

    def load_all(self) -> list[str]:
        """Load all prompt modules in the configured directory."""
        ...


def load_default_prompt_modules(
    loader: PromptModuleLoader, *, order_file: str | Path = "order.txt",
) -> list[str]:
    """Use the configured order, available common modules, then all Markdown files.

    Explicit order files are authoritative, including empty files and missing
    module names. Relative order paths are resolved under ``prompts_dir``.
    """
    order_path = Path(order_file)
    if not order_path.is_absolute():
        order_path = loader.prompts_dir / order_path
    if order_path.is_file():
        names = [
            name for line in order_path.read_text(encoding="utf-8").splitlines()
            if (name := line.strip()) and not name.startswith("#")
        ]
    else:
        names = [
            spec.name for spec in COMMON_PROMPT_MODULES
            if resolve_prompt_spec_path(loader.prompts_dir, spec).is_file()
        ]
        if not names:
            return loader.load_all()
    for name in names:
        loader.load(name)
    return names


def resolve_prompt_spec_path(prompts_dir: str | Path, spec: PromptModuleSpec) -> Path:
    """Resolve a prompt spec path relative to one prompts directory."""
    path = Path(spec.template_path)
    if path.parts and path.parts[0] == "prompts":
        path = Path(*path.parts[1:])
    if path.is_absolute():
        return path
    return Path(prompts_dir) / path


def load_common_prompt_specs(loader: PromptModuleLoader) -> list[str]:
    """Load common prompt modules in registry order."""
    names: list[str] = []
    for spec in COMMON_PROMPT_MODULES:
        loader.load(spec.name, path=resolve_prompt_spec_path(loader.prompts_dir, spec))
        names.append(spec.name)
    return names
