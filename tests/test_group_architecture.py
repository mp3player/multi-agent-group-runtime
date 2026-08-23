import json
import sys
from pathlib import Path
import inspect
import ast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.group_tool_specs as group_tool_specs
import core.group_tools as group_tools
import core.group_tools_runtime as group_tools_runtime
import core.group_turns as group_turns
import core
import core.group_policy.policies as group_policy_policies
import core.group_policy.registry as group_policy_registry
import core.group_policy.rules as group_policy_rules
import domain
from core.group_runtime import (
    GroupContextBuilder,
    GroupContextPort,
    GroupDispatchLoop,
    GroupDispatchPort,
    GroupDispatchRuntimePort,
    GroupDispatchRuntimeAdapter,
    GroupMemberPort,
    GroupMemberLifecycle,
    GroupMemberStore,
    GroupMemoryRuntime,
    GroupMessageRuntime,
    GroupMessagePort,
    GroupMessageStore,
    GroupRunState,
    GroupRuntimePort,
    GroupSyntheticPassRuntimeAdapter,
    GroupSyntheticPassRuntimePort,
    GroupSyntheticPassService,
    GroupToolRuntimeAdapter,
    GroupToolRuntimePort,
    GroupTurnRuntimeAdapter,
    GroupTurnRuntimePort,
)
from domain.group_memory import GROUP_MEMORY_SECTIONS, GroupMemory
from core.group_policy import (
    BroadcastFeedbackGroupDispatchPolicy,
    DefaultGroupDispatchPolicy,
    OnDemandGroupDispatchPolicy,
    create_dispatch_policy,
    directed_auto_pass_messages,
    dispatchable_unread_messages,
    next_member,
    registered_dispatch_policies,
    register_dispatch_policy,
    unregister_dispatch_policy,
    unread_messages,
)
from core.group_tools import GROUP_TOOL_EFFECTS
from domain.group_events import GroupEventLog, group_event_sink_from_path
from domain.group import AgentLike, GroupMember, GroupMessage, GroupTurn
from domain.group_stats import GroupStats
from core.group import GroupChat
from observability.views import group_member_view, group_message_view
from tools.permissions import ToolPermission
from tools.registry import ToolRegistry


class DummyAgent:
    def __init__(self) -> None:
        self.registry = ToolRegistry()
        self.system_builder = None
        self.llm = type("DummyLLM", (), {
            "model": "dummy-model",
            "base_url": "http://dummy.local",
        })()

    def rebuild_system_prompt(self) -> None:
        return None

    def reset_active_to_system(self) -> None:
        return None


def test_group_runtime_component_imports() -> None:
    message_store = GroupMessageStore()
    member_store = GroupMemberStore()
    member_lifecycle = GroupMemberLifecycle()
    context_builder = GroupContextBuilder()
    dispatch_loop = GroupDispatchLoop()
    run_state = GroupRunState()

    assert message_store.transcript_limit == 40
    assert message_store.message_store_limit == 1000
    assert member_store.allow_duplicate_agents is False
    assert member_lifecycle.validate_available(
        object(),
        GroupMember("A", DummyAgent()),
    ) is None
    assert context_builder.recent_context_limit == 8
    assert dispatch_loop.max_dispatch_rounds == 100
    assert run_state.run_timeout is None
    assert GroupMessagePort.__name__ == "GroupMessagePort"
    assert GroupMemberPort.__name__ == "GroupMemberPort"
    assert GroupContextPort.__name__ == "GroupContextPort"
    assert GroupDispatchPort.__name__ == "GroupDispatchPort"
    assert GroupDispatchRuntimePort.__name__ == "GroupDispatchRuntimePort"
    assert GroupTurnRuntimePort.__name__ == "GroupTurnRuntimePort"
    assert GroupSyntheticPassRuntimePort.__name__ == "GroupSyntheticPassRuntimePort"
    assert GroupToolRuntimePort.__name__ == "GroupToolRuntimePort"
    assert GroupRuntimePort.__name__ == "GroupRuntimePort"


def test_domain_layer_does_not_import_core() -> None:
    for path in Path("domain").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("core"), path
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("core"), path


def test_core_runtime_uses_domain_imports_not_removed_migration_paths() -> None:
    forbidden = (
        "core.group_state",
        "core.group_memory",
        "core.group_events",
        "core.group_stats",
    )
    for path in Path("core").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "") not in forbidden, path
            elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name not in forbidden, path


