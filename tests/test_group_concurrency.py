
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.group import GroupChat
from core.group_policy import (
    BroadcastFeedbackGroupDispatchPolicy,
    DefaultGroupDispatchPolicy,
    OnDemandGroupDispatchPolicy,
)
from domain.group import GroupMember
from core.llm import LLMClient
from core.session import Session
from models import AI, ToolCall
from tools.registry import ToolRegistry


class MockAgent:
    """Minimal async agent stub for deterministic group scheduling tests."""

    def __init__(self, name: str, response: str = "PASS", delay: float = 0.0):
        self.name = name
        self.registry = ToolRegistry()
        self.system_builder = None
        self.response = response
        self.delay = delay
        self.prompts: list[str] = []
        self.response_count = 0

    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        self.response_count += 1
        return self.response

    def run(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.response_count += 1
        return self.response

    def rebuild_system_prompt(self) -> None:
        return None

    def reset_active_to_system(self) -> None:
        return None


class SessionAgent(MockAgent):
    def __init__(self, name: str, response: str = "PASS", delay: float = 0.0):
        super().__init__(name=name, response=response, delay=delay)
        self.session = Session()

    async def arun(self, prompt: str) -> str:
        self.session.add(AI(message=f"called {self.name}"), to_active=False)
        return await super().arun(prompt)

    def run(self, prompt: str) -> str:
        self.session.add(AI(message=f"called {self.name}"), to_active=False)
        return super().run(prompt)


class FailingAgent(MockAgent):
    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.response_count += 1
        raise RuntimeError("simulated failure")

    def run(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.response_count += 1
        raise RuntimeError("simulated failure")


class GroupSendThenFinalAgent(MockAgent):
    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.response_count += 1
        self.registry.call("group_send", message="I handled the greeting.")
        return "I have responded to the user in the group chat."


class GroupBroadcastAgent(MockAgent):
    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.response_count += 1
        self.registry.call(
            "group_broadcast",
            message="Everyone should review this from their own role.",
        )
        return ""


class GroupScopeAgent(MockAgent):
    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.response_count += 1
        self.registry.call(
            "group_note_scope",
            scope=f"{self.name} scope",
            files="core/",
        )
        return ""


class GroupReportAgent(MockAgent):
    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.response_count += 1
        self.registry.call(
            "group_report",
            summary=f"{self.name} report",
            findings=f"{self.name} findings",
        )
        return ""


class GroupSendThenPassAgent(MockAgent):
    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.response_count += 1
        self.registry.call("group_send", message="I have a substantive point.")
        self.registry.call("group_pass")
        return ""


class GroupSendThenWorkAgent(MockAgent):
    def __init__(self, name: str):
        super().__init__(name=name)
        self.session = Session()

    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.response_count += 1
        self.registry.call("group_send", message="I will inspect core/group.py.")
        self.session.add(
            AI(tool_calls=[ToolCall("call-1", "read_file", {"path": "core/group.py"})])
        )
        return "Done: inspected core/group.py. Next: review dispatch tests."


class GroupSendWorkDelayedFinalAgent(MockAgent):
    def __init__(self, name: str, delay: float):
        super().__init__(name=name)
        self.delay = delay
        self.session = Session()

    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.response_count:
            self.response_count += 1
            return "PASS"
        self.response_count += 1
        self.registry.call("group_send", message="I started the review.")
        if self.delay:
            await asyncio.sleep(self.delay)
        self.session.add(
            AI(tool_calls=[ToolCall("call-1", "read_file", {"path": "README.md"})])
        )
        return "I assigned the review work."


class SequenceAgent(MockAgent):
    def __init__(self, name: str, responses: list[str], delays: list[float] | None = None):
        super().__init__(name=name)
        self.responses = responses
        self.delays = delays or []

    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        index = self.response_count
        delay = self.delays[index] if index < len(self.delays) else 0.0
        if delay:
            await asyncio.sleep(delay)
        self.response_count += 1
        if index < len(self.responses):
            return self.responses[index]
        return self.responses[-1]


class DirectWorkDelayedAgent(MockAgent):
    def __init__(self, name: str, response: str, delay: float):
        super().__init__(name=name, response=response, delay=delay)
        self.session = Session()

    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        self.response_count += 1
        self.session.add(
            AI(tool_calls=[ToolCall("call-1", "read_file", {"path": "README.md"})])
        )
        return self.response


async def test_adispatch_concurrency_state_is_consistent() -> None:
    group = GroupChat(name="TestGroup", max_dispatch_rounds=20)

    for name in ["Dev-A", "Dev-B", "Dev-C", "Dev-D", "Dev-E"]:
        agent = MockAgent(name=name, response="PASS", delay=0.001)
        group.add_member(GroupMember(name=name, agent=agent, description=name))

    turns = await group.arun("Please analyze the project architecture.", max_rounds=20)

    assert turns
    assert [msg.id for msg in group.messages] == list(range(1, len(group.messages) + 1))
    assert all(member.status == "idle" for member in group.members.values())
    assert all(
        member.last_read_message_id <= group.messages[-1].id
        for member in group.members.values()
    )


async def test_injected_dispatch_policy_controls_actual_member_turn() -> None:
    class SecondMemberPolicy(DefaultGroupDispatchPolicy):
        name = "second-member-test"

        def next_member(self, group, candidates):  # type: ignore[no-untyped-def]
            idle_with_unread = [
                member for member in candidates
                if member.enabled
                and member.status == "idle"
                and self.unread_messages(group, member)
            ]
            return idle_with_unread[-1] if idle_with_unread else None

        def dispatchable_messages(self, group, member, candidates):  # type: ignore[no-untyped-def]
            return self.unread_messages(group, member)

    group = GroupChat(
        name="InjectedRuntimePolicyGroup",
        dispatch_policy=SecondMemberPolicy(),
        max_dispatch_rounds=1,
    )
    first = MockAgent(name="A", response="A handled")
    second = MockAgent(name="B", response="B handled")

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))

    await group.arun("route this once", max_rounds=1)

    assert first.response_count == 0
    assert second.response_count == 1
    assert any(
        msg.sender == "B" and msg.content == "B handled"
        for msg in group.messages
    )


