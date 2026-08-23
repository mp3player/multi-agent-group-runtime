import ast
import inspect
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import application
import observability
import prompting
from application import config as app_config
from application import builders as app_builders
from application import agent_service as app_agent_service
from application import group_service as app_group_service
from application import member_config_service as app_member_config_service
from application import options as app_options
from core.config import MASConfig
from core.group import GroupChat
from core.group_policy import registered_dispatch_policies
from core.group_tool_specs import GROUP_TOOL_EFFECTS
from core.member_config import MemberConfig, load_member_configs
from core.system_builder import SystemBuilder
from prompting.loader import resolve_prompt_spec_path
from prompting.renderer import render_group_chat_prompt
from prompting.runtime import PromptModuleStore, PromptRuntime
from prompting.skills import (
    load_skills_from_dir,
    parse_skill_frontmatter,
    render_skills,
)
from prompting import specs as prompt_specs
from prompting.tool_renderer import render_tool_registry
from tools import workspace_binding, workspace_handlers, workspace_specs
from tools.audit import ToolAuditLog
from tools.permissions import ToolPermission
from tools.registry import ToolRegistry
from core.usage import UsageRecord
from core.usage import UsageMonitor
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
    assert application.AppConfig is app_config.AppConfig
    assert "main" not in sys.modules
    assert "web_server" not in sys.modules


def test_application_modules_do_not_import_cli_or_web() -> None:
    for path in Path("application").glob("*.py"):
        forbidden = {"main", "web_server"}
        _assert_module_does_not_import(path, forbidden)
        if path.name != "group_service.py":
            assert "observability" not in path.read_text(), path


def test_application_service_modules_are_direct_package_exports() -> None:
    assert application.AgentAppService is app_agent_service.AgentAppService
    assert application.HistoryEntry is app_agent_service.HistoryEntry
    assert application.GroupAppService is app_group_service.GroupAppService
    assert application.AgentServiceOptions is app_options.AgentServiceOptions
    assert application.GroupServiceOptions is app_options.GroupServiceOptions
    assert (
        application.load_group_member_configs
        is app_member_config_service.load_group_member_configs
    )
    assert application.member_runtime_configs is (
        app_member_config_service.member_runtime_configs
    )
    assert application.member_config_from_command is (
        app_member_config_service.member_config_from_command
    )
    assert application.app_config_from_options is (
        app_member_config_service.app_config_from_options
    )


def test_removed_application_services_facade_is_absent() -> None:
    assert not Path("application/services.py").exists()


def test_application_package_exports_stable_service_api() -> None:
    assert {
        "AgentAppService",
        "AgentServiceOptions",
        "GroupAppService",
        "GroupServiceOptions",
        "HistoryEntry",
        "app_config_from_options",
        "load_group_member_configs",
        "member_config_from_command",
        "member_runtime_configs",
    }.issubset(set(application.__all__))


def test_removed_core_migration_paths_are_absent() -> None:
    for path in (
        "core/factory.py",
        "core/group_events.py",
        "core/group_memory.py",
        "core/group_scheduler.py",
        "core/group_state.py",
        "core/group_stats.py",
        "core/group_tool_handlers.py",
        "core/group_views.py",
    ):
        assert not Path(path).exists(), path


def test_cli_and_web_use_application_layer_for_construction() -> None:
    for path in (Path("main.py"), Path("web_server.py")):
        _assert_module_does_not_import(
            path,
            {
                "application.builders",
                "application.config",
                "core.member_config.load_member_configs",
                "core.member_config.merge_member_configs",
            },
        )
        source = path.read_text()
        assert "load_member_configs" not in source
        assert "merge_member_configs" not in source
        assert "application.services" not in source
    assert "application.agent_service" in Path("main.py").read_text()
    assert "application.group_service" in Path("main.py").read_text()
    assert "application.group_service" in Path("web_server.py").read_text()


def test_entrypoints_do_not_import_runtime_types_directly() -> None:
    for path in (Path("main.py"), Path("web_server.py")):
        _assert_module_does_not_import(path, {"core"})
    main_source = Path("main.py").read_text()
    web_source = Path("web_server.py").read_text()

    assert "from core import" not in main_source
    assert "from core import" not in web_source
    assert "def run_async(" not in main_source
    assert "def run_group_sync(" not in main_source
    assert "def print_group_turns(" not in main_source
    assert "def _print_group_turn(" not in main_source
    assert "GroupChat" not in web_source
    assert "GroupMember" not in web_source
    assert "GroupMessage" not in web_source