def test_dispatch_policy_does_not_depend_on_removed_scheduler_module() -> None:
    source = inspect.getsource(sys.modules["core.group_policy"])

    assert "group_scheduler" not in source


def test_removed_core_migration_modules_do_not_exist() -> None:
    removed = {
        "core/factory.py",
        "core/group_events.py",
        "core/group_memory.py",
        "core/group_scheduler.py",
        "core/group_state.py",
        "core/group_stats.py",
        "core/group_tool_handlers.py",
        "core/group_views.py",
    }
    for path in removed:
        assert not Path(path).exists(), path


def test_runtime_does_not_depend_on_removed_migration_modules() -> None:
    removed_modules = {
        "core.factory",
        "core.group_events",
        "core.group_memory",
        "core.group_scheduler",
        "core.group_state",
        "core.group_stats",
        "core.group_tool_handlers",
        "core.group_views",
    }
    for path in Path("core").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "") not in removed_modules, path
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in removed_modules, path


def test_cli_and_web_do_not_import_each_other() -> None:
    pairs = {
        Path("main.py"): "web_server",
        Path("web_server.py"): "main",
    }
    for path, forbidden in pairs.items():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "") != forbidden, path
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != forbidden, path


def test_domain_group_exports() -> None:
    assert domain.AgentLike is AgentLike
    assert domain.GroupMember is GroupMember
    assert domain.GroupMessage is GroupMessage
    assert domain.GroupTurn is GroupTurn
    assert domain.GroupMemory is GroupMemory
    assert domain.GroupEventLog is GroupEventLog
    assert domain.GroupStats is GroupStats


def test_group_message_store_add_transcript_prune_and_clear() -> None:
    store = GroupMessageStore(transcript_limit=2, message_store_limit=3)

    first = store.add("A", "first", round_index=1)
    second = store.add("B", "second", round_index=2)
    third = store.add("C", "third", round_index=3)
    fourth = store.add("D", "fourth", round_index=4)

    assert [first.id, second.id, third.id, fourth.id] == [1, 2, 3, 4]
    assert [msg.id for msg in store.messages] == [2, 3, 4]
    assert "first" not in store.transcript(limit=0)
    assert "second" in store.transcript(limit=0)
    assert store.transcript() == "\n".join(msg.render() for msg in store.messages[-2:])

    store.clear()

    assert store.messages == []
    assert store.next_message_id == 1
    assert store.add("A", "after clear").id == 1


def test_group_member_store_add_get_enabled_remove_and_validate() -> None:
    store = GroupMemberStore()
    first = GroupMember(name="A", agent=DummyAgent(), description="first")
    duplicate_agent = GroupMember(name="B", agent=first.agent, description="dup")
    disabled = GroupMember(
        name="C",
        agent=DummyAgent(),
        description="disabled",
        enabled=False,
    )

    assert store.validate_new_member(first) is None
    assert store.add(first) is first
    assert store.get("A") is first
    assert store.validate_new_member(GroupMember(name="A", agent=DummyAgent())) == (
        "成员已存在: A"
    )
    assert store.validate_new_member(duplicate_agent) == (
        "同一个 Agent 不能作为多个成员重复加入: B"
    )
    store.add(disabled)
    assert store.enabled() == [first]
    assert store.remove("missing") is None
    assert store.remove("A") is first


def test_group_run_state_guards_and_timeout() -> None:
    run_state = GroupRunState(run_timeout=0.001)

    run_state.enter(RuntimeError)
    assert run_state.active is True
    try:
        run_state.ensure_not_running("添加成员", RuntimeError)
    except RuntimeError as error:
        assert str(error) == "群组正在运行，不能添加成员"
    else:
        raise AssertionError("expected running guard to raise")
    run_state.exit()
    assert run_state.active is False

    deadline = run_state.deadline()
    assert deadline is not None
    run_state.check_deadline(deadline + 10, RuntimeError)
    try:
        run_state.check_deadline(0, RuntimeError)
    except RuntimeError as error:
        assert str(error) == "Group run exceeded timeout 0.001s"
    else:
        raise AssertionError("expected deadline guard to raise")