async def test_injected_policy_controls_directed_empty_response_prompt() -> None:
    class ContentDirectedPolicy(DefaultGroupDispatchPolicy):
        name = "content-directed-test"

        def is_directed_to_member(self, group, message, member):  # type: ignore[no-untyped-def]
            return f"target={member.name}" in message.content

    group = GroupChat(
        name="DirectedPromptPolicyGroup",
        dispatch_policy=ContentDirectedPolicy(),
        max_dispatch_rounds=1,
    )
    first = MockAgent(name="A", response="")

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_message("system", "target=A handle this", kind="system")

    await group.adispatch(max_rounds=1)

    assert group.messages[-1].sender == "A"
    assert group.messages[-1].content == "[空回复：收到定向请求但未给出有效响应]"
    assert not group.messages[-1].propagate


async def test_on_demand_policy_keeps_user_first_responder_entry() -> None:
    group = GroupChat(
        name="OnDemandEntryGroup",
        dispatch_policy=OnDemandGroupDispatchPolicy(),
        max_dispatch_rounds=5,
    )
    first = SessionAgent(name="A", response="A accepted the user request")
    second = SessionAgent(name="B", response="B should not run")

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))

    await group.arun("please review the project", max_rounds=5)

    assert first.response_count == 1
    assert second.response_count == 0
    assert any(
        msg.sender == "A" and msg.content == "A accepted the user request"
        for msg in group.messages
    )
    assert group.get_member("B").last_read_message_id >= 2
    assert group.stats.synthetic_passes == 1


async def test_on_demand_policy_synthetic_passes_normal_messages() -> None:
    group = GroupChat(
        name="OnDemandBroadcastGroup",
        dispatch_policy=OnDemandGroupDispatchPolicy(),
        max_dispatch_rounds=5,
    )
    first = SessionAgent(name="A", response="PASS")
    second = SessionAgent(name="B", response="B should not run")

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))
    group.add_message("A", "broadcast status update")

    await group.adispatch(max_rounds=5)

    assert first.response_count == 0
    assert second.response_count == 0
    assert group.get_member("B").last_read_message_id >= 1
    pass_messages = [
        msg for msg in group.messages
        if msg.sender == "B" and msg.content == "PASS"
    ]
    assert len(pass_messages) == 1
    assert not pass_messages[0].propagate
    assert group.stats.synthetic_passes == 1


