"""Existing single-agent composition checks retained from the mixed architecture suite."""

import ast
import inspect
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import application
import observability
import prompting
from application import agent_config as app_config
from application import agent_builder as app_builders
from application import agent_service as app_agent_service
from application import options as app_options
from core import defaults
from core.system_builder import SystemBuilder
from core.usage import UsageMonitor, UsageRecord
from prompting.loader import resolve_prompt_spec_path
from prompting.runtime import PromptRuntime
from prompting.skills import load_skills_from_dir, parse_skill_frontmatter, render_skills
from prompting import specs as prompt_specs
from prompting.tool_renderer import render_tool_registry
from tools import workspace_binding, workspace_handlers, workspace_specs
from tools.audit import ToolAuditLog
from tools.permissions import ToolPermission
from tools.registry import ToolRegistry
from models import ToolCall, User


def _assert_module_does_not_import(path: Path, forbidden: set[str]) -> None:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module not in forbidden, path
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden, path


def test_application_package_imports_without_cli_or_web() -> None:
    assert application.AgentAppConfig is app_config.AgentAppConfig
    _assert_import_without_entrypoints("application")


def _assert_import_without_entrypoints(package: str) -> None:
    # Check package imports independently of collection/execution order.
    result = subprocess.run(
        [sys.executable, "-c", (
            f"import {package}, sys; "
            'assert "main" not in sys.modules; '
            'assert "web_server" not in sys.modules'
        )],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_application_modules_do_not_import_cli_or_web() -> None:
    for path in Path("application").glob("*.py"):
        forbidden = {"main", "web_server"}
        _assert_module_does_not_import(path, forbidden)
        assert "observability" not in path.read_text(), path


def test_removed_application_services_facade_is_absent() -> None:
    assert not Path("application/services.py").exists()


def test_core_runtime_does_not_import_observability() -> None:
    for path in Path("core").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "") != "observability", path
                assert not (node.module or "").startswith("observability."), path
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "observability", path
                    assert not alias.name.startswith("observability."), path


def test_app_config_defaults_match_current_runtime_defaults() -> None:
    config = app_config.AgentAppConfig()

    assert config.llm.timeout == defaults.TIMEOUT
    assert config.llm.max_tokens == defaults.DEFAULT_MAX_TOKENS
    assert config.llm.temperature == defaults.DEFAULT_TEMPERATURE
    assert config.agent.max_turns == defaults.DEFAULT_MAX_TURNS
    assert config.agent.active_message_limit == defaults.ACTIVE_MESSAGE_LIMIT
    assert config.agent.history_message_limit == defaults.HISTORY_MESSAGE_LIMIT
    assert config.agent.run_timeout == defaults.AGENT_RUN_TIMEOUT
    assert config.tools.permission_dry_run is False
    assert config.tools.permission_enforce is False
    assert config.tools.permission_rules == ""
    assert config.tools.audit_jsonl == ""
    assert config.tools.workspace_roots == ""
    assert config.logging.level == defaults.LOG_LEVEL
    assert config.usage.enabled is True


def test_app_config_from_mapping_preserves_current_env_names() -> None:
    config = app_config.AgentAppConfig.from_mapping({
        "BaseURL": "https://llm.example/v1",
        "BaseKey": "secret",
        "BaseModel": "model-x",
        "MAS_SKILLS_DIR": "/tmp/skills",
        "MAS_ENABLE_TOOLS": "false",
        "MAS_TOOL_PERMISSION_DRY_RUN": "true",
        "MAS_TOOL_PERMISSION_ENFORCE": "true",
        "MAS_TOOL_PERMISSION_RULES": "external_effect=approval",
        "MAS_TOOL_AUDIT_JSONL": "/tmp/audit.jsonl",
        "MAS_WORKSPACE_ROOTS": "/tmp/workspace",
        "MAS_USAGE_ENABLED": "false",
    })

    assert config.llm.base_url == "https://llm.example/v1"
    assert config.llm.api_key == "secret"
    assert config.llm.model == "model-x"
    assert config.skills.skills_dir == "/tmp/skills"
    assert config.tools.enable_workspace_tools is False
    assert config.tools.permission_dry_run is True
    assert config.tools.permission_enforce is True
    assert config.tools.permission_rules == "external_effect=approval"
    assert config.tools.audit_jsonl == "/tmp/audit.jsonl"
    assert config.tools.workspace_roots == "/tmp/workspace"
    assert config.usage.enabled is False