def test_cli_and_web_use_observability_not_core_group_views() -> None:
    for path in (Path("main.py"), Path("web_server.py")):
        source = path.read_text()
        assert "UsageRecord" not in source
    web_source = Path("web_server.py").read_text()
    for forbidden in ("group_member_view", "group_message_view", "transcript_view"):
        assert forbidden not in web_source


def test_group_cli_audit_uses_application_and_observability() -> None:
    source = Path("main.py").read_text()

    assert "/audit [N]" in source
    assert "/config" in source
    assert "/debug [N]" in source
    assert "def print_tool_audit_report" in source
    assert "def print_debug_report" in source
    assert "tool_audit_report_lines" in source
    assert "service.tool_audit_records" in source
    assert "service.config_report_lines" in source
    assert "service.debug_report_lines" in source
    assert "tool_audit_log" not in source


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


def test_runtime_and_application_do_not_import_removed_migration_paths() -> None:
    removed_modules = {
        "application.services",
        "core.factory",
        "core.group_events",
        "core.group_memory",
        "core.group_scheduler",
        "core.group_state",
        "core.group_stats",
        "core.group_tool_handlers",
        "core.group_views",
    }
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


def test_app_config_defaults_match_current_runtime_defaults() -> None:
    config = app_config.AppConfig()

    assert config.llm.timeout == MASConfig.TIMEOUT
    assert config.llm.max_tokens == MASConfig.DEFAULT_MAX_TOKENS
    assert config.llm.temperature == MASConfig.DEFAULT_TEMPERATURE
    assert config.agent.max_turns == MASConfig.DEFAULT_MAX_TURNS
    assert config.agent.active_message_limit == MASConfig.ACTIVE_MESSAGE_LIMIT
    assert config.agent.history_message_limit == MASConfig.HISTORY_MESSAGE_LIMIT
    assert config.agent.run_timeout == MASConfig.AGENT_RUN_TIMEOUT
    assert config.group.transcript_limit == MASConfig.TRANSCRIPT_LIMIT
    assert config.group.max_dispatch_rounds == MASConfig.MAX_DISPATCH_ROUNDS
    assert config.group.run_timeout == MASConfig.GROUP_RUN_TIMEOUT
    assert config.group.events_jsonl == ""
    assert config.tools.permission_dry_run is False
    assert config.tools.permission_enforce is False
    assert config.tools.permission_rules == ""
    assert config.tools.audit_jsonl == ""
    assert config.tools.workspace_roots == ""
    assert config.logging.level == MASConfig.LOG_LEVEL
    assert config.usage.enabled is True


def test_app_config_from_mapping_preserves_current_env_names() -> None:
    config = app_config.AppConfig.from_mapping({
        "BaseURL": "https://llm.example/v1",
        "BaseKey": "secret",
        "BaseModel": "model-x",
        "MAS_GROUP_DISPATCH_POLICY": "broadcast_feedback",
        "MAS_SKILLS_DIR": "/tmp/skills",
        "MAS_ENABLE_TOOLS": "false",
        "MAS_TOOL_PERMISSION_DRY_RUN": "true",
        "MAS_TOOL_PERMISSION_ENFORCE": "true",
        "MAS_TOOL_PERMISSION_RULES": "external_effect=approval",
        "MAS_TOOL_AUDIT_JSONL": "/tmp/audit.jsonl",
        "MAS_GROUP_EVENTS_JSONL": "/tmp/group_events.jsonl",
        "MAS_WORKSPACE_ROOTS": "/tmp/workspace",
        "MAS_USAGE_ENABLED": "false",
    })

    assert config.llm.base_url == "https://llm.example/v1"
    assert config.llm.api_key == "secret"
    assert config.llm.model == "model-x"
    assert config.group.dispatch_policy == "broadcast_feedback"
    assert config.group.events_jsonl == "/tmp/group_events.jsonl"
    assert config.skills.skills_dir == "/tmp/skills"
    assert config.tools.enable_workspace_tools is False
    assert config.tools.permission_dry_run is True
    assert config.tools.permission_enforce is True
    assert config.tools.permission_rules == "external_effect=approval"
    assert config.tools.audit_jsonl == "/tmp/audit.jsonl"
    assert config.tools.workspace_roots == "/tmp/workspace"
    assert config.usage.enabled is False