async def test_on_demand_policy_dispatches_explicit_broadcast_messages() -> None:
    group = GroupChat(
        name="OnDemandExplicitBroadcastGroup",
        dispatch_policy=OnDemandGroupDispatchPolicy(),
        max_dispatch_rounds=5,
    )
    first = GroupBroadcastAgent(name="A")
    second = SessionAgent(name="B", response="B reviewed from dev role")
    third = SessionAgent(name="C", response="C reviewed from ops role")

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))
    group.add_member(GroupMember(name="C", agent=third, description="C"))

    await group.arun("please coordinate a review", max_rounds=5)

    assert first.response_count == 1
    assert second.response_count == 1
    assert third.response_count == 1
    broadcast_messages = [
        msg for msg in group.messages
        if msg.sender == "A" and msg.dispatch_mode == "broadcast"
    ]
    assert len(broadcast_messages) == 1
    assert broadcast_messages[0].content.startswith(
        "Explicit broadcast to all members.\n"
    )
    assert "use PASS only when there is genuinely nothing useful to add" in (
        broadcast_messages[0].content
    )


async def test_broadcast_feedback_policy_routes_member_reports_to_originator() -> None:
    group = GroupChat(
        name="BroadcastFeedbackGroup",
        dispatch_policy=BroadcastFeedbackGroupDispatchPolicy(),
        max_dispatch_rounds=8,
    )
    leader = SequenceAgent(
        name="Leader",
        responses=["", "Leader summary received reports"],
    )
    first = GroupReportAgent(name="Dev")
    second = GroupReportAgent(name="Ops")

    async def leader_arun(prompt: str) -> str:
        leader.prompts.append(prompt)
        index = leader.response_count
        leader.response_count += 1
        if index == 0:
            leader.registry.call(
                "group_broadcast",
                message="Everyone review from your role.",
            )
            return ""
        return "Leader summary received reports"

    leader.arun = leader_arun  # type: ignore[method-assign]

    group.add_member(GroupMember(name="Leader", agent=leader, description="lead"))
    group.add_member(GroupMember(name="Dev", agent=first, description="dev"))
    group.add_member(GroupMember(name="Ops", agent=second, description="ops"))

    await group.arun("please coordinate a review", max_rounds=8)

    assert leader.response_count >= 2
    assert first.response_count == 1
    assert second.response_count == 1
    assert any(
        msg.sender == "Leader"
        and msg.content == "Leader summary received reports"
        for msg in group.messages
    )
    feedback_prompts = "\n".join(leader.prompts[1:])
    assert "Dev report" in feedback_prompts
    assert "Ops report" in feedback_prompts
    assert any(
        msg.sender == "Dev" and msg.dispatch_mode == "feedback"
        for msg in group.messages
    )


async def test_broadcast_feedback_policy_does_not_route_scope_to_originator() -> None:
    group = GroupChat(
        name="BroadcastScopeGroup",
        dispatch_policy=BroadcastFeedbackGroupDispatchPolicy(),
        max_dispatch_rounds=5,
    )
    leader = GroupBroadcastAgent(name="Leader")
    scoped = GroupScopeAgent(name="Dev")

    group.add_member(GroupMember(name="Leader", agent=leader, description="lead"))
    group.add_member(GroupMember(name="Dev", agent=scoped, description="dev"))

    await group.arun("please coordinate a review", max_rounds=5)

    assert leader.response_count == 1
    assert scoped.response_count == 1
    assert any(
        msg.sender == "Dev" and msg.dispatch_mode == "normal"
        for msg in group.messages
    )


async def test_on_demand_policy_dispatches_directed_messages_only_to_targets() -> None:
    group = GroupChat(
        name="OnDemandDirectedGroup",
        dispatch_policy=OnDemandGroupDispatchPolicy(),
        max_dispatch_rounds=5,
    )
    first = SessionAgent(name="A", response="A should not run")
    second = SessionAgent(name="B", response="B handled directed work")
    third = SessionAgent(name="C", response="C should not run")

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))
    group.add_member(GroupMember(name="C", agent=third, description="C"))
    group.add_message(
        "A",
        "Directed to: B\nplease handle this",
        mentions=["B"],
    )

    await group.adispatch(max_rounds=5)

    assert first.response_count == 0
    assert second.response_count == 1
    assert third.response_count == 0
    assert any(
        msg.sender == "B" and msg.content == "B handled directed work"
        for msg in group.messages
    )
    assert group.get_member("C").last_read_message_id >= 1
    assert group.stats.synthetic_passes == 3


