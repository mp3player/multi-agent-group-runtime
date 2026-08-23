import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.group import GroupChat
from core.group_policy import BroadcastFeedbackGroupDispatchPolicy, OnDemandGroupDispatchPolicy
from core.session import Session
from domain.group import GroupMember
from models import AI, ToolCall
from tools.registry import ToolRegistry


class EvalAgent:
    """Small deterministic agent for operational group-flow evaluation tests."""

    def __init__(self, name: str, responses: list[str] | None = None, delay: float = 0.0):
        self.name = name
        self.registry = ToolRegistry()
        self.system_builder = None
        self.session = Session()
        self.responses = list(responses or ["PASS"])
        self.delay = delay
        self.prompts: list[str] = []
        self.response_count = 0

    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        index = self.response_count
        self.response_count += 1
        if index < len(self.responses):
            return self.responses[index]
        return self.responses[-1]

    def run(self, prompt: str) -> str:
        self.prompts.append(prompt)
        index = self.response_count
        self.response_count += 1
        if index < len(self.responses):
            return self.responses[index]
        return self.responses[-1]

    def rebuild_system_prompt(self) -> None:
        return None

    def reset_active_to_system(self) -> None:
        return None


class BroadcastLeader(EvalAgent):
    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        index = self.response_count
        self.response_count += 1
        if index == 0:
            self.registry.call(
                "group_broadcast",
                message="Review this from your own role and report back.",
            )
            return ""
        return "Leader summary: reports received."


class ReportAgent(EvalAgent):
    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.response_count += 1
        self.registry.call(
            "group_report",
            summary=f"{self.name} completed review",
            findings=f"{self.name} finding",
        )
        return ""


class HandoffAgent(EvalAgent):
    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.response_count += 1
        self.registry.call(
            "group_handoff",
            to=["Developer"],
            summary="accepted task and prepared context",
            next="implement the focused change",
            files="core/",
        )
        return ""


class GroupSendWorkDelayedAgent(EvalAgent):
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


class LongFlowLeader(EvalAgent):
    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.response_count += 1
        if self.response_count == 1:
            self.registry.call(
                "group_broadcast",
                message="Review the release plan and report concrete risks.",
            )
            return ""
        if (
            not getattr(self, "handoff_sent", False)
            and "Developer report ready" in prompt
            and "Production report ready" in prompt
        ):
            self.handoff_sent = True
            self.registry.call(
                "group_handoff",
                to=["QA"],
                summary="integrated Developer and Production reports",
                next="validate the final risk list",
                files="ROADMAP.md",
            )
            return ""
        if not getattr(self, "handoff_sent", False):
            self.registry.call("group_pass")
            return ""
        return "Leader final summary: release review is complete."


class LongFlowReportAgent(EvalAgent):
    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.response_count += 1
        self.registry.call(
            "group_report",
            summary=f"{self.name} report ready",
            findings=f"{self.name} found one concrete risk",
        )
        return ""


