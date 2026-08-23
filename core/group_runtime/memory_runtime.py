"""Group memory side-effect runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class GroupMemoryRuntime:
    """Update group memory and record related events."""

    def update(
        self,
        group: object,
        section: str,
        content: str,
        *,
        append: bool = False,
        actor: str = "system",
    ) -> str:
        """Update one memory section and record a memory event on success."""
        result = group.memory.update(section, content, append=append)
        if not result.startswith("[错误]"):
            group.events.append(
                "memory_update",
                actor=actor,
                data={"section": section.strip().lower(), "append": append},
            )
        return result

    def clear(self, group: object, section: str = "", *, actor: str = "system") -> str:
        """Clear one memory section, or all sections when section is empty."""
        result = group.memory.clear(section)
        if not result.startswith("[错误]"):
            group.events.append(
                "memory_update",
                actor=actor,
                data={"section": section.strip().lower(), "clear": True},
            )
        return result