async def test_failed_member_keeps_unread_while_other_member_can_continue() -> None:
    group = GroupChat(name="FailureGroup", max_dispatch_rounds=5)
    failing = FailingAgent(name="A")
    fallback = MockAgent(name="B", response="handled by B")

    group.add_member(GroupMember(name="A", agent=failing, description="A"))
    group.add_member(GroupMember(name="B", agent=fallback, description="B"))

    turns = await group.arun("do the task", max_rounds=5)

    assert any(turn.member == "A" and turn.message.kind == "system" for turn in turns)
    assert any(msg.sender == "B" and msg.content == "handled by B" for msg in group.messages)
    assert group.get_member("A").last_read_message_id == 0
    assert group.get_member("B").last_read_message_id >= 1


async def test_group_closes_member_llm_clients() -> None:
    group = GroupChat(name="CloseGroup")
    agent = MockAgent(name="A")
    agent.llm = LLMClient(
        base_url="http://127.0.0.1:1/v1",
        api_key="dummy",
        model="dummy",
    )
    group.add_member(GroupMember(name="A", agent=agent, description="A"))

    client = await agent.llm._get_async_client()
    await group.aclose_members()

    assert client.is_closed


async def test_member_prompt_includes_recent_context_without_duplication() -> None:
    group = GroupChat(name="ContextGroup", recent_context_limit=4)
    first = MockAgent(name="A", response="A will inspect scheduling")
    second = MockAgent(name="B", response="PASS")

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))

    await group.arun("review the group scheduler", max_rounds=2)

    assert second.prompts
    prompt = second.prompts[0]
    assert "Recent group context" in prompt
    assert "#1 [1] 用户: review the group scheduler" in prompt
    assert "Unread group messages:" in prompt
    assert "#2 [2] A: A will inspect scheduling" in prompt
    assert prompt.count("#1 [1] 用户: review the group scheduler") == 1


async def test_group_tool_final_response_is_not_duplicated() -> None:
    group = GroupChat(name="ToolFinalGroup", max_dispatch_rounds=4)
    first = GroupSendThenFinalAgent(name="A")
    second = MockAgent(name="B", response="PASS")

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))

    await group.arun("hello", max_rounds=4)

    contents = [msg.content for msg in group.messages]
    assert "I handled the greeting." in contents
    assert "I have responded to the user in the group chat." not in contents


async def test_group_pass_after_group_message_is_ignored_in_same_turn() -> None:
    group = GroupChat(name="ToolPassGroup", max_dispatch_rounds=2)
    first = GroupSendThenPassAgent(name="A")

    group.add_member(GroupMember(name="A", agent=first, description="A"))

    await group.arun("review this", max_rounds=2)

    own_messages = [msg.content for msg in group.messages if msg.sender == "A"]
    assert own_messages == ["I have a substantive point."]


async def test_group_tool_final_response_is_kept_after_real_tool_work() -> None:
    group = GroupChat(name="ToolWorkGroup", max_dispatch_rounds=2)
    first = GroupSendThenWorkAgent(name="A")

    group.add_member(GroupMember(name="A", agent=first, description="A"))

    await group.arun("review scheduling", max_rounds=2)

    contents = [msg.content for msg in group.messages]
    assert "I will inspect core/group.py." in contents
    assert "Done: inspected core/group.py. Next: review dispatch tests." in contents


async def test_group_memory_tools_do_not_propagate_messages() -> None:
    group = GroupChat(name="MemoryGroup")
    first = MockAgent(name="A")

    group.add_member(GroupMember(name="A", agent=first, description="A"))

    result = first.registry.call(
        "group_memory_update",
        section="goal",
        content="Ship group memory v1",
    )

    assert result == "(已更新 memory.goal)"
    assert group.memory.get("goal") == "Ship group memory v1"
    assert group.messages == []
    assert [event.kind for event in group.events.recent()] == [
        "tool_call",
        "memory_update",
    ]