def test_group_context_builder_prompt_and_recent_context() -> None:
    builder = GroupContextBuilder(recent_context_limit=1)
    first = GroupMessage(id=1, sender="A", content="first", propagate=True)
    ignored = GroupMessage(id=2, sender="B", content="ignored", propagate=False)
    unread = [GroupMessage(id=3, sender="C", content="third", propagate=True)]

    assert builder.recent_context_messages([first, ignored, *unread], unread) == [first]
    prompt = builder.build_member_prompt([first, ignored, *unread], unread)
    assert "Recent group context" in prompt
    assert "#1" in prompt
    assert "#2" not in prompt
    assert "Unread group messages:" in prompt
    assert "#3" in prompt

    no_context = GroupContextBuilder(recent_context_limit=0)
    assert no_context.recent_context_messages([first], []) == []
    assert no_context.build_member_prompt([first], []) == (
        "Unread group messages:\n(没有新的可传播消息)"
    )


def test_group_chat_uses_runtime_components_behind_existing_facade() -> None:
    group = GroupChat(name="RuntimeGroup")

    assert type(group) is GroupChat
    assert isinstance(group.message_store, GroupMessageStore)
    assert isinstance(group.member_store, GroupMemberStore)
    assert isinstance(group.member_lifecycle, GroupMemberLifecycle)
    assert isinstance(group.message_runtime, GroupMessageRuntime)
    assert isinstance(group.memory_runtime, GroupMemoryRuntime)
    assert isinstance(group.context_builder, GroupContextBuilder)
    assert isinstance(group.dispatch_loop, GroupDispatchLoop)
    assert isinstance(group.dispatch_runtime, GroupDispatchRuntimeAdapter)
    assert isinstance(group.turn_runtime, GroupTurnRuntimeAdapter)
    assert isinstance(group.synthetic_pass_service, GroupSyntheticPassService)
    assert isinstance(group.synthetic_pass_runtime, GroupSyntheticPassRuntimeAdapter)
    assert not hasattr(group, "tool_runtime")
    assert isinstance(group.run_state, GroupRunState)


def test_group_chat_delegates_member_lifecycle_side_effects() -> None:
    source = inspect.getsource(GroupChat)

    assert "attach_group_tools" not in source
    assert "detach_group_tools" not in source
    assert "_sync_member_system" not in source
    assert "_unsync_member_system" not in source


def test_group_chat_delegates_member_close_to_lifecycle() -> None:
    source = inspect.getsource(GroupChat.aclose_members)

    assert "member_lifecycle.close_members" in source


def test_dispatch_loop_uses_runtime_port_boundary() -> None:
    group = GroupChat(name="DispatchPortGroup")
    required_methods = [
        "dispatch_enter_run",
        "dispatch_exit_run",
        "dispatch_deadline",
        "dispatch_check_deadline",
        "dispatch_select_speakers",
        "dispatch_synthetic_pass_unread",
        "dispatch_next_member",
        "dispatchable_unread_for",
        "dispatch_run_member_turn",
        "dispatch_arun_member_turn",
        "dispatch_record_member_error",
        "dispatch_record_turn_stats",
        "dispatch_record_user_steps",
        "dispatch_record_limit",
        "dispatch_event",
        "dispatch_wake",
    ]

    assert all(
        callable(getattr(group.dispatch_runtime, name))
        for name in required_methods
    )
    assert "group._" not in inspect.getsource(GroupDispatchLoop)


def test_group_chat_delegates_dispatch_recording_to_runtime_adapter() -> None:
    source = inspect.getsource(GroupChat)
    adapter_source = inspect.getsource(GroupDispatchRuntimeAdapter)

    assert "_record_member_error" not in source
    assert "_record_dispatch_limit" not in source
    assert "dispatch_error" in adapter_source
    assert "dispatch_limit" in adapter_source


def test_group_chat_delegates_member_resolution_to_store() -> None:
    source = inspect.getsource(GroupChat._resolve_member)
    select_source = inspect.getsource(GroupChat._select_speakers)
    mention_source = inspect.getsource(GroupChat._validate_mentions)

    assert "member_store.resolve" in source
    assert "member_store.select" in select_source
    assert "member_store.validate_mentions" in mention_source


