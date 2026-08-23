"""Prompt rendering helpers."""

from __future__ import annotations

from pathlib import Path


GROUP_CHAT_FALLBACK_TEMPLATE = (
    "# Multi-Agent Group Chat\n\n"
    "- Group: `{group_name}`\n"
    "- Your group identity: `{member_name}`\n"
    "- Your role: `{member_description}`\n"
    "- Use group messages to coordinate with other members.\n"
    "- Reply `PASS` when you have no useful continuation."
)


def render_group_chat_prompt(
    prompts_dir: str | Path,
    *,
    group_name: str,
    member_name: str,
    member_description: str = "",
) -> str:
    """Render the group-chat prompt module for one member identity."""
    template_path = Path(prompts_dir) / "group_chat.md"
    if template_path.exists():
        template = template_path.read_text(encoding="utf-8").strip()
    else:
        template = GROUP_CHAT_FALLBACK_TEMPLATE
    return template.format(
        group_name=group_name,
        member_name=member_name,
        member_description=member_description or "unspecified",
    )