async def test_message_runtime_records_stats_events_callback_and_wake() -> None:
    group = GroupChat(name="MessageRuntimeGroup")
    seen = []
    group._message_callback = seen.append
    group._dispatch_event = asyncio.Event()

    message = group.add_message("A", "PASS")

    assert message.propagate is False
    assert seen == [message]
    assert group.stats.pass_messages == 1
    assert group.events.recent()[-1].kind == "message"
    assert not group._dispatch_event.is_set()

    group.add_message("A", "work available")

    assert group.stats.propagating_messages == 1
    assert group._dispatch_event.is_set()


async def test_structured_group_tools_update_memory_and_propagate() -> None:
    group = GroupChat(name="StructuredGroup")
    first = MockAgent(name="A")
    second = MockAgent(name="B")

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))

    first.registry.call(
        "group_note_scope",
        scope="implement memory tools",
        files="core/group.py",
        next="B should review tests",
    )
    first.registry.call(
        "group_done",
        summary="wired memory tool registration",
        next="add regression tests",
    )
    first.registry.call(
        "group_decision",
        decision="keep memory in-process for v1",
        reason="avoid persistence complexity",
    )

    memory = second.registry.call("group_memory_get")
    assert "implement memory tools" in memory
    assert "wired memory tool registration" in memory
    assert "keep memory in-process for v1" in memory
    assert "B should review tests" in memory
    assert "add regression tests" in memory

    messages = [msg for msg in group.messages if msg.sender == "A"]
    assert [msg.propagate for msg in messages] == [True, True, True]
    assert messages[0].content.startswith("Scope: implement memory tools")
    assert messages[1].content.startswith("Done: wired memory tool registration")
    assert messages[2].content.startswith("Decision: keep memory in-process for v1")
    assert group.stats.propagating_messages == 3
    assert any(event.kind == "tool_call" for event in group.events.recent())
    assert any(event.kind == "memory_update" for event in group.events.recent())


async def test_literal_group_pass_response_is_nonpropagating() -> None:
    group = GroupChat(name="LiteralPassGroup", max_dispatch_rounds=4)
    first = MockAgent(name="A", response="`group_pass()`")
    second = MockAgent(name="B", response="should not run")

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))

    await group.arun("hello", max_rounds=4)

    assert group.messages[-1].sender == "A"
    assert group.messages[-1].content == "`group_pass()`"
    assert not group.messages[-1].propagate
    assert second.response_count == 0
    assert group.stats.pass_messages == 1


async def test_explicit_directed_message_runs_only_mentioned_member_and_synthetic_passes_others() -> None:
    group = GroupChat(name="DirectedGroup", max_dispatch_rounds=5)
    first = SessionAgent(name="A", response="PASS")
    second = SessionAgent(name="B", response="should not run")
    third = SessionAgent(name="C", response="should not run")

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))
    group.add_member(GroupMember(name="C", agent=third, description="C"))

    group.add_message(
        "system",
        "Directed to: A\nhandle this directly",
        kind="system",
        mentions=["A"],
    )
    await group.adispatch(max_rounds=5)

    assert first.response_count == 1
    assert second.response_count == 0
    assert third.response_count == 0
    assert second.session.history[0].role == "user"
    assert "Directed to: A\nhandle this directly" in second.session.history[0].message
    assert isinstance(second.session.history[1], AI)
    assert second.session.history[1].tool_calls
    assert second.session.history[1].tool_calls[0].name == "group_pass"
    assert second.session.history[2].role == "tool"
    assert third.session.history[1].tool_calls[0].name == "group_pass"
    assert group.get_member("B").last_read_message_id >= 1
    assert group.get_member("C").last_read_message_id >= 1
    pass_messages = [
        msg for msg in group.messages
        if msg.sender in {"B", "C"} and msg.content == "PASS"
    ]
    assert len(pass_messages) == 2
    assert all(not msg.propagate for msg in pass_messages)
    assert group.stats.synthetic_passes == 2
    assert group.stats.member_turns == {"A": 1}
    assert sum(event.kind == "synthetic_pass" for event in group.events.recent()) == 2