def test_group_chat_delegates_message_and_memory_side_effects() -> None:
    add_source = inspect.getsource(GroupChat.add_message)
    update_source = inspect.getsource(GroupChat.update_memory)
    clear_source = inspect.getsource(GroupChat.clear_memory)
    group_source = inspect.getsource(GroupChat)

    assert "message_runtime.add_message" in add_source
    assert "memory_runtime.update" in update_source
    assert "memory_runtime.clear" in clear_source
    assert "_record_message_stats" not in group_source


def test_group_turns_use_runtime_port_boundary() -> None:
    group = GroupChat(name="TurnPortGroup")
    required_methods = [
        "turn_was_directed_to_member",
        "turn_next_message_id",
        "turn_begin",
        "turn_end",
        "turn_build_prompt",
        "turn_dispatchable_unread",
        "turn_add_message",
        "turn_record_stale_response",
    ]

    assert all(
        callable(getattr(group.turn_runtime, name))
        for name in required_methods
    )
    assert "group._" not in inspect.getsource(group_turns)


def test_synthetic_pass_uses_runtime_service() -> None:
    group = GroupChat(name="SyntheticPassPortGroup")
    required_methods = [
        "synthetic_pass_messages",
        "synthetic_pass_run_member_turn",
        "synthetic_pass_record",
    ]

    assert all(
        callable(getattr(group.synthetic_pass_runtime, name))
        for name in required_methods
    )
    source = inspect.getsource(GroupDispatchRuntimeAdapter.dispatch_synthetic_pass_unread)
    assert "synthetic_pass_service.pass_unread" in source
    assert "_synthetic_pass_directed_unread" not in inspect.getsource(GroupChat)


def test_group_runtime_adapters_do_not_bounce_through_facade_wrappers() -> None:
    adapter_source = inspect.getsource(GroupDispatchRuntimeAdapter)
    turn_source = inspect.getsource(GroupTurnRuntimeAdapter)
    group_source = inspect.getsource(GroupChat)

    for name in (
        "_next_member",
        "_record_turn_stats",
        "_record_user_dispatch_steps",
        "_synthetic_pass_directed_unread",
        "_run_member_turn_with_unread",
        "_arun_member_turn",
    ):
        assert f"group.{name}" not in adapter_source
        assert name not in group_source
    for name in ("_was_directed_to_member", "_build_member_prompt"):
        assert f"group.{name}" not in turn_source
        assert name not in group_source
    assert "dispatch_policy.next_member" in adapter_source
    assert "stats.record_turn" in adapter_source
    assert "stats.record_user_dispatch_steps" in adapter_source
    assert "context_builder.build_member_prompt" in turn_source


def test_group_tools_use_runtime_port_boundary() -> None:
    group = GroupChat(name="ToolPortGroup")
    adapter = GroupToolRuntimeAdapter(group)
    required_methods = [
        "tool_record_call",
        "tool_member_status_lines",
        "tool_recent_messages",
        "tool_member_messages_after",
        "tool_memory_summary",
        "tool_memory_full",
        "tool_add_message",
        "tool_has_member",
        "tool_member_enabled",
        "tool_update_memory",
        "tool_clear_memory",
        "tool_turn_start_message_id",
    ]

    assert all(callable(getattr(adapter, name)) for name in required_methods)
    assert "group._" not in inspect.getsource(group_tools)


def test_group_tools_are_split_into_specs_handlers_and_binding() -> None:
    assert group_tools.GROUP_TOOL_EFFECTS is group_tool_specs.GROUP_TOOL_EFFECTS
    source = inspect.getsource(group_tools)

    assert "class GroupToolHandlers" not in source
    assert "def group_direct" not in source
    assert group_tools_runtime.GroupToolHandlers.__name__ == "GroupToolHandlers"
    assert "GROUP_TOOL_ORDER" in source


def test_core_exports_group_chat_unchanged() -> None:
    assert core.GroupChat is GroupChat


def test_group_memory_sections_update_append_clear_and_summary() -> None:
    memory = GroupMemory()

    assert memory.sections == GROUP_MEMORY_SECTIONS
    assert memory.render_summary() == "(empty)"
    assert memory.update("goal", "Ship architecture") == "(已更新 memory.goal)"
    assert memory.update("done", "first", append=True) == "(已更新 memory.done)"
    assert memory.update("done", "second", append=True) == "(已更新 memory.done)"
    assert memory.get("done") == "first\nsecond"
    assert "- goal: Ship architecture" in memory.render_summary()
    assert memory.clear("done") == "(已清空 memory.done)"
    assert memory.get("done") == ""
    assert memory.clear() == "(已清空全部 group memory)"
    assert memory.render_summary() == "(empty)"
    assert memory.to_dict() == {section: "" for section in GROUP_MEMORY_SECTIONS}