class LongFlowQaAgent(EvalAgent):
    async def arun(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.response_count += 1
        if "Handoff summary:" in prompt:
            return "QA validated the handed-off risk list."
        self.registry.call("group_pass")
        return ""


async def test_default_policy_first_responder_then_pass_evaluation() -> None:
    group = GroupChat(name="DefaultEval", max_dispatch_rounds=4)
    leader = EvalAgent("Leader", responses=["Leader accepted the task."])
    reviewer = EvalAgent("Reviewer", responses=["PASS"])

    group.add_member(GroupMember("Leader", leader, "coordinates"))
    group.add_member(GroupMember("Reviewer", reviewer, "reviews"))

    await group.arun("please inspect the project", max_rounds=4)

    assert leader.response_count == 1
    assert reviewer.response_count == 1
    assert group.messages[1].sender == "Leader"
    assert group.messages[1].content == "Leader accepted the task."
    assert group.messages[-1].sender == "Reviewer"
    assert group.messages[-1].content == "PASS"
    assert not group.messages[-1].propagate
    assert group.stats.pass_messages == 1
    assert "Leader accepted the task." in reviewer.prompts[0]


async def test_on_demand_handoff_targets_one_member_and_synthetic_passes_others() -> None:
    group = GroupChat(
        name="OnDemandEval",
        dispatch_policy=OnDemandGroupDispatchPolicy(),
        max_dispatch_rounds=6,
    )
    leader = HandoffAgent("Leader")
    developer = EvalAgent("Developer", responses=["Developer completed the handoff."])
    production = EvalAgent("Production", responses=["Production should not run."])

    group.add_member(GroupMember("Leader", leader, "coordinates"))
    group.add_member(GroupMember("Developer", developer, "implements"))
    group.add_member(GroupMember("Production", production, "checks release"))

    await group.arun("coordinate implementation", max_rounds=6)

    assert leader.response_count == 1
    assert developer.response_count == 1
    assert production.response_count == 0
    assert "accepted task and prepared context" in group.memory.get("done")
    assert "implement the focused change" in group.memory.get("next")
    assert any(msg.mentions == ["Developer"] for msg in group.messages)
    assert any(
        msg.sender == "Production" and msg.content == "PASS" and not msg.propagate
        for msg in group.messages
    )
    assert group.stats.synthetic_passes >= 1


async def test_broadcast_feedback_routes_reports_back_to_originator() -> None:
    group = GroupChat(
        name="BroadcastFeedbackEval",
        dispatch_policy=BroadcastFeedbackGroupDispatchPolicy(),
        max_dispatch_rounds=8,
    )
    leader = BroadcastLeader("Leader")
    developer = ReportAgent("Developer")
    production = ReportAgent("Production")

    group.add_member(GroupMember("Leader", leader, "coordinates"))
    group.add_member(GroupMember("Developer", developer, "reviews code"))
    group.add_member(GroupMember("Production", production, "reviews product"))

    await group.arun("please coordinate a review", max_rounds=8)

    assert leader.response_count >= 2
    assert developer.response_count == 1
    assert production.response_count == 1
    assert any(msg.dispatch_mode == "broadcast" for msg in group.messages)
    assert sum(msg.dispatch_mode == "feedback" for msg in group.messages) == 2
    assert "Developer completed review" in leader.prompts[-1]
    assert "Production completed review" in leader.prompts[-1]
    assert any(
        msg.sender == "Leader" and msg.content == "Leader summary: reports received."
        for msg in group.messages
    )


async def test_stale_direct_response_guard_keeps_real_tool_work_evaluation() -> None:
    group = GroupChat(name="StaleEval", max_dispatch_rounds=8)
    leader = GroupSendWorkDelayedAgent("Leader", delay=0.01)
    developer = EvalAgent("Developer", responses=["Developer completed review."], delay=0.03)
    production = EvalAgent(
        "Production",
        responses=["Production stale comment.", "PASS"],
        delay=0.05,
    )

    group.add_member(GroupMember("Leader", leader, "coordinates"))
    group.add_member(GroupMember("Developer", developer, "reviews code"))
    group.add_member(GroupMember("Production", production, "reviews product"))

    await group.arun("review the project", max_rounds=8)

    contents = [msg.content for msg in group.messages]
    assert "Developer completed review." in contents
    assert "Production stale comment." not in contents
    assert any(
        msg.sender == "Production" and msg.content == "PASS" and not msg.propagate
        for msg in group.messages
    )
    assert group.stats.stale_responses >= 1


async def test_broadcast_feedback_long_flow_is_observable_and_bounded() -> None:
    group = GroupChat(
        name="LongFlowEval",
        dispatch_policy=BroadcastFeedbackGroupDispatchPolicy(),
        max_dispatch_rounds=16,
    )
    leader = LongFlowLeader("Leader")
    developer = LongFlowReportAgent("Developer")
    production = LongFlowReportAgent("Production")
    qa = LongFlowQaAgent("QA")

    group.add_member(GroupMember("Leader", leader, "coordinates"))
    group.add_member(GroupMember("Developer", developer, "reviews code"))
    group.add_member(GroupMember("Production", production, "reviews product"))
    group.add_member(GroupMember("QA", qa, "validates risk"))

    await group.arun("coordinate a full release review", max_rounds=16)

    assert leader.response_count >= 3
    assert developer.response_count == 1
    assert production.response_count == 1
    assert qa.response_count >= 1
    assert group.stats.dispatch_limit_hits == 0
    assert group.stats.user_dispatch_steps[-1] <= 10
    assert group.stats.synthetic_passes >= 3
    assert any(event.kind == "synthetic_pass" for event in group.events.recent())
    assert any(msg.dispatch_mode == "broadcast" for msg in group.messages)
    assert sum(msg.dispatch_mode == "feedback" for msg in group.messages) == 2
    assert any(msg.mentions == ["QA"] for msg in group.messages)
    assert any(
        msg.sender == "Developer" and "Developer report ready" in msg.content
        for msg in group.messages
    )
    assert any(
        msg.sender == "Production" and "Production report ready" in msg.content
        for msg in group.messages
    )
    assert any(
        msg.sender == "QA"
        and msg.content == "QA validated the handed-off risk list."
        for msg in group.messages
    )
    assert "integrated Developer and Production reports" in group.memory.get("done")
    assert "validate the final risk list" in qa.prompts[-1]


if __name__ == "__main__":
    asyncio.run(test_default_policy_first_responder_then_pass_evaluation())
    asyncio.run(test_on_demand_handoff_targets_one_member_and_synthetic_passes_others())
    asyncio.run(test_broadcast_feedback_routes_reports_back_to_originator())
    asyncio.run(test_stale_direct_response_guard_keeps_real_tool_work_evaluation())
    asyncio.run(test_broadcast_feedback_long_flow_is_observable_and_bounded())