async def test_explicit_multi_mention_dispatches_all_mentioned_members_only() -> None:
    group = GroupChat(name="MultiDirectedGroup", max_dispatch_rounds=5)
    first = SessionAgent(name="A", response="PASS", delay=0.001)
    second = SessionAgent(name="B", response="PASS", delay=0.001)
    third = SessionAgent(name="C", response="should not run")

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))
    group.add_member(GroupMember(name="C", agent=third, description="C"))

    group.add_message(
        "system",
        "Directed to: A, B\nreview together",
        kind="system",
        mentions=["A", "B"],
    )
    await group.adispatch(max_rounds=5)

    assert first.response_count == 1
    assert second.response_count == 1
    assert third.response_count == 0
    assert group.get_member("C").last_read_message_id >= 1
    assert group.stats.synthetic_passes == 1
    assert group.stats.member_turns == {"A": 1, "B": 1}


async def test_unknown_mention_does_not_trigger_directed_dispatch() -> None:
    group = GroupChat(name="UnknownMentionGroup", max_dispatch_rounds=5)
    first = SessionAgent(name="A", response="PASS", delay=0.001)
    second = SessionAgent(name="B", response="PASS", delay=0.001)

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))
    group.add_message("user", "@Missing this is ordinary text", kind="user")

    await group.adispatch(max_rounds=5)

    assert first.response_count == 1
    assert second.response_count == 1
    assert group.stats.synthetic_passes == 0


async def test_user_text_mentions_are_not_directed_dispatch() -> None:
    group = GroupChat(name="UserTextMentionGroup", max_dispatch_rounds=5)
    first = SessionAgent(name="A", response="PASS", delay=0.001)
    second = SessionAgent(name="B", response="PASS", delay=0.001)

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))

    await group.arun("A is named here as ordinary user text", max_rounds=5)

    assert group.messages[0].mentions == []
    assert first.response_count == 1
    assert second.response_count == 0
    assert group.stats.synthetic_passes == 0


async def test_agent_text_mentions_are_not_directed_dispatch() -> None:
    group = GroupChat(name="AgentTextMentionGroup", max_dispatch_rounds=5)
    first = SessionAgent(name="A", response="PASS", delay=0.001)
    second = SessionAgent(name="B", response="PASS", delay=0.001)

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))
    group.add_message("A", "I agree with B, but this is just text.")

    assert group.messages[-1].mentions == []

    await group.adispatch(max_rounds=5)

    assert second.response_count == 1
    assert group.stats.synthetic_passes == 0


async def test_group_direct_tool_creates_directed_dispatch() -> None:
    group = GroupChat(name="ToolDirectGroup", max_dispatch_rounds=5)
    first = SessionAgent(name="A", response="PASS")
    second = SessionAgent(name="B", response="PASS", delay=0.001)
    third = SessionAgent(name="C", response="should not run")

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))
    group.add_member(GroupMember(name="C", agent=third, description="C"))

    result = first.registry.call(
        "group_direct",
        to=["B"],
        message="please review the implementation",
    )

    assert "已定向发送给 B" in result
    assert group.messages[-1].content.startswith("Directed to: B\n")
    assert group.messages[-1].mentions == ["B"]

    await group.adispatch(max_rounds=5)

    assert second.response_count == 1
    assert third.response_count == 0
    assert group.get_member("C").last_read_message_id >= group.messages[0].id
    assert group.stats.synthetic_passes == 1


async def test_group_handoff_updates_memory_and_creates_directed_dispatch() -> None:
    group = GroupChat(name="HandoffGroup", max_dispatch_rounds=5)
    first = SessionAgent(name="A", response="PASS")
    second = SessionAgent(name="B", response="PASS", delay=0.001)
    third = SessionAgent(name="C", response="should not run")

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))
    group.add_member(GroupMember(name="C", agent=third, description="C"))

    result = first.registry.call(
        "group_handoff",
        to=["B"],
        summary="finished the shell",
        next="fill in game logic",
        files="game.js",
    )

    assert "已交接给 B" in result
    assert "finished the shell" in group.memory.get("done")
    assert "fill in game logic" in group.memory.get("next")
    assert "game.js" in group.memory.get("scopes")
    assert group.messages[-1].mentions == ["B"]
    assert group.messages[-1].content.startswith("Directed to: B\n")
    assert "Handoff summary: finished the shell" in group.messages[-1].content

    await group.adispatch(max_rounds=5)

    assert second.response_count == 1
    assert third.response_count == 0
    assert group.stats.synthetic_passes == 1


