"""Prompt builders honor custom rendering at the final prompt boundary."""

from prompting.runtime import PromptRuntime
from tools.registry import ToolRegistry


def test_build_uses_subclass_public_renderers_and_default_sections() -> None:
    class CustomPrompt(PromptRuntime):
        def render_tools(self) -> str:
            return "Custom tools\n" + super().render_tools()

        def render_skills(self) -> str:
            return "Custom skills\n" + super().render_skills()

    def lookup(query: str) -> str:
        """Look up a record."""
        return query

    registry = ToolRegistry()
    registry.register(lookup)
    prompt = CustomPrompt.create()
    prompt.add_module("base", "BASE")
    prompt.attach_tool_registry(registry)
    prompt.add_skill("review", "Review a record.", path="/skills/review/SKILL.md")

    assert prompt.build() == (
        "BASE\n\n"
        "Custom tools\n## Available Tools\n- lookup(query): Look up a record.\n\n"
        "Custom skills\n## Available Skills\n"
        "Use a skill only when its name fits the task or the user asks for it. "
        "Read its SKILL.md before relying on details.\n"
        "- review (/skills/review/SKILL.md): Review a record."
    )


def test_build_uses_instance_public_renderer_overrides() -> None:
    prompt = PromptRuntime.create()
    prompt.add_module("base", "BASE")
    prompt.render_tools = lambda: "INSTANCE TOOL"  # type: ignore[method-assign]
    prompt.render_skills = lambda: "INSTANCE SKILL"  # type: ignore[method-assign]

    assert prompt.build() == "BASE\n\nINSTANCE TOOL\n\nINSTANCE SKILL"


def test_build_keeps_private_renderer_override_compatible() -> None:
    class PrivatePrompt(PromptRuntime):
        def _render_tools(self) -> str:
            return "PRIVATE TOOL"

        def _render_skills(self) -> str:
            return "PRIVATE SKILL"

    prompt = PrivatePrompt.create()
    prompt.add_module("base", "BASE")

    assert prompt.build() == "BASE\n\nPRIVATE TOOL\n\nPRIVATE SKILL"
