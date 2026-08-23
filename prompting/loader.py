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