async def test_group_handoff_invalid_target_does_not_update_memory() -> None:
    group = GroupChat(name="InvalidHandoffGroup")
    first = SessionAgent(name="A", response="PASS")

    group.add_member(GroupMember(name="A", agent=first, description="A"))

    result = first.registry.call(
        "group_handoff",
        to=["Missing"],
        summary="finished the shell",
        next="fill in game logic",
        files="game.js",
    )

    assert result == "[错误] 成员不存在: Missing"
    assert group.messages == []
    assert group.memory.get("done") == ""
    assert group.memory.get("next") == ""
    assert group.memory.get("scopes") == ""


def test_sync_directed_dispatch_uses_synthetic_pass() -> None:
    group = GroupChat(name="SyncDirectedGroup", max_dispatch_rounds=5)
    first = SessionAgent(name="A", response="PASS")
    second = SessionAgent(name="B", response="should not run")

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))
    group.add_message(
        "system",
        "Directed to: A\nsync handle this",
        kind="system",
        mentions=["A"],
    )
    group.dispatch(max_rounds=5)

    assert first.response_count == 1
    assert second.response_count == 0
    assert second.session.history[1].tool_calls[0].name == "group_pass"
    assert group.stats.synthetic_passes == 1


async def test_group_transcript_is_pruned_to_store_limit() -> None:
    group = GroupChat(name="PruneGroup", message_store_limit=3)

    for idx in range(5):
        group.add_message("A", f"message {idx}")

    assert [msg.id for msg in group.messages] == [3, 4, 5]
    assert [msg.content for msg in group.messages] == [
        "message 2",
        "message 3",
        "message 4",
    ]


async def test_stale_direct_response_is_nonpropagating() -> None:
    group = GroupChat(name="StaleGroup", max_dispatch_rounds=8)
    leader = GroupSendWorkDelayedFinalAgent(name="Leader", delay=0.01)
    dev = MockAgent(name="Dev", response="Dev completed the review.", delay=0.03)
    production = SequenceAgent(
        name="Production",
        responses=["Production is waiting for Dev.", "PASS"],
        delays=[0.05, 0.0],
    )

    group.add_member(GroupMember(name="Leader", agent=leader, description="lead"))
    group.add_member(GroupMember(name="Dev", agent=dev, description="dev"))
    group.add_member(GroupMember(name="Production", agent=production, description="product"))

    await group.arun("review the project", max_rounds=8)

    contents = [msg.content for msg in group.messages]
    assert "Dev completed the review." in contents
    assert "Production is waiting for Dev." not in contents
    pass_messages = [
        msg for msg in group.messages
        if msg.sender == "Production" and msg.content == "PASS"
    ]
    assert pass_messages
    assert not pass_messages[0].propagate
    assert production.response_count >= 2
    assert group.stats.stale_responses >= 1


async def test_stale_real_tool_work_response_is_kept() -> None:
    group = GroupChat(name="StaleWorkGroup", max_dispatch_rounds=8)
    leader = GroupSendWorkDelayedFinalAgent(name="Leader", delay=0.01)
    dev = MockAgent(name="Dev", response="Dev completed the review.", delay=0.03)
    production = DirectWorkDelayedAgent(
        name="Production",
        response="Production completed the delivery review.",
        delay=0.05,
    )

    group.add_member(GroupMember(name="Leader", agent=leader, description="lead"))
    group.add_member(GroupMember(name="Dev", agent=dev, description="dev"))
    group.add_member(GroupMember(name="Production", agent=production, description="product"))

    await group.arun("review the project", max_rounds=8)

    contents = [msg.content for msg in group.messages]
    assert "Dev completed the review." in contents
    assert "Production completed the delivery review." in contents


async def test_dispatch_limit_hit_is_counted() -> None:
    group = GroupChat(name="StatsGroup", max_dispatch_rounds=1)
    first = MockAgent(name="A", response="A started")
    second = MockAgent(name="B", response="B follows")

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    group.add_member(GroupMember(name="B", agent=second, description="B"))

    await group.arun("start", max_rounds=1)

    assert group.stats.dispatch_limit_hits == 1
    assert group.stats.member_turns == {"A": 1}
    assert group.stats.user_dispatch_steps[-1] == 1
    assert any(event.kind == "dispatch_limit" for event in group.events.recent())


