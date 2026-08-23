"""Skill prompt loading and rendering helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LoadedSkills:
    """Result of loading skills from a skill directory."""

    skills: list[dict[str, str]]
    loaded_names: list[str]
    errors: list[str]
    directory_exists: bool


def load_skills_from_dir(
    skills_dir: str | Path,
    *,
    logger: logging.Logger | None = None,
) -> LoadedSkills:
    """Load all ``SKILL.md`` summaries from one skills directory."""
    root = Path(skills_dir)
    errors: list[str] = []
    if not root.exists():
        return LoadedSkills([], [], errors, False)

    skills: list[dict[str, str]] = []
    loaded: list[str] = []
    for skill_dir in sorted(root.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue
        try:
            text = skill_file.read_text(encoding="utf-8")
        except OSError as exc:
            error = f"{skill_file}: {type(exc).__name__}: {exc}"
            errors.append(error)
            if logger is not None:
                logger.warning("skill_load_failed %s", error)
            continue
        name, description = parse_skill_frontmatter(text)
        if not name:
            name = skill_dir.name
        skills.append({
            "name": name,
            "description": description,
            "path": str(skill_file),
        })
        loaded.append(name)
    return LoadedSkills(skills, loaded, errors, True)


def parse_skill_frontmatter(text: str) -> tuple[str, str]:
    """Parse simple YAML frontmatter from a ``SKILL.md`` file."""
    name = ""
    description = ""
    if not text.startswith("---"):
        return name, description
    end = text.find("\n---", 3)
    if end == -1:
        return name, description
    front = text[3:end]
    lines = front.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if ":" in stripped and not stripped.startswith((" ", "\t")):
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if value in (">", ">-", "|", "|-"):
                block_lines: list[str] = []
                i += 1
                while i < n:
                    bline = lines[i]
                    if bline.startswith((" ", "\t")):
                        block_lines.append(bline.strip())
                        i += 1
                    else:
                        break
                block_text = " ".join(bl for bl in block_lines if bl)
                if key == "name":
                    name = block_text.strip('"').strip("'")
                elif key == "description":
                    description = block_text.strip('"').strip("'")
                continue
            value = value.strip('"').strip("'")
            if key == "name":
                name = value
            elif key == "description":
                description = value
        i += 1
    return name, description


def render_skills(skills: list[dict[str, str]]) -> str:
    """Render the available-skills prompt section."""
    if not skills:
        return ""
    lines = [
        "## Available Skills",
        "Use a skill only when its name fits the task or the user asks for it. Read its SKILL.md before relying on details.",
    ]
    for skill in skills:
        desc = skill.get("description", "")
        desc_short = desc.split("。", 1)[0].split(". ", 1)[0].strip()
        if not desc_short:
            desc_short = "no summary"
        path = skill.get("path", "")
        lines.append(f"- {skill['name']} ({path}): {desc_short}")
    return "\n".join(lines)