def test_app_config_from_env_loads_project_env_file(tmp_path) -> None:
    keys = [
        "BaseURL",
        "BaseKey",
        "BaseModel",
        "MAS_GROUP_DISPATCH_POLICY",
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
            "MAS_GROUP_DISPATCH_POLICY=on_demand",
        ]),
        encoding="utf-8",
    )

    try:
        config = app_config.AppConfig.from_env(env_file)

        assert config.llm.base_url == "https://llm.example/v1"
        assert config.llm.api_key == "secret"
        assert config.llm.model == "model-x"
        assert config.group.dispatch_policy == "on_demand"
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_application_builder_interfaces_are_declared_and_wired() -> None:
    assert list(inspect.signature(app_builders.build_agent).parameters) == [
        "config",
        "usage_monitor",
        "usage_label",
        "llm_config",
    ]
    assert list(inspect.signature(app_builders.build_group).parameters) == [
        "config",
        "usage_monitor",
    ]
    assert list(inspect.signature(app_builders.add_group_member).parameters) == [
        "group",
        "member_config",
        "app_config",
        "usage_monitor",
    ]
    agent = app_builders.build_agent(app_config.AppConfig())

    assert agent.registry.names() == [
        "terminal",
        "read_file",
        "write_file",
        "str_replace",
        "list_dir",
    ]


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
    config = app_config.AppConfig(
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
        group=app_config.GroupRuntimeConfig(events_jsonl=str(tmp_path / "events.jsonl")),
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

    group = app_builders.build_group(config)
    group.add_message("system", "hello", kind="system")

    events_jsonl = tmp_path / "events.jsonl"
    assert events_jsonl.exists()
    assert '"kind": "message"' in events_jsonl.read_text(encoding="utf-8")


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
    config = app_config.AppConfig(
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
    assert "路径不在允许的工作区内" in outside_result
    assert agent.llm.usage_monitor is None


def test_tool_registry_does_not_own_workspace_context() -> None:
    source = Path("tools/registry.py").read_text(encoding="utf-8")
    assert "workspace_roots" not in source
    assert "tools.file_ops" not in source


def test_application_builder_constructs_group_members_and_policy() -> None:
    config = (
        app_config.AppConfig()
        .with_agent_options(max_turns=3, enable_tools=False)
        .with_group_options(
            name="review",
            max_dispatch_rounds=7,
            dispatch_policy="on_demand",
        )
        .with_members((
            app_config.MemberRuntimeConfig(
                "Leader",
                "coordinates work",
                base_url="https://leader.example/v1",
                api_key="leader-key",
                model="leader-model",
            ),
        ))
    )

    group = app_builders.build_group(config)

    assert group.name == "review"
    assert group.max_dispatch_rounds == 7
    assert group.dispatch_policy.name == "on_demand"
    assert list(group.members) == ["Leader"]
    assert group.members["Leader"].description == "coordinates work"
    assert group.members["Leader"].agent.llm.base_url == "https://leader.example/v1"
    assert group.members["Leader"].agent.llm.api_key == "leader-key"
    assert group.members["Leader"].agent.llm.model == "leader-model"
    assert group.members["Leader"].agent.max_turns == 3
    assert group.members["Leader"].agent.tool_executor.runtime.caller == "Leader"
    assert not {
        "terminal",
        "read_file",
        "write_file",
        "str_replace",
        "list_dir",
    }.intersection(group.members["Leader"].agent.registry.names())


def test_application_services_wrap_current_entrypoint_construction(tmp_path) -> None:
    member_path = tmp_path / "members.json"
    service = app_group_service.GroupAppService(
        group_name="review",
        member_configs=[MemberConfig("Leader", "coordinates work")],
        member_config_path=member_path,
        max_turns=3,
        enable_tools=False,
        group_max_rounds=7,
    )

    assert service.group.name == "review"
    assert service.group.max_dispatch_rounds == 7
    assert list(service.group.members) == ["Leader"]
    assert service.group.members["Leader"].description == "coordinates work"

    member = service.add_member(name="Dev", description="implements changes")

    assert member.name == "Dev"
    assert load_member_configs(member_path) == [
        MemberConfig("Leader", "coordinates work"),
        MemberConfig("Dev", "implements changes"),
    ]


def test_group_service_options_load_and_merge_members(tmp_path) -> None:
    member_path = tmp_path / "members.json"
    missing_path = tmp_path / "missing.json"
    member_path.write_text(
        '{"members":[{"name":"Leader","description":"coordinates work"}]}',
        encoding="utf-8",
    )

    assert app_member_config_service.load_group_member_configs(missing_path, ("Dev",)) == [
        MemberConfig("Dev", ""),
    ]
    assert app_member_config_service.load_group_member_configs(member_path, ("Leader", "Dev")) == [
        MemberConfig("Leader", "coordinates work"),
        MemberConfig("Dev", ""),
    ]


def test_group_app_service_from_options_preserves_runtime_shape(tmp_path) -> None:
    member_path = tmp_path / "members.json"
    member_path.write_text(
        '{"members":[{"name":"Leader","description":"coordinates work"}]}',
        encoding="utf-8",
    )
    service = app_group_service.GroupAppService.from_options(
        app_options.GroupServiceOptions(
            group_name="review",
            member_config_path=member_path,
            extra_member_names=("Dev",),
            max_turns=3,
            enable_tools=False,
            group_max_rounds=7,
            persist_dynamic_members=False,
        )
    )

    assert service.group.name == "review"
    assert service.group.max_dispatch_rounds == 7
    assert list(service.group.members) == ["Leader", "Dev"]
    assert service.group.members["Leader"].description == "coordinates work"
    assert service.group.members["Dev"].description == "Dev 是群组 review 中的协作 Agent。"


def test_group_app_service_dynamic_member_persistence_modes(tmp_path) -> None:
    cli_path = tmp_path / "cli-members.json"
    web_path = tmp_path / "web-members.json"

    cli_service = app_group_service.GroupAppService.from_options(
        app_options.GroupServiceOptions(
            group_name="default",
            member_config_path=cli_path,
            max_turns=1,
            enable_tools=False,
            group_max_rounds=5,
            persist_dynamic_members=False,
        )
    )
    web_service = app_group_service.GroupAppService.from_options(
        app_options.GroupServiceOptions(
            group_name="default",
            member_config_path=web_path,
            max_turns=1,
            enable_tools=False,
            group_max_rounds=5,
            persist_dynamic_members=True,
        )
    )

    cli_service.add_member(name="Dev", description="implements changes")
    web_service.add_member(name="Dev", description="implements changes")

    assert not cli_path.exists()
    assert load_member_configs(web_path) == [
        MemberConfig("Dev", "implements changes"),
    ]


def test_group_app_service_clear_reset_and_usage_semantics() -> None:
    service = app_group_service.GroupAppService(
        group_name="review",
        member_configs=[],
        member_config_path=None,
        max_turns=3,
        enable_tools=False,
        group_max_rounds=7,
    )
    service.usage_monitor.record("A", {"usage": {"total_tokens": 1}})
    service.group.add_message("user", "hello", kind="user")

    service.clear_group()

    assert service.group.messages == []
    assert len(service.usage_monitor.records()) == 1

    old_group = service.group
    service.reset_group()

    assert service.group is not old_group
    assert service.group.name == "review"
    assert len(service.usage_monitor.records()) == 1


def test_group_app_service_payload_views_keep_current_shapes() -> None:
    service = app_group_service.GroupAppService(
        group_name="review",
        member_configs=[MemberConfig("Leader", "coordinates work")],
        member_config_path=None,
        max_turns=3,
        enable_tools=False,
        group_max_rounds=7,
    )
    service.group.add_message("user", "hello", kind="user")
    service.usage_monitor.record(
        "Leader",
        {"model": "model-x", "usage": {"total_tokens": 3}},
    )
    member = service.group.members["Leader"]
    message = service.group.messages[-1]

    state = service.state_payload(busy=False)
    history = service.history_payload()
    usage = service.usage_payload()
    lines = service.usage_report_lines()
    member_payload = service.member_payload(member)
    message_payload = service.message_payload(message)

    assert set(state) == {"busy", "group", "members"}
    assert state["busy"] is False
    assert state["group"] == "review"
    assert state["members"][0] == member_payload
    assert set(member_payload) == {
        "name",
        "description",
        "enabled",
        "status",
        "unread",
        "dispatchable",
        "tools",
        "model",
        "url",
    }
    assert history == {"messages": [message_payload]}
    assert set(message_payload) == {
        "id",
        "sender",
        "content",
        "kind",
        "round_index",
        "mentions",
        "propagate",
        "dispatch_mode",
    }
    assert set(usage) == {"records", "summary"}
    assert usage["records"][0]["label"] == "Leader"
    assert lines == [
        "最近调用:",
        "- #1 Leader: prompt=0, cached=0, hit=0, miss=0, completion=0, reasoning=0, total=3",
        "汇总:",
        "- Leader: prompt=0, cached=0, hit=0, miss=0, completion=0, reasoning=0, total=3",
    ]

    config_lines = service.config_report_lines()
    snapshot = service.debug_snapshot(limit=3)
    debug_lines = service.debug_report_lines(limit=3)

    assert config_lines[0] == "Runtime config:"
    assert any("policy=" in line for line in config_lines)
    assert set(snapshot) == {
        "group",
        "config",
        "members",
        "memory",
        "stats",
        "events",
        "audit",
        "usage",
    }
    assert snapshot["group"]["name"] == "review"
    assert snapshot["members"][0]["name"] == "Leader"
    assert debug_lines[0] == "Debug snapshot:"
    assert any("recent_events" in line for line in debug_lines)


def test_agent_app_service_session_and_tools_queries() -> None:
    service = app_agent_service.AgentAppService.from_options(
        app_options.AgentServiceOptions(
            max_turns=3,
            enable_tools=False,
            use_stream=False,
        )
    )
    service.agent.session.add(User("hello\nthere"))

    assert service.tool_names() == []
    assert service.history_counts() == (2, 2)
    assert service.history_entries()[1].text == "hello there"
    assert service.toggle_stream() is True

    service.clear_session()

    assert service.history_counts()[0] == 1


def test_application_services_expose_tool_audit_queries() -> None:
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

    group_service = app_group_service.GroupAppService(
        group_name="review",
        member_configs=[MemberConfig("Leader", "coordinates work")],
        member_config_path=None,
        max_turns=3,
        enable_tools=False,
        group_max_rounds=7,
    )
    member = group_service.group.members["Leader"]
    member.agent.registry.tool_audit_log.append(
        tool="group_status",
        caller="Leader",
        permission=ToolPermission(side_effect="read_only"),
        arguments={},
        result="ok",
        error=False,
    )

    assert group_service.tool_audit_records()[0].tool == "group_status"
    assert group_service.tool_audit_payload()["records"][0]["caller"] == "Leader"


def test_prompting_package_imports_without_cli_or_web() -> None:
    assert prompting.COMMON_PROMPT_MODULES is prompt_specs.COMMON_PROMPT_MODULES
    assert "main" not in sys.modules
    assert "web_server" not in sys.modules


def test_common_prompt_specs_do_not_include_group_only_modules() -> None:
    common_scopes = {module.scope for module in prompt_specs.COMMON_PROMPT_MODULES}
    group_names = prompt_specs.group_module_names()

    assert common_scopes == {"common"}
    assert "group_chat" not in prompt_specs.common_module_names()
    assert group_names
    assert prompt_specs.common_module_names().isdisjoint(group_names)


def test_group_prompt_specs_cover_declared_group_tools() -> None:
    assert prompt_specs.group_tool_prompt_names() == set(GROUP_TOOL_EFFECTS)


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


def test_prompt_module_store_matches_system_builder_static_behavior(tmp_path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "base.md").write_text("# Base", encoding="utf-8")
    (prompts_dir / "extra.md").write_text("# Extra", encoding="utf-8")
    (prompts_dir / "order.txt").write_text(
        "base\n# ignored\nextra\n",
        encoding="utf-8",
    )
    store = PromptModuleStore(prompts_dir)

    assert store.load_default() == ["base", "extra"]
    assert store.list_modules() == ["base", "extra"]
    store.add("custom", "  # Custom  ")
    assert store.modules["custom"] == "# Custom"
    store.prepend("custom")
    assert store.list_modules()[0] == "custom"
    store.use(["extra", "custom"])
    assert store.render() == ["# Extra", "# Custom"]
    store.remove("custom")
    assert store.list_modules() == ["extra"]


def test_prompt_runtime_build_keeps_dynamic_module_order(tmp_path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "group_chat.md").write_text(
        "Group {group_name} / {member_name} / {member_description}",
        encoding="utf-8",
    )
    runtime = PromptRuntime.create(prompts_dir)
    runtime.store.add("base", "# Base")
    runtime.state.add_skill("demo", "Demo skill.", path="/tmp/demo/SKILL.md")
    runtime.state.enable_group_chat(
        group_name="G",
        member_name="A",
        member_description="role",
    )

    assert runtime.build() == (
        "# Base\n\n"
        "## Available Skills\n"
        "Use a skill only when its name fits the task or the user asks for it. Read its SKILL.md before relying on details.\n"
        "- demo (/tmp/demo/SKILL.md): Demo skill.\n\n"
        "Group G / A / role"
    )


def test_system_builder_public_facade_delegates_to_prompt_runtime(tmp_path) -> None:
    prompts_dir = tmp_path / "prompts"
    missing_skills = tmp_path / "missing-skills"
    prompts_dir.mkdir()
    (prompts_dir / "base.md").write_text("# Base", encoding="utf-8")
    builder = SystemBuilder(prompts_dir=prompts_dir)

    builder.load("base")
    builder.add_skill("manual", "Manual skill.", path="/tmp/manual/SKILL.md")
    assert builder.load_skills(missing_skills) == []

    assert isinstance(builder.runtime, PromptRuntime)
    assert builder.modules == {"base": "# Base"}
    assert builder.order == ["base"]
    assert builder.skills_dir == missing_skills
    assert builder.list_skills() == ["manual"]
    assert "Manual skill" in builder.build()


def test_prompt_loader_resolves_specs_relative_to_prompts_dir(tmp_path) -> None:
    spec = prompt_specs.PromptModuleSpec("custom", "common", "prompts/custom.md")

    assert resolve_prompt_spec_path(tmp_path, spec) == tmp_path / "custom.md"


def test_group_prompt_renderer_keeps_current_identity_shape(tmp_path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "group_chat.md").write_text(
        "# Group\n{group_name}|{member_name}|{member_description}",
        encoding="utf-8",
    )

    text = render_group_chat_prompt(
        prompts_dir,
        group_name="review",
        member_name="Leader",
        member_description="coordinates",
    )

    assert text == "# Group\nreview|Leader|coordinates"


def test_prompt_renderer_does_not_import_core_runtime() -> None:
    for path in (
        Path("prompting/renderer.py"),
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


def test_system_builder_delegates_skill_and_tool_prompt_details() -> None:
    source = inspect.getsource(SystemBuilder)

    assert "self.runtime.render_tools()" in source
    assert "self.runtime.load_skills(skills_dir" in source
    assert "parse_skill_frontmatter(text)" in source
    assert "self.runtime.render_skills()" in source
    assert "self.runtime.build()" in source
    assert "self.runtime.store" not in source
    assert "self.runtime.state" not in source
    assert "to_openai_tools()" not in source
    assert "block_lines" not in source


def test_prompt_runtime_exposes_facade_facing_api() -> None:
    expected = {
        "add_module",
        "add_skill",
        "attach_tool_registry",
        "detach_tool_registry",
        "disable_group_chat",
        "enable_group_chat",
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


def test_dispatch_policy_prompt_hints_cover_registered_policies() -> None:
    assert set(prompt_specs.DISPATCH_POLICY_HINTS) == set(registered_dispatch_policies())
    for name, hint in prompt_specs.DISPATCH_POLICY_HINTS.items():
        assert hint.policy_name == name
        assert hint.text.strip()


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
    assert hasattr(observability, "transcript_view")
    assert "main" not in sys.modules
    assert "web_server" not in sys.modules


def test_observability_views_wrap_current_group_objects_without_mutation() -> None:
    group = GroupChat(name="ObserveGroup")
    group.add_message("system", "hello", kind="system", propagate=False)
    before_messages = list(group.messages)
    before_events = group.events.to_dicts()

    transcript = observability.transcript_view(group.messages)
    events = observability.group_events_view(group.events)
    stats = observability.group_stats_view(group.stats)

    assert transcript[-1]["content"] == "hello"
    assert events[-1]["kind"] == "message"
    assert stats["pass_messages"] == 0
    assert list(group.messages) == before_messages
    assert group.events.to_dicts() == before_events


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
        "最近调用:",
        "- #1 A: prompt=10, cached=4, hit=3, miss=7, completion=5, reasoning=2, total=15",
        "汇总:",
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
    assert observability.tool_audit_report_lines([]) == ["(暂无 tool audit 记录)"]
    assert observability.tool_audit_report_lines([record]) == [
        "最近 tool audit:",
        (
            f"- #{record.id} read_file caller=A side_effect=read_only "
            f"decision=allow status=ok result=content timestamp={record.timestamp}"
        ),
    ]


def test_roadmap_records_current_implementation_status() -> None:
    text = Path("ROADMAP.md").read_text()

    assert "## Current Implementation Status" in text
    for expected in (
        "application services",
        "prompt runtime",
        "tool permission/audit v1",
        "group dispatch policies",
        "legacy core/application compatibility facades have been removed",
        "interactive tool approval",
        "database-backed audit querying",
        "Web audit UI",
    ):
        assert expected in text


def test_readme_documents_runtime_operations() -> None:
    text = Path("README.md").read_text()
    operations = Path("docs/operations.md").read_text()
    env = Path(".env.example").read_text()

    for expected in (
        "docs/configuration.md",
        "docs/operations.md",
        "docs/modules.md",
        "docs/dispatch.md",
    ):
        assert expected in text
    for expected in (
        "/audit [N]",
        "MAS_TOOL_PERMISSION_DRY_RUN",
        "MAS_TOOL_PERMISSION_ENFORCE",
        "MAS_TOOL_PERMISSION_RULES",
        "MAS_TOOL_AUDIT_JSONL",
        "MAS_GROUP_EVENTS_JSONL",
        "/config",
        "/debug [N]",
        "Interactive permission approval UI",
        "Web audit page",
    ):
        assert expected in operations
    for expected in (
        "MAS_TOOL_PERMISSION_DRY_RUN",
        "MAS_TOOL_PERMISSION_ENFORCE",
        "MAS_TOOL_PERMISSION_RULES",
        "MAS_TOOL_AUDIT_JSONL",
        "MAS_GROUP_EVENTS_JSONL",
    ):
        assert expected in env


def test_public_docs_are_english() -> None:
    for path in (
        Path("README.md"),
        Path("docs/configuration.md"),
        Path("docs/operations.md"),
        Path("docs/modules.md"),
        Path("docs/dispatch.md"),
    ):
        assert not re.search(r"[\u4e00-\u9fff]", path.read_text(encoding="utf-8"))


def test_web_state_payload_shape_stays_stable(tmp_path) -> None:
    import logging
    import web_server

    web_server.setup_logging = lambda: logging.getLogger("mas.web.test")

    state = web_server.WebState(
        group_name="default",
        member_config_path=tmp_path / "members.json",
        extra_member_names=("A",),
        max_turns=2,
        enable_tools=False,
        group_max_rounds=5,
    )
    handler = object.__new__(web_server.Handler)
    handler.state = state
    state_payload = handler._state_payload()
    usage = web_server.usage_payload(state.usage_monitor)

    assert set(state_payload) == {"busy", "group", "members"}
    assert state_payload["busy"] is False
    assert state_payload["group"] == "default"
    assert set(state_payload["members"][0]) == {
        "name",
        "description",
        "enabled",
        "status",
        "unread",
        "dispatchable",
        "tools",
        "model",
        "url",
    }
    assert set(usage) == {"records", "summary"}

    state.service.close_members()


if __name__ == "__main__":
    test_application_package_imports_without_cli_or_web()
    test_application_modules_do_not_import_cli_or_web()
    test_application_service_modules_are_direct_package_exports()
    test_removed_application_services_facade_is_absent()
    test_application_package_exports_stable_service_api()
    test_removed_core_migration_paths_are_absent()
    test_cli_and_web_use_application_layer_for_construction()
    test_entrypoints_do_not_import_runtime_types_directly()
    test_cli_and_web_use_observability_not_core_group_views()
    test_group_cli_audit_uses_application_and_observability()
    test_core_runtime_does_not_import_observability()
    test_runtime_and_application_do_not_import_removed_migration_paths()
    test_app_config_defaults_match_current_runtime_defaults()
    test_app_config_from_mapping_preserves_current_env_names()
    import tempfile
    from pathlib import Path as _Path
    with tempfile.TemporaryDirectory() as tmp:
        test_app_config_from_env_loads_project_env_file(_Path(tmp))
    test_application_builder_interfaces_are_declared_and_wired()
    with tempfile.TemporaryDirectory() as tmp:
        test_application_builder_consumes_runtime_config(_Path(tmp))
    with tempfile.TemporaryDirectory() as tmp:
        test_application_builder_injects_workspace_roots_and_usage_toggle(_Path(tmp))
    test_tool_registry_does_not_own_workspace_context()
    test_application_builder_constructs_group_members_and_policy()
    with tempfile.TemporaryDirectory() as tmp:
        test_application_services_wrap_current_entrypoint_construction(_Path(tmp))
    with tempfile.TemporaryDirectory() as tmp:
        test_group_service_options_load_and_merge_members(_Path(tmp))
    with tempfile.TemporaryDirectory() as tmp:
        test_group_app_service_from_options_preserves_runtime_shape(_Path(tmp))
    with tempfile.TemporaryDirectory() as tmp:
        test_group_app_service_dynamic_member_persistence_modes(_Path(tmp))
    test_group_app_service_clear_reset_and_usage_semantics()
    test_group_app_service_payload_views_keep_current_shapes()
    test_agent_app_service_session_and_tools_queries()
    test_application_services_expose_tool_audit_queries()
    test_prompting_package_imports_without_cli_or_web()
    test_common_prompt_specs_do_not_include_group_only_modules()
    test_group_prompt_specs_cover_declared_group_tools()
    with tempfile.TemporaryDirectory() as tmp:
        test_system_builder_loads_common_prompt_specs(_Path(tmp))
    with tempfile.TemporaryDirectory() as tmp:
        test_prompt_module_store_matches_system_builder_static_behavior(_Path(tmp))
    with tempfile.TemporaryDirectory() as tmp:
        test_prompt_runtime_build_keeps_dynamic_module_order(_Path(tmp))
    with tempfile.TemporaryDirectory() as tmp:
        test_system_builder_public_facade_delegates_to_prompt_runtime(_Path(tmp))
    with tempfile.TemporaryDirectory() as tmp:
        test_prompt_loader_resolves_specs_relative_to_prompts_dir(_Path(tmp))
    with tempfile.TemporaryDirectory() as tmp:
        test_group_prompt_renderer_keeps_current_identity_shape(_Path(tmp))
    test_prompt_renderer_does_not_import_core_runtime()
    test_system_builder_delegates_skill_and_tool_prompt_details()
    test_prompt_runtime_exposes_facade_facing_api()
    test_prompt_tool_renderer_keeps_current_shape()
    with tempfile.TemporaryDirectory() as tmp:
        test_prompt_skill_loader_and_renderer_keep_current_shape(_Path(tmp))
    test_dispatch_policy_prompt_hints_cover_registered_policies()
    test_workspace_tool_specs_cover_current_default_workspace_tools()
    test_workspace_tool_architecture_boundaries_are_wired()
    test_workspace_tool_architecture_modules_do_not_import_cli_or_web()
    test_tool_permission_and_audit_modules_do_not_import_runtime_or_entrypoints()
    test_observability_package_imports_without_cli_or_web()
    test_observability_views_wrap_current_group_objects_without_mutation()
    with tempfile.TemporaryDirectory() as tmp:
        test_observability_usage_and_log_views_are_plain_dicts(_Path(tmp))
    test_observability_usage_formatting_helpers_keep_shapes()
    test_observability_tool_audit_view_is_plain_dict()
    test_roadmap_records_current_implementation_status()
    test_readme_documents_runtime_operations()
    with tempfile.TemporaryDirectory() as tmp:
        test_web_state_payload_shape_stays_stable(_Path(tmp))
