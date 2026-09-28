"""Malformed or inaccessible skills must not prevent application startup."""

import logging
import os
from pathlib import Path

import pytest

from core.system_builder import SystemBuilder
from prompting.skills import load_skills_from_dir


def _write_skill(root: Path, directory: str, content: bytes) -> Path:
    skill_file = root / directory / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_bytes(content)
    return skill_file


@pytest.fixture
def skill_files(tmp_path):
    root = tmp_path / "skills"
    broken = _write_skill(root, "a-broken", b"---\nname: broken\n---\n")
    valid = _write_skill(
        root,
        "z-valid",
        b"---\nname: usable\ndescription: Available skill.\n---\n",
    )
    return root, broken, valid


def test_invalid_utf8_is_reported_and_valid_sibling_loads(skill_files, caplog):
    root, broken, valid = skill_files
    broken.write_bytes(b"\xff\xfe")

    with caplog.at_level(logging.WARNING):
        result = load_skills_from_dir(root, logger=logging.getLogger(__name__))

    assert result.directory_exists is True
    assert result.loaded_names == ["usable"]
    assert result.skills == [{
        "name": "usable",
        "description": "Available skill.",
        "path": str(valid),
    }]
    assert len(result.errors) == 1
    assert str(broken) in result.errors[0]
    assert "UnicodeDecodeError" in result.errors[0]
    assert "utf-8" in result.errors[0]
    assert result.errors[0] in caplog.text


def test_existing_file_root_returns_a_diagnostic(tmp_path, caplog):
    root = tmp_path / "skills"
    root.write_text("This is a file, not a directory.", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        result = load_skills_from_dir(root, logger=logging.getLogger(__name__))

    assert result.directory_exists is True
    assert result.skills == []
    assert result.loaded_names == []
    assert len(result.errors) == 1
    assert str(root) in result.errors[0]
    assert "NotADirectoryError" in result.errors[0]
    assert result.errors[0] in caplog.text


@pytest.mark.parametrize("operation", ["stat", "iterdir"])
def test_unreadable_root_returns_a_diagnostic(tmp_path, monkeypatch, operation):
    root = tmp_path / "skills"
    root.mkdir()
    target = os if operation == "stat" else Path
    original = getattr(target, operation)

    # Inject the filesystem error so this also works when tests run as root.
    def deny_root(path, *args, **kwargs):
        if path == root or path == str(root):
            raise PermissionError("Permission denied for skills root")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(target, operation, deny_root)

    result = load_skills_from_dir(root)

    assert result.directory_exists is True
    assert result.skills == []
    assert result.loaded_names == []
    assert len(result.errors) == 1
    assert str(root) in result.errors[0]
    assert "PermissionError" in result.errors[0]
    assert "Permission denied" in result.errors[0]


@pytest.mark.parametrize("operation", ["read_text", "file_stat", "directory_stat"])
def test_inaccessible_skill_is_reported_and_valid_sibling_loads(
    skill_files, monkeypatch, operation
):
    root, broken, valid = skill_files
    failed_path = broken.parent if operation == "directory_stat" else broken
    method = "read_text" if operation == "read_text" else "stat"
    target = Path if method == "read_text" else os
    original = getattr(target, method)

    def deny_skill(path, *args, **kwargs):
        if path == failed_path or path == str(failed_path):
            raise PermissionError("Permission denied for skill")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(target, method, deny_skill)

    result = load_skills_from_dir(root)

    assert result.directory_exists is True
    assert result.loaded_names == ["usable"]
    assert result.skills[0]["path"] == str(valid)
    assert len(result.errors) == 1
    assert str(failed_path) in result.errors[0]
    assert "PermissionError" in result.errors[0]


def test_missing_root_preserves_manually_added_skills(tmp_path):
    root = tmp_path / "missing-skills"
    result = load_skills_from_dir(root)
    assert result.directory_exists is False
    assert result.skills == []
    assert result.loaded_names == []
    assert result.errors == []

    builder = SystemBuilder(prompts_dir=tmp_path, skills_dir=root)
    builder.add_skill("manual", "Manual skill.")
    assert builder.load_skills() == []
    assert builder.list_skills() == ["manual"]
    assert builder.skill_load_errors == []


def test_non_skill_entries_are_ignored(tmp_path):
    root = tmp_path / "skills"
    _write_skill(root, "valid", b"---\nname: usable\n---\n")
    (root / "empty-directory").mkdir()
    (root / "ordinary-file").write_text("Not a skill", encoding="utf-8")

    result = load_skills_from_dir(root)

    assert result.directory_exists is True
    assert result.loaded_names == ["usable"]
    assert result.errors == []


def test_application_build_exposes_bad_skill_diagnostics(skill_files, tmp_path, caplog):
    from application.agent_builder import build_agent
    from application.agent_config import AgentAppConfig
    from tests.runtime_fakes import ScriptedModel

    root, broken, _ = skill_files
    broken.write_bytes(b"\xff\xfe")
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "base.md").write_text("Base prompt", encoding="utf-8")
    (prompts / "order.txt").write_text("base\n", encoding="utf-8")
    config = AgentAppConfig.from_mapping({
        "MAS_PROMPTS_DIR": str(prompts),
        "MAS_PROMPT_ORDER_FILE": "order.txt",
        "MAS_SKILLS_DIR": str(root),
        "MAS_ENABLE_TOOLS": "false",
    })

    with caplog.at_level(logging.WARNING, logger="mas.system_builder"):
        agent = build_agent(config, model=ScriptedModel())

    builder = agent.system_builder
    assert builder.list_skills() == ["usable"]
    assert "Available skill." in builder.build()
    assert len(builder.skill_load_errors) == 1
    assert str(broken) in builder.skill_load_errors[0]
    assert "UnicodeDecodeError" in builder.skill_load_errors[0]
    assert builder.skill_load_errors[0] in caplog.text