def test_app_config_from_env_loads_project_env_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    keys = [
        "BaseURL",
        "BaseKey",
        "BaseModel",
    ]
    old_env = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ.pop(key, None)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join([
            "BaseURL=https://llm.example/v1",
            "BaseKey=secret",
            "BaseModel=model-x",
        ]),
        encoding="utf-8",
    )

    try:
        config = app_config.AgentAppConfig.from_env(env_file)

        assert config.llm.base_url == "https://llm.example/v1"
        assert config.llm.api_key == "secret"
        assert config.llm.model == "model-x"
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_application_builder_consumes_runtime_config(tmp_path) -> None:
    prompts_dir = tmp_path / "prompts"
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "demo"
    prompts_dir.mkdir()
    skill_dir.mkdir(parents=True)
    for spec in prompt_specs.COMMON_PROMPT_MODULES:
        path = prompts_dir / Path(spec.template_path).name
        path.write_text(f"# {spec.name}", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill.\n---\n",
        encoding="utf-8",
    )
    config = app_config.AgentAppConfig(
        llm=app_config.LLMProviderConfig(
            base_url="https://llm.example/v1",
            api_key="secret",
            model="model-x",
            timeout=7,
        ),
        agent=app_config.AgentRuntimeConfig(
            max_turns=4,
            active_message_limit=9,
            history_message_limit=11,
            run_timeout=3,
        ),
        prompt=app_config.PromptConfig(prompts_dir=str(prompts_dir)),
        skills=app_config.SkillsConfig(skills_dir=str(skills_dir)),
        tools=app_config.ToolConfig(
            enable_workspace_tools=False,
            permission_dry_run=True,
            permission_enforce=True,
            permission_rules="external_effect=approval",
            audit_jsonl=str(tmp_path / "audit.jsonl"),
        ),
    )

    agent = app_builders.build_agent(config)

    assert agent.llm.base_url == "https://llm.example/v1"
    assert agent.llm.api_key == "secret"
    assert agent.llm.model == "model-x"
    assert agent.llm.timeout == 7
    assert agent.max_turns == 4
    assert agent.active_message_limit == 9
    assert agent.run_timeout == 3
    assert agent.session.history_limit == 11
    assert agent.system_builder is not None
    assert agent.system_builder.prompts_dir == prompts_dir
    assert agent.system_builder.skills_dir == skills_dir
    assert agent.system_builder.list_modules() == [
        spec.name for spec in prompt_specs.COMMON_PROMPT_MODULES
    ]
    assert agent.system_builder.list_skills() == ["demo"]
    assert agent.tool_executor.runtime.permission_policy.dry_run is True
    assert agent.tool_executor.runtime.permission_policy.enforce is True
    assert agent.tool_executor.runtime.permission_policy.rules == {
        "external_effect": "approval",
    }
    assert agent.registry.tool_audit_log.sink is not None


