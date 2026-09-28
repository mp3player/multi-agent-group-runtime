"""Deterministic model boundary for real runtime and collaboration tests."""

from copy import deepcopy

from models import AI, Chunk


class ScriptedModel:
    model = "scripted"
    context_window = 131072  # Explicit capacity of this deterministic test provider.
    base_url = "http://unused.invalid"

    def __init__(self, *responses: AI) -> None:
        self.responses = list(responses)
        self.requests = []
        self.tool_schemas = []
        self.streams_closed = 0

    def _next(self, messages, **kwargs):
        self.requests.append(deepcopy(messages))
        self.tool_schemas.append(deepcopy(kwargs.get("tools")))
        return self.responses.pop(0)

    def invoke(self, messages, **kwargs):
        return {"choices": [{"message": self._next(messages, **kwargs).to_dict()}]}

    async def ainvoke(self, messages, **kwargs):
        return self.invoke(messages, **kwargs)

    def stream(self, messages, **kwargs):
        response = self._next(messages, **kwargs)
        try:
            # Exercise the legal mixed-delta shape, including final content.
            yield Chunk(message=response.message, reasoning=response.reasoning)
            if response.tool_calls:
                yield Chunk(tool_calls=response.tool_calls)
            yield Chunk(finish_reason="stop")
        finally:
            self.streams_closed += 1

    async def astream(self, messages, **kwargs):
        source = self.stream(messages, **kwargs)
        try:
            for chunk in source:
                yield chunk
        finally:
            source.close()


async def execute(agent, mode, message="go"):
    if mode == "run":
        return agent.run(message)
    if mode == "arun":
        return await agent.arun(message)
    if mode == "run_stream":
        return "".join(chunk.message for chunk in agent.run_stream(message))
    return "".join([chunk.message async for chunk in agent.arun_stream(message)])
