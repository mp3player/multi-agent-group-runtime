import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.llm import LLMClient, RateLimitError, ServerError  # noqa: E402
from core.llm_runtime import (  # noqa: E402
    ToolCallAccumulator,
    aiter_sse_data,
    iter_sse_data,
    parse_sse_data,
    raise_for_status,
)


def test_sse_data_parser_handles_complete_events() -> None:
    payload = '{"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}'
    events = list(iter_sse_data([
        ": keep-alive",
        f"data: {payload}",
        "",
        "data: [DONE]",
        "",
    ]))

    assert events == [payload, "[DONE]"]

    acc = ToolCallAccumulator()
    chunk = parse_sse_data(events[0], acc)
    assert chunk is not None
    assert chunk.message == "hello"
    assert parse_sse_data(events[1], acc) is None


def test_http_error_classification() -> None:
    try:
        raise_for_status(429, "rate limited")
    except RateLimitError:
        pass
    else:
        raise AssertionError("429 should raise RateLimitError")

    try:
        raise_for_status(503, "unavailable")
    except ServerError:
        pass
    else:
        raise AssertionError("5xx should raise ServerError")


async def test_async_sse_data_parser_handles_complete_events() -> None:
    async def lines():  # type: ignore[no-untyped-def]
        for line in ("data: first", "", "data: [DONE]", ""):
            yield line

    assert [event async for event in aiter_sse_data(lines())] == ["first", "[DONE]"]


async def test_async_client_is_reused() -> None:
    proxy_env = {
        name: os.environ.pop(name)
        for name in ("ALL_PROXY", "all_proxy")
        if name in os.environ
    }
    client = LLMClient(
        base_url="http://127.0.0.1:1/v1",
        api_key="dummy",
        model="dummy",
    )
    try:
        first = await client._get_async_client()
        second = await client._get_async_client()
        assert first is second
        await client.aclose()
        assert first.is_closed
    finally:
        os.environ.update(proxy_env)


if __name__ == "__main__":
    test_sse_data_parser_handles_complete_events()
    test_http_error_classification()
    asyncio.run(test_async_sse_data_parser_handles_complete_events())
    asyncio.run(test_async_client_is_reused())