def test_application_builder_injects_workspace_roots_and_usage_toggle(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    allowed_file = workspace / "allowed.txt"
    outside_file = outside / "outside.txt"
    allowed_file.write_text("allowed", encoding="utf-8")
    outside_file.write_text("outside", encoding="utf-8")
    monitor = UsageMonitor()
    config = app_config.AgentAppConfig(
        llm=app_config.LLMProviderConfig(
            base_url="https://llm.example/v1",
            api_key="secret",
            model="model-x",
        ),
        tools=app_config.ToolConfig(
            workspace_roots=str(workspace),
        ),
        usage=app_config.UsageConfig(enabled=False),
    )

    agent = app_builders.build_agent(config, usage_monitor=monitor)

    runtime = agent.tool_executor.runtime
    assert runtime.workspace_roots == (str(workspace),)
    allowed_result = runtime.execute([
        ToolCall("read-1", "read_file", {"path": str(allowed_file)}),
    ])[0].message
    outside_result = runtime.execute([
        ToolCall("read-2", "read_file", {"path": str(outside_file)}),
    ])[0].message
    assert "allowed" in allowed_result
    assert "Path is outside the allowed workspace" in outside_result
    assert agent.llm.usage_monitor is None


def test_tool_registry_does_not_own_workspace_context() -> None:
    source = Path("tools/registry.py").read_text(encoding="utf-8")
    assert "workspace_roots" not in source
    assert "tools.file_ops" not in source


def test_agent_app_service_session_and_tools_queries(monkeypatch) -> None:
    config = app_config.AgentAppConfig.from_mapping({
        'BaseURL': 'http://127.0.0.1:1/v1', 'BaseKey': 'test-only', 'BaseModel': 'fixture',
    })
    monkeypatch.setattr(app_config.AgentAppConfig, 'from_env', classmethod(lambda cls: config))
    service = app_agent_service.AgentAppService.from_options(
        app_options.AgentServiceOptions(
            max_turns=3,
            enable_tools=False,
            use_stream=False,
        )
    )
    service.agent.session.add(User("hello\nthere"))

    assert service.tool_names() == ['read_context_archive']
    assert service.history_counts() == (2, 2)
    assert service.history_entries()[1].text == "hello there"
    assert service.toggle_stream() is True

    service.clear_session()

    assert service.history_counts()[0] == 1


def test_application_services_expose_tool_audit_queries(monkeypatch) -> None:
    config = app_config.AgentAppConfig.from_mapping({
        'BaseURL': 'http://127.0.0.1:1/v1', 'BaseKey': 'test-only', 'BaseModel': 'fixture',
    })
    monkeypatch.setattr(app_config.AgentAppConfig, 'from_env', classmethod(lambda cls: config))
    agent_service = app_agent_service.AgentAppService.from_options(
        app_options.AgentServiceOptions(
            max_turns=3,
            enable_tools=False,
            use_stream=False,
        )
    )
    agent_service.agent.registry.tool_audit_log.append(
        tool="demo",
        caller="single",
        permission=ToolPermission(side_effect="read_only"),
        arguments={},
        result="ok",
        error=False,
    )

    assert agent_service.tool_audit_records()[0].tool == "demo"


def test_prompting_package_imports_without_cli_or_web() -> None:
    assert prompting.COMMON_PROMPT_MODULES is prompt_specs.COMMON_PROMPT_MODULES
    _assert_import_without_entrypoints("prompting")


def test_system_builder_loads_common_prompt_specs(tmp_path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for spec in prompt_specs.COMMON_PROMPT_MODULES:
        path = prompts_dir / Path(spec.template_path).name
        path.write_text(f"# {spec.name}", encoding="utf-8")

    builder = SystemBuilder(prompts_dir=prompts_dir)
    loaded = builder.load_default_from_specs()

    assert loaded == [spec.name for spec in prompt_specs.COMMON_PROMPT_MODULES]
    assert builder.list_modules() == loaded


def test_system_builder_loads_orders_and_removes_static_modules(tmp_path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "base.md").write_text("# Base", encoding="utf-8")
    (prompts_dir / "extra.md").write_text("# Extra", encoding="utf-8")
    (prompts_dir / "order.txt").write_text(
        "base\n# ignored\nextra\n",
        encoding="utf-8",
    )
    store = SystemBuilder(prompts_dir)

    assert store.load_default() == ["base", "extra"]
    assert store.list_modules() == ["base", "extra"]
    store.add_module("custom", "  # Custom  ")
    assert store.modules["custom"] == "# Custom"
    store.prepend("custom")
    assert store.list_modules()[0] == "custom"
    store.use(["extra", "custom"])
    assert store.build() == "# Extra\n\n# Custom"
    store.remove("custom")
    assert store.list_modules() == ["extra"]


def test_prompt_runtime_build_keeps_dynamic_module_order(tmp_path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    runtime = PromptRuntime.create(prompts_dir)
    runtime.add_module("base", "# Base")
    runtime.add_skill("demo", "Demo skill.", path="/tmp/demo/SKILL.md")
    runtime.add_module("extra", "# Extra")

    assert runtime.build() == (
        "# Base\n\n# Extra\n\n"
        "## Available Skills\n"
        "Use a skill only when its name fits the task or the user asks for it. Read its SKILL.md before relying on details.\n"
        "- demo (/tmp/demo/SKILL.md): Demo skill."
    )


def test_system_builder_preserves_manual_skills_when_directory_is_missing(tmp_path) -> None:
    prompts_dir = tmp_path / "prompts"
    missing_skills = tmp_path / "missing-skills"
    prompts_dir.mkdir()
    (prompts_dir / "base.md").write_text("# Base", encoding="utf-8")
    builder = SystemBuilder(prompts_dir=prompts_dir)

    builder.load("base")
    builder.add_skill("manual", "Manual skill.", path="/tmp/manual/SKILL.md")
    assert builder.load_skills(missing_skills) == []

    assert isinstance(builder, PromptRuntime)
    assert builder.modules == {"base": "# Base"}
    assert builder.order == ["base"]
    assert builder.skills_dir == missing_skills
    assert builder.list_skills() == ["manual"]
    assert "Manual skill" in builder.build()


def test_prompt_loader_resolves_specs_relative_to_prompts_dir(tmp_path) -> None:
    spec = prompt_specs.PromptModuleSpec("custom", "common", "prompts/custom.md")

    assert resolve_prompt_spec_path(tmp_path, spec) == tmp_path / "custom.md"


def test_system_builder_composes_tools_and_skills_in_order() -> None:
    builder = SystemBuilder()
    registry = ToolRegistry()

    def echo(text: str) -> str:
        """Return text."""
        return text

    registry.register(echo)
    builder.add_module("base", "# Base")
    builder.attach_tool_registry(registry)
    builder.add_skill("demo", "A skill.", path="/tmp/demo/SKILL.md")
    assert builder.build() == "\n\n".join([
        "# Base", render_tool_registry(registry), render_skills(builder.skills),
    ])
    builder.detach_tool_registry()
    builder.remove_skill("demo")
    assert builder.build() == "# Base"


def test_prompt_runtime_exposes_facade_facing_api() -> None:
    expected = {
        "add_module",
        "add_skill",
        "attach_tool_registry",
        "detach_tool_registry",
        "has",
        "list_modules",
        "list_skills",
        "load_all",
        "load_default",
        "prepend",
        "remove",
        "remove_skill",
        "use",
    }

    assert expected.issubset(set(dir(PromptRuntime)))


def test_prompt_tool_renderer_keeps_current_shape() -> None:
    registry = ToolRegistry()

    def demo_tool(required: str, optional: str = "") -> str:
        """Demo tool first line.

        More details.
        """
        return f"{required}{optional}"

    registry.register(demo_tool)

    assert render_tool_registry(registry) == (
        "## Available Tools\n"
        "- demo_tool(required, optional?): Demo tool first line."
    )


def test_prompt_skill_loader_and_renderer_keep_current_shape(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: demo\n"
        "description: >\n"
        "  Demo skill first sentence.\n"
        "  Extra detail.\n"
        "---\n"
        "# Demo\n",
        encoding="utf-8",
    )

    result = load_skills_from_dir(tmp_path / "skills")
    text = render_skills(result.skills)

    assert result.directory_exists is True
    assert result.loaded_names == ["demo"]
    assert parse_skill_frontmatter((skill_dir / "SKILL.md").read_text())[0] == "demo"
    assert text == (
        "## Available Skills\n"
        "Use a skill only when its name fits the task or the user asks for it. Read its SKILL.md before relying on details.\n"
        f"- demo ({skill_dir / 'SKILL.md'}): Demo skill first sentence"
    )


def test_workspace_tool_specs_cover_current_default_workspace_tools() -> None:
    assert workspace_specs.workspace_tool_names() == {
        "terminal",
        "read_file",
        "write_file",
        "str_replace",
        "list_dir",
    }
    assert workspace_specs.WORKSPACE_TOOL_SPECS["read_file"].side_effect == "read_only"
    assert workspace_specs.WORKSPACE_TOOL_SPECS["list_dir"].side_effect == "read_only"
    assert (
        workspace_specs.WORKSPACE_TOOL_SPECS["write_file"].side_effect
        == "workspace_mutating"
    )
    assert (
        workspace_specs.WORKSPACE_TOOL_SPECS["str_replace"].side_effect
        == "workspace_mutating"
    )
    assert (
        workspace_specs.WORKSPACE_TOOL_SPECS["terminal"].side_effect
        == "external_effect"
    )
    assert workspace_specs.workspace_tool_spec("read_file") is (
        workspace_specs.WORKSPACE_TOOL_SPECS["read_file"]
    )
    assert [
        spec.name for spec in workspace_specs.workspace_tools_by_effect("read_only")
    ] == ["read_file", "list_dir"]


def test_workspace_tool_architecture_boundaries_are_wired() -> None:
    assert hasattr(workspace_handlers, "WorkspaceToolHandlers")
    assert [
        spec.name for spec in workspace_binding.workspace_tool_specs()
    ] == list(workspace_specs.DEFAULT_WORKSPACE_TOOL_ORDER)
    registry = ToolRegistry()
    workspace_binding.bind_workspace_tools(registry)
    assert registry.names() == list(workspace_specs.DEFAULT_WORKSPACE_TOOL_ORDER)
    assert registry.tool_metadata_map() == {
        "terminal": "external_effect",
        "read_file": "read_only",
        "write_file": "workspace_mutating",
        "str_replace": "workspace_mutating",
        "list_dir": "read_only",
    }
    assert all(
        isinstance(permission, ToolPermission)
        for permission in registry.tool_metadata_map().values()
    )


def test_workspace_tool_architecture_modules_do_not_import_cli_or_web() -> None:
    for path in Path("tools").glob("workspace_*.py"):
        _assert_module_does_not_import(path, {"main", "web_server"})


def test_tool_permission_and_audit_modules_do_not_import_runtime_or_entrypoints() -> None:
    forbidden = {
        "application",
        "observability",
        "main",
        "web_server",
        "core.agent",
        "core.group",
        "core.group_runtime",
    }
    for path in (Path("tools/permissions.py"), Path("tools/audit.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module not in forbidden, path
                assert not module.startswith("core.group_runtime"), path
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in forbidden, path
                    assert not alias.name.startswith("core.group_runtime"), path


def test_observability_package_imports_without_cli_or_web() -> None:
    assert hasattr(observability, "tool_audit_view")
    _assert_import_without_entrypoints("observability")


def test_observability_usage_and_log_views_are_plain_dicts(tmp_path) -> None:
    record = UsageRecord(
        label="A",
        model="model-x",
        prompt_tokens=1,
        completion_tokens=2,
        total_tokens=3,
    )
    log_file = tmp_path / "debug.log"
    log_file.write_text("hello", encoding="utf-8")

    usage = observability.usage_record_view(record)
    logs = observability.debug_log_metadata_view(log_file)

    assert usage["label"] == "A"
    assert usage["total_tokens"] == 3
    assert logs["exists"] is True
    assert logs["size"] == 5


def test_observability_usage_formatting_helpers_keep_shapes() -> None:
    monitor = UsageMonitor()
    monitor.record(
        "A",
        {
            "model": "model-x",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_tokens_details": {"cached_tokens": 4},
                "completion_tokens_details": {"reasoning_tokens": 2},
                "prompt_cache_hit_tokens": 3,
                "prompt_cache_miss_tokens": 7,
            },
        },
    )

    lines = observability.usage_report_lines(monitor)
    payload = observability.usage_payload_view(monitor)

    assert lines == [
        "Recent calls:",
        "- #1 A: prompt=10, cached=4, hit=3, miss=7, completion=5, reasoning=2, total=15",
        "Summary:",
        "- A: prompt=10, cached=4, hit=3, miss=7, completion=5, reasoning=2, total=15",
    ]
    assert payload["records"][0]["label"] == "A"
    assert payload["summary"][0]["total_tokens"] == 15


def test_observability_tool_audit_view_is_plain_dict() -> None:
    log = ToolAuditLog()
    record = log.append(
        tool="read_file",
        caller="A",
        permission=ToolPermission(side_effect="read_only"),
        arguments={"path": "demo.txt"},
        result="content",
        error=False,
    )

    assert observability.tool_audit_record_view(record)["tool"] == "read_file"
    assert observability.tool_audit_view(log) == [record.to_dict()]
    assert observability.tool_audit_report_lines([]) == ["(No tool audit records yet)"]
    assert observability.tool_audit_report_lines([record]) == [
        "Recent tool audit records:",
        (
            f"- #{record.id} read_file caller=A side_effect=read_only "
            f"decision=allow status=ok result=content timestamp={record.timestamp}"
        ),
    ]


def test_application_service_modules_are_direct_package_exports() -> None:
    assert application.AgentAppService is app_agent_service.AgentAppService
    assert application.HistoryEntry is app_agent_service.HistoryEntry
    assert application.AgentServiceOptions is app_options.AgentServiceOptions


def test_application_package_exports_stable_service_api() -> None:
    assert {
        "AgentAppService",
        "AgentServiceOptions",
        "HistoryEntry",
    }.issubset(set(application.__all__))


def test_application_builder_interfaces_are_declared_and_wired() -> None:
    assert list(inspect.signature(app_builders.build_agent).parameters) == [
        "config",
        "model",
        "usage_monitor",
        "usage_label",
        "llm_config",
    ]
    agent = app_builders.build_agent(app_config.AgentAppConfig(
        llm=app_config.LLMProviderConfig(base_url='http://127.0.0.1:1/v1', api_key='test-only', model='fixture'),
    ))
    assert agent.registry.names() == [
        "terminal",
        "read_file",
        "write_file",
        "str_replace",
        "list_dir",
        "read_context_archive",
    ]


def test_prompt_renderer_does_not_import_core_runtime() -> None:
    for path in (
        Path("prompting/runtime.py"),
        Path("prompting/skills.py"),
        Path("prompting/tool_renderer.py"),
    ):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("core."), path
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("core."), path


def test_runtime_and_application_do_not_import_removed_migration_paths() -> None:
    removed_modules = {"application.services", "core.factory"}
    for root in (Path("application"), Path("core")):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    assert module not in removed_modules, path
                    if path.parts[:2] == ("core", "agent_runtime"):
                        assert module != "core.llm", path
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name not in removed_modules, path
                        if path.parts[:2] == ("core", "agent_runtime"):
                            assert alias.name != "core.llm", path


def test_removed_core_migration_paths_are_absent() -> None:
    assert not Path("core/factory.py").exists()