async def test_group_clear_resets_messages_memory_and_events_not_stats() -> None:
    group = GroupChat(name="ClearGroup")
    first = MockAgent(name="A")

    group.add_member(GroupMember(name="A", agent=first, description="A"))
    first.registry.call("group_done", summary="finished setup")
    assert group.messages
    assert group.events.recent()
    assert group.memory.get("done")
    assert group.stats.propagating_messages == 1

    group.clear()

    assert group.messages == []
    assert group.events.recent() == []
    assert group.memory.render_summary() == "(empty)"
    assert group.stats.propagating_messages == 1


async def test_member_status_snapshot_reports_public_counts() -> None:
    group = GroupChat(name="SnapshotGroup")
    first = MockAgent(name="A")
    second = MockAgent(name="B")

    group.add_member(GroupMember(name="A", agent=first, description="lead"))
    group.add_member(GroupMember(name="B", agent=second, description="helper"))
    group.add_user_message("coordinate")

    snapshot = group.member_status_snapshot("A")

    assert snapshot == {
        "name": "A",
        "description": "lead",
        "enabled": True,
        "status": "idle",
        "unread": 1,
        "dispatchable": 1,
    }
    assert group.member_unread_count("B") == 1
    assert group.member_dispatchable_count(group.get_member("B")) == 0


if __name__ == "__main__":
    asyncio.run(test_adispatch_concurrency_state_is_consistent())
    asyncio.run(test_injected_dispatch_policy_controls_actual_member_turn())
    asyncio.run(test_injected_policy_controls_directed_empty_response_prompt())
    asyncio.run(test_on_demand_policy_keeps_user_first_responder_entry())
    asyncio.run(test_on_demand_policy_synthetic_passes_normal_messages())
    asyncio.run(test_on_demand_policy_dispatches_explicit_broadcast_messages())
    asyncio.run(test_broadcast_feedback_policy_routes_member_reports_to_originator())
    asyncio.run(test_broadcast_feedback_policy_does_not_route_scope_to_originator())
    asyncio.run(test_on_demand_policy_dispatches_directed_messages_only_to_targets())
    asyncio.run(test_failed_member_keeps_unread_while_other_member_can_continue())
    asyncio.run(test_group_closes_member_llm_clients())
    asyncio.run(test_member_prompt_includes_recent_context_without_duplication())
    asyncio.run(test_group_tool_final_response_is_not_duplicated())
    asyncio.run(test_group_pass_after_group_message_is_ignored_in_same_turn())
    asyncio.run(test_group_tool_final_response_is_kept_after_real_tool_work())
    asyncio.run(test_group_memory_tools_do_not_propagate_messages())
    asyncio.run(test_message_runtime_records_stats_events_callback_and_wake())
    asyncio.run(test_structured_group_tools_update_memory_and_propagate())
    asyncio.run(test_literal_group_pass_response_is_nonpropagating())
    asyncio.run(test_explicit_directed_message_runs_only_mentioned_member_and_synthetic_passes_others())
    asyncio.run(test_explicit_multi_mention_dispatches_all_mentioned_members_only())
    asyncio.run(test_unknown_mention_does_not_trigger_directed_dispatch())
    asyncio.run(test_user_text_mentions_are_not_directed_dispatch())
    asyncio.run(test_agent_text_mentions_are_not_directed_dispatch())
    asyncio.run(test_group_direct_tool_creates_directed_dispatch())
    asyncio.run(test_group_handoff_updates_memory_and_creates_directed_dispatch())
    asyncio.run(test_group_handoff_invalid_target_does_not_update_memory())
    test_sync_directed_dispatch_uses_synthetic_pass()
    asyncio.run(test_group_transcript_is_pruned_to_store_limit())
    asyncio.run(test_stale_direct_response_is_nonpropagating())
    asyncio.run(test_stale_real_tool_work_response_is_kept())
    asyncio.run(test_dispatch_limit_hit_is_counted())
    asyncio.run(test_group_clear_resets_messages_memory_and_events_not_stats())
    asyncio.run(test_member_status_snapshot_reports_public_counts())