def test_group_event_log_recent_and_dict_views() -> None:
    events = GroupEventLog()

    events.append("tool_call", actor="A", data={"tool": "group_status"})
    events.append("memory_update", actor="B", data={"section": "done"})

    assert [event.kind for event in events.recent(1)] == ["memory_update"]
    assert events.to_dicts() == [
        {
            "id": 1,
            "kind": "tool_call",
            "actor": "A",
            "data": {"tool": "group_status"},
        },
        {
            "id": 2,
            "kind": "memory_update",
            "actor": "B",
            "data": {"section": "done"},
        },
    ]


def test_group_event_log_jsonl_sink_appends_and_clear_keeps_file(tmp_path: Path) -> None:
    sink_path = tmp_path / "group-events.jsonl"
    sink_path.unlink(missing_ok=True)
    disabled = GroupEventLog()

    disabled.append("tool_call", actor="A", data={"tool": "group_status"})
    assert not sink_path.exists()

    events = GroupEventLog(group_event_sink_from_path(sink_path))
    first = events.append("message", actor="user", data={"message_id": 1})
    second = events.append("dispatch_limit", actor="system", data={"limit": 20})

    rows = [
        json.loads(line)
        for line in sink_path.read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [first.to_dict(), second.to_dict()]

    events.clear()
    assert events.recent() == []
    assert sink_path.exists()
    assert len(sink_path.read_text(encoding="utf-8").splitlines()) == 2

    events.append("memory_update", actor="A", data={"section": "done"})
    rows = [
        json.loads(line)
        for line in sink_path.read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1] == {
        "id": 1,
        "kind": "memory_update",
        "actor": "A",
        "data": {"section": "done"},
    }


def test_group_message_event_records_mentions() -> None:
    group = GroupChat(name="EventMentionsGroup")
    group.add_member(GroupMember(
        name="A",
        agent=DummyAgent(),
        description="sender",
    ))
    group.add_member(GroupMember(
        name="B",
        agent=DummyAgent(),
        description="receiver",
    ))

    group.add_message("A", "Directed to: B\nplease inspect", mentions=["B"])

    message_events = [
        event for event in group.events.recent()
        if event.kind == "message"
    ]
    assert message_events[-1].data["mentions"] == ["B"]


def test_group_view_serialization_shapes_are_stable() -> None:
    group = GroupChat(name="ViewGroup")
    member = group.add_member(GroupMember(
        name="A",
        agent=DummyAgent(),
        description="reviewer",
    ))
    msg = GroupMessage(
        id=7,
        sender="A",
        content="hello",
        round_index=2,
        mentions=["B"],
        propagate=True,
    )

    assert group_message_view(msg) == {
        "id": 7,
        "sender": "A",
        "content": "hello",
        "kind": "agent",
        "round_index": 2,
        "mentions": ["B"],
        "propagate": True,
        "dispatch_mode": "normal",
    }
    assert group_member_view(group, member) == {
        "name": "A",
        "description": "reviewer",
        "enabled": True,
        "status": "idle",
        "unread": 0,
        "dispatchable": 0,
        "tools": member.agent.registry.names(),
        "model": "dummy-model",
        "url": "http://dummy.local",
    }


def test_group_tool_side_effect_classification_is_complete() -> None:
    assert GROUP_TOOL_EFFECTS == {
        "group_status": "read_only",
        "group_memory_get": "read_only",
        "group_memory_update": "memory_only",
        "group_memory_append": "memory_only",
        "group_memory_clear": "memory_only",
        "group_send": "propagating",
        "group_broadcast": "propagating",
        "group_direct": "propagating",
        "group_handoff": "propagating",
        "group_note_scope": "propagating",
        "group_done": "propagating",
        "group_report": "propagating",
        "group_decision": "propagating",
        "group_pass": "non_propagating",
    }
    assert group_tool_specs.group_tool_names() == set(GROUP_TOOL_EFFECTS)
    assert group_tool_specs.group_tool_effects() == GROUP_TOOL_EFFECTS
    assert group_tool_specs.group_tools_by_effect("read_only") == (
        "group_status",
        "group_memory_get",
    )
    assert group_tool_specs.group_tools_by_effect("non_propagating") == ("group_pass",)


def test_group_tool_binding_records_registry_metadata() -> None:
    group = GroupChat(name="ToolMetadataGroup")
    member = group.add_member(GroupMember(
        name="A",
        agent=DummyAgent(),
        description="member",
    ))

    metadata = member.agent.registry.tool_metadata_map()

    for name, effect in GROUP_TOOL_EFFECTS.items():
        assert metadata[name] == effect
        assert isinstance(metadata[name], ToolPermission)


def test_group_direct_records_single_tool_call_event() -> None:
    group = GroupChat(name="ToolEventGroup")
    sender = group.add_member(GroupMember(
        name="A",
        agent=DummyAgent(),
        description="sender",
    ))
    group.add_member(GroupMember(
        name="B",
        agent=DummyAgent(),
        description="receiver",
    ))

    result = sender.agent.registry.call(
        "group_direct",
        to=["B"],
        message="please inspect this",
    )

    assert "已定向发送给 B" in result
    tool_events = [
        event for event in group.events.recent()
        if event.kind == "tool_call"
    ]
    assert [event.data["tool"] for event in tool_events] == ["group_direct"]
    assert group.messages[-1].mentions == ["B"]
    assert group.messages[-1].content.startswith("Directed to: B\n")


def test_dispatch_policy_rules_match_policy_methods() -> None:
    group = GroupChat(name="PolicyGroup")
    first = group.add_member(GroupMember(
        name="A",
        agent=DummyAgent(),
        description="first",
    ))
    second = group.add_member(GroupMember(
        name="B",
        agent=DummyAgent(),
        description="second",
    ))
    group.add_user_message("review the project")
    candidates = [first, second]
    policy = DefaultGroupDispatchPolicy()

    assert policy.unread_messages(group, first) == unread_messages(
        group,
        first,
    )
    assert policy.dispatchable_messages(
        group,
        first,
        candidates,
    ) == dispatchable_unread_messages(group, first, candidates)
    assert policy.synthetic_pass_messages(
        group,
        first,
    ) == directed_auto_pass_messages(group, first)
    assert policy.next_member(group, candidates) is group_policy_rules.next_member(
        group,
        candidates,
    )
    assert group_policy_rules.unread_messages(group, first) == unread_messages(group, first)
    assert group_policy_rules.dispatchable_unread_messages(
        group,
        first,
        candidates,
    ) == dispatchable_unread_messages(group, first, candidates)


def test_group_policy_package_exports_stable_api() -> None:
    assert group_policy_rules.next_member is next_member
    assert group_policy_rules.dispatchable_unread_messages is dispatchable_unread_messages
    assert group_policy_rules.directed_auto_pass_messages is directed_auto_pass_messages
    assert group_policy_policies.DefaultGroupDispatchPolicy is DefaultGroupDispatchPolicy
    assert (
        group_policy_policies.BroadcastFeedbackGroupDispatchPolicy
        is BroadcastFeedbackGroupDispatchPolicy
    )
    assert group_policy_policies.OnDemandGroupDispatchPolicy is OnDemandGroupDispatchPolicy
    assert group_policy_registry.create_dispatch_policy is create_dispatch_policy
    assert group_policy_registry.registered_dispatch_policies is registered_dispatch_policies
    assert group_policy_registry.register_dispatch_policy is register_dispatch_policy
    assert group_policy_registry.unregister_dispatch_policy is unregister_dispatch_policy


def test_dispatch_policy_registry_creates_custom_policy() -> None:
    class ReversePolicy(DefaultGroupDispatchPolicy):
        name = "reverse-test"

        def next_member(self, group, candidates):  # type: ignore[no-untyped-def]
            idle = [m for m in candidates if m.enabled and m.status == "idle"]
            return idle[-1] if idle else None

    try:
        register_dispatch_policy(ReversePolicy.name, ReversePolicy)

        group = GroupChat(name="CustomPolicyGroup")
        first = group.add_member(GroupMember(
            name="A",
            agent=DummyAgent(),
            description="first",
        ))
        second = group.add_member(GroupMember(
            name="B",
            agent=DummyAgent(),
            description="second",
        ))
        policy = create_dispatch_policy("reverse-test")

        assert "default" in registered_dispatch_policies()
        assert "reverse-test" in registered_dispatch_policies()
        assert policy.name == "reverse-test"
        assert policy.next_member(group, [first, second]) is second
    finally:
        unregister_dispatch_policy(ReversePolicy.name)
    assert "reverse-test" not in registered_dispatch_policies()


def test_dispatch_policy_registry_keeps_default_policy() -> None:
    try:
        unregister_dispatch_policy("default")
    except ValueError as error:
        assert str(error) == "cannot unregister default dispatch policy"
    else:
        raise AssertionError("unregistering default policy should fail")
    assert "default" in registered_dispatch_policies()


def test_dispatch_policy_registry_includes_on_demand_policy() -> None:
    policy = create_dispatch_policy("on_demand")

    assert "on_demand" in registered_dispatch_policies()
    assert isinstance(policy, OnDemandGroupDispatchPolicy)


def test_dispatch_policy_registry_includes_broadcast_feedback_policy() -> None:
    policy = create_dispatch_policy("broadcast_feedback")

    assert "broadcast_feedback" in registered_dispatch_policies()
    assert isinstance(policy, BroadcastFeedbackGroupDispatchPolicy)


def test_group_chat_delegates_scheduling_to_injected_policy() -> None:
    class FixedPolicy(DefaultGroupDispatchPolicy):
        name = "fixed-test"

        def next_member(self, group, candidates):  # type: ignore[no-untyped-def]
            return candidates[-1]

    group = GroupChat(name="InjectedPolicyGroup", dispatch_policy=FixedPolicy())
    first = group.add_member(GroupMember(
        name="A",
        agent=DummyAgent(),
        description="first",
    ))
    second = group.add_member(GroupMember(
        name="B",
        agent=DummyAgent(),
        description="second",
    ))

    assert group.dispatch_policy.name == "fixed-test"
    assert group.dispatch_runtime.dispatch_next_member([first, second]) is second


if __name__ == "__main__":
    test_group_runtime_component_imports()
    test_domain_layer_does_not_import_core()
    test_core_runtime_uses_domain_imports_not_removed_migration_paths()
    test_dispatch_policy_does_not_depend_on_removed_scheduler_module()
    test_removed_core_migration_modules_do_not_exist()
    test_runtime_does_not_depend_on_removed_migration_modules()
    test_cli_and_web_do_not_import_each_other()
    test_domain_group_exports()
    test_group_message_store_add_transcript_prune_and_clear()
    test_group_member_store_add_get_enabled_remove_and_validate()
    test_group_run_state_guards_and_timeout()
    test_group_context_builder_prompt_and_recent_context()
    test_group_chat_uses_runtime_components_behind_existing_facade()
    test_group_chat_delegates_member_lifecycle_side_effects()
    test_group_chat_delegates_member_close_to_lifecycle()
    test_dispatch_loop_uses_runtime_port_boundary()
    test_group_chat_delegates_dispatch_recording_to_runtime_adapter()
    test_group_chat_delegates_member_resolution_to_store()
    test_group_chat_delegates_message_and_memory_side_effects()
    test_group_turns_use_runtime_port_boundary()
    test_synthetic_pass_uses_runtime_service()
    test_group_runtime_adapters_do_not_bounce_through_facade_wrappers()
    test_group_tools_use_runtime_port_boundary()
    test_group_tools_are_split_into_specs_handlers_and_binding()
    test_core_exports_group_chat_unchanged()
    test_group_memory_sections_update_append_clear_and_summary()
    test_group_event_log_recent_and_dict_views()
    test_group_event_log_jsonl_sink_appends_and_clear_keeps_file(Path("/tmp"))
    test_group_message_event_records_mentions()
    test_group_view_serialization_shapes_are_stable()
    test_group_tool_side_effect_classification_is_complete()
    test_group_tool_binding_records_registry_metadata()
    test_group_direct_records_single_tool_call_event()
    test_dispatch_policy_rules_match_policy_methods()
    test_group_policy_package_exports_stable_api()
    test_dispatch_policy_registry_creates_custom_policy()
    test_dispatch_policy_registry_keeps_default_policy()
    test_dispatch_policy_registry_includes_on_demand_policy()
    test_dispatch_policy_registry_includes_broadcast_feedback_policy()
    test_group_chat_delegates_scheduling_to_injected_policy()
