import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.agent import Agent, AgentTimeoutError
from core.group import GroupChat, GroupTimeoutError
from domain.group import GroupMember
from tools.registry import ToolRegistry


class SlowLLM:
    model = "slow"
    base_url = "http://slow.local"

    async def ainvoke(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        return {"choices": [{"message": {"content": "done"}}]}

    def invoke(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "done"}}]}


class SlowAgent:
    def __init__(self) -> None:
        self.registry = ToolRegistry()
        self.system_builder = None
        self.response_count = 0

    async def arun(self, prompt: str) -> str:
        self.response_count += 1
        await asyncio.sleep(0.05)
        return "late response"

    def run(self, prompt: str) -> str:
        self.response_count += 1
        return "late response"

    def rebuild_system_prompt(self) -> None:
        return None

    def reset_active_to_system(self) -> None:
        return None


async def test_agent_arun_timeout_raises() -> None:
    agent = Agent(
        SlowLLM(),  # type: ignore[arg-type]
        run_timeout=0.01,
    )

    try:
        await agent.arun("slow request")
    except AgentTimeoutError as e:
        assert "timeout" in str(e)
    else:
        raise AssertionError("expected AgentTimeoutError")


async def test_group_arun_timeout_cleans_running_state() -> None:
    group = GroupChat(name="TimeoutGroup", run_timeout=0.01)
    member = GroupMember(name="A", agent=SlowAgent(), description="slow")
    group.add_member(member)

    try:
        await group.arun("slow group request")
    except GroupTimeoutError as e:
        assert "timeout" in str(e)
    else:
        raise AssertionError("expected GroupTimeoutError")

    assert member.status == "idle"
    assert group.run_state.active is False
    group.add_message("system", "still usable", kind="system", propagate=False)
    assert group.messages[-1].content == "still usable"


if __name__ == "__main__":
    asyncio.run(test_agent_arun_timeout_raises())
    asyncio.run(test_group_arun_timeout_cleans_running_state())
