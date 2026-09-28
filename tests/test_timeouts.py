import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.agent import Agent, AgentTimeoutError


class SlowLLM:
    model = "slow"
    context_window = 32768
    base_url = "http://slow.local"

    async def ainvoke(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        return {"choices": [{"message": {"content": "done"}}]}

    def invoke(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "done"}}]}


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


if __name__ == "__main__":
    asyncio.run(test_agent_arun_timeout_raises())
