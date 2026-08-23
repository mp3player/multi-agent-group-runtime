"""In-memory shared state for group collaboration."""

from __future__ import annotations

GROUP_MEMORY_SECTIONS = (
    "goal",
    "plan",
    "scopes",
    "decisions",
    "done",
    "next",
    "risks",
)


class GroupMemory:
    """Small bounded-by-process memory shared by all members of one group."""

    def __init__(self) -> None:
        self._sections: dict[str, str] = {
            section: "" for section in GROUP_MEMORY_SECTIONS
        }

    @property
    def sections(self) -> tuple[str, ...]:
        return GROUP_MEMORY_SECTIONS

    def __getitem__(self, section: str) -> str:
        return self._sections[section]

    def get(self, section: str, default: str = "") -> str:
        return self._sections.get(section, default)

    def to_dict(self) -> dict[str, str]:
        """Return a shallow copy of all fixed memory sections."""
        return dict(self._sections)

    def update(self, section: str, content: str, *, append: bool = False) -> str:
        """Update one memory section and return a tool-facing status string."""
        section = self._normalize_section(section)
        content = content.strip()
        if section not in GROUP_MEMORY_SECTIONS:
            return _section_error()
        if append and self._sections[section].strip():
            self._sections[section] = f"{self._sections[section].rstrip()}\n{content}"
        else:
            self._sections[section] = content
        return f"(已更新 memory.{section})"

    def clear(self, section: str = "") -> str:
        """Clear one memory section, or all sections when section is empty."""
        section = self._normalize_section(section)
        if not section:
            for key in GROUP_MEMORY_SECTIONS:
                self._sections[key] = ""
            return "(已清空全部 group memory)"
        if section not in GROUP_MEMORY_SECTIONS:
            return _section_error()
        self._sections[section] = ""
        return f"(已清空 memory.{section})"

    def render_full(self, *, include_empty: bool = False) -> str:
        lines = []
        for section in GROUP_MEMORY_SECTIONS:
            content = self._sections.get(section, "").strip()
            if content or include_empty:
                lines.append(f"[{section}]\n{content or '(empty)'}")
        return "\n\n".join(lines) if lines else "(group memory is empty)"

    def render_summary(self, *, limit: int = 120) -> str:
        lines = []
        for section in GROUP_MEMORY_SECTIONS:
            content = " ".join(self._sections.get(section, "").split())
            if not content:
                continue
            if len(content) > limit:
                content = content[:limit] + "..."
            lines.append(f"- {section}: {content}")
        return "\n".join(lines) if lines else "(empty)"

    def _normalize_section(self, section: str) -> str:
        return str(section).strip().lower()


def _section_error() -> str:
    allowed = ", ".join(GROUP_MEMORY_SECTIONS)
    return f"[错误] section 必须是以下之一: {allowed}"
