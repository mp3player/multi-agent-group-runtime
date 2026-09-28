"""Prompt selection must agree for embedded builders and application agents."""

from pathlib import Path

import pytest

from application.agent_builder import build_agent
from application.agent_config import AgentAppConfig
from core.system_builder import SystemBuilder, SystemBuilderError
from tests.runtime_fakes import ScriptedModel


def _build_application_prompt(prompts_dir: Path, order_file: str = "order.txt") -> SystemBuilder:
    config = AgentAppConfig.from_mapping({
        "MAS_PROMPTS_DIR": str(prompts_dir),
        "MAS_PROMPT_ORDER_FILE": order_file,
        "MAS_SKILLS_DIR": str(prompts_dir / "missing-skills"),
        "MAS_ENABLE_TOOLS": "false",
    })
    builder = build_agent(config, model=ScriptedModel()).system_builder
    builder.detach_tool_registry()
    return builder


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["base"], ["base"]),
        (
            ["communication", "skills", "workflow", "base", "tool_use"],
            ["base", "workflow", "tool_use", "skills", "communication"],
        ),
        (["zeta", "alpha"], ["alpha", "zeta"]),
    ],
)
def test_missing_order_uses_available_prompt_bundle_consistently(tmp_path, files, expected):
    for name in files:
        (tmp_path / f"{name}.md").write_text(f"# {name}", encoding="utf-8")
    direct = SystemBuilder(prompts_dir=tmp_path)

    assert direct.load_default() == expected
    application = _build_application_prompt(tmp_path)

    assert direct.list_modules() == application.list_modules() == expected
    assert direct.build() == application.build() == "\n\n".join(f"# {name}" for name in expected)


@pytest.mark.parametrize("absolute_order", [False, True])
def test_configured_order_is_shared_by_builder_and_application(tmp_path, absolute_order):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for name in ["base", "extra", "excluded"]:
        (prompts / f"{name}.md").write_text(f"# {name}", encoding="utf-8")
    (prompts / "order.txt").write_text("excluded\n", encoding="utf-8")
    order_file = (tmp_path if absolute_order else prompts) / "custom-order.txt"
    order_file.write_text("# selected prompts\n\nextra\n  base  \n", encoding="utf-8")
    configured_path = str(order_file) if absolute_order else order_file.name
    direct = SystemBuilder(prompts_dir=prompts)

    assert direct.load_default(order_file=configured_path) == ["extra", "base"]
    application = _build_application_prompt(prompts, configured_path)

    assert direct.build() == application.build() == "# extra\n\n# base"


def test_empty_order_does_not_enable_fallback_modules(tmp_path):
    (tmp_path / "base.md").write_text("# Base", encoding="utf-8")
    (tmp_path / "order.txt").write_text("# intentionally empty\n", encoding="utf-8")
    direct = SystemBuilder(prompts_dir=tmp_path)

    assert direct.load_default() == []
    assert direct.build() == _build_application_prompt(tmp_path).build() == ""


def test_missing_explicit_module_raises_public_builder_error(tmp_path):
    (tmp_path / "order.txt").write_text("missing\n", encoding="utf-8")

    with pytest.raises(SystemBuilderError, match="missing.md"):
        SystemBuilder(prompts_dir=tmp_path).load_default()
    with pytest.raises(SystemBuilderError, match="missing.md"):
        _build_application_prompt(tmp_path)
