"""LLM integration module for OpenAI-compatible endpoints over httpx.

Configuration is read from `.env`:
    BaseURL    endpoint URL, for example https://xxx/v2/...
    BaseKey    API Key
    BaseModel  default model name
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Iterator

import httpx

from core.config import MASConfig
from core.llm_runtime import (
    LLMError,
    RateLimitError,
    ServerError,
    ToolCallAccumulator,
    aiter_sse_data,
    get_config,
    iter_sse_data,
    parse_sse_data,
    raise_for_status,
    to_dict_list,
)
from models import Chunk, Message


class LLMClient:
    """Lightweight client for OpenAI-compatible APIs.

    Methods:
        invoke     non-streaming, returns full response dict
        stream     streaming, yields Chunk deltas
        ainvoke    async non-streaming
        astream    async streaming
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = MASConfig.TIMEOUT,
        usage_monitor: Any | None = None,
        usage_label: str = "",
    ) -> None:
        cfg = get_config()
        self.base_url = (base_url or cfg["base_url"]).rstrip("/")
        self.api_key = api_key or cfg["api_key"]
        self.model = model or cfg["model"]
        self.timeout = timeout
        self.usage_monitor = usage_monitor
        self.usage_label = usage_label or self.model
        if not self.base_url or not self.api_key:
            raise LLMError("缺少 BaseURL 或 BaseKey，请检查 .env 配置")
        if self.base_url.endswith("/chat/completions"):
            self._chat_endpoint = self.base_url
        else:
            self._chat_endpoint = f"{self.base_url}/chat/completions"
        self._async_client: httpx.AsyncClient | None = None

    # ----- Internal helpers -----

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(
        self,
        messages: list[Message],
        model: str | None,
        stream: bool,
        temperature: float | None,
        max_tokens: int | None,
        **extra: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": to_dict_list(messages),
            "stream": stream,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        payload.update(extra)
        return payload

    async def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(
                timeout=self.timeout,
                trust_env=False,
            )
        return self._async_client

    async def aclose(self) -> None:
        """Close the reusable async HTTP client, if it was created."""
        if self._async_client is not None and not self._async_client.is_closed:
            await self._async_client.aclose()

    # ----- Sync non-streaming -----

    def invoke(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Run a non-streaming request and return the response JSON dict."""
        payload = self._payload(
            messages, model, stream=False,
            temperature=temperature, max_tokens=max_tokens, **extra,
        )
        try:
            resp = httpx.post(
                self._chat_endpoint,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
                trust_env=False,
            )
        except httpx.RequestError as e:
            raise LLMError(f"请求失败: {e}") from e

        raise_for_status(resp.status_code, resp.text)
        try:
            data = resp.json()
        except ValueError as e:
            raise LLMError(f"响应不是合法 JSON: {e}\n{resp.text}") from e
        self._record_usage(data)
        return data

    # ----- Sync streaming -----

    def stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **extra: Any,
    ) -> Iterator[Chunk]:
        """Run a streaming request.

        - content / reasoning deltas are yielded as they arrive
        - tool_calls are yielded once at stream end
        - the final Chunk carries finish_reason
        """
        payload = self._payload(
            messages, model, stream=True,
            temperature=temperature, max_tokens=max_tokens, **extra,
        )
        # Accumulator for streamed tool_call deltas.
        acc = ToolCallAccumulator()
        finish_reason = None
        try:
            with httpx.stream(
                "POST",
                self._chat_endpoint,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
                trust_env=False,
            ) as resp:
                body = None
                if resp.status_code >= 400:
                    body = resp.read().decode("utf-8", errors="ignore")
                raise_for_status(resp.status_code, body)
                for data in iter_sse_data(resp.iter_lines()):
                    parsed = parse_sse_data(data, acc)
                    if parsed is not None:
                        if parsed.finish_reason:
                            finish_reason = parsed.finish_reason
                        # Yield content/reasoning deltas immediately.
                        if parsed.message or parsed.reasoning:
                            yield parsed
                    # finish_reason can also be tracked by the accumulator.
                    if acc.finish_reason:
                        finish_reason = acc.finish_reason
        except httpx.RequestError as e:
            raise LLMError(f"流式请求失败: {e}") from e

        # At stream end, yield complete tool calls if any were accumulated.
        tool_calls = acc.build_tool_calls()
        if tool_calls:
            yield Chunk(tool_calls=tool_calls, finish_reason=finish_reason)
        elif finish_reason:
            # No tool calls, but still emit the finish signal.
            yield Chunk(finish_reason=finish_reason)

    # ----- Async non-streaming -----

    async def ainvoke(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Run an async non-streaming request and return the response JSON dict."""
        payload = self._payload(
            messages, model, stream=False,
            temperature=temperature, max_tokens=max_tokens, **extra,
        )
        try:
            client = await self._get_async_client()
            resp = await client.post(
                self._chat_endpoint,
                headers=self._headers(),
                json=payload,
            )
        except httpx.RequestError as e:
            raise LLMError(f"请求失败: {e}") from e

        raise_for_status(resp.status_code, resp.text)
        try:
            data = resp.json()
        except ValueError as e:
            raise LLMError(f"响应不是合法 JSON: {e}\n{resp.text}") from e
        self._record_usage(data)
        return data

    def _record_usage(self, response: dict[str, Any]) -> None:
        """Send response usage to the optional monitor."""
        if self.usage_monitor is None:
            return
        record = getattr(self.usage_monitor, "record", None)
        if record is None:
            return
        record(self.usage_label, response, model=self.model)

    # ----- Async streaming -----

    async def astream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **extra: Any,
    ) -> AsyncIterator[Chunk]:
        """Run an async streaming request.

        - content / reasoning deltas are yielded as they arrive
        - tool_calls are yielded once at stream end
        - the final Chunk carries finish_reason
        """
        payload = self._payload(
            messages, model, stream=True,
            temperature=temperature, max_tokens=max_tokens, **extra,
        )
        acc = ToolCallAccumulator()
        finish_reason = None
        try:
            client = await self._get_async_client()
            async with client.stream(
                "POST",
                self._chat_endpoint,
                headers=self._headers(),
                json=payload,
            ) as resp:
                body = None
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="ignore")
                raise_for_status(resp.status_code, body)
                async for data in aiter_sse_data(resp.aiter_lines()):
                    parsed = parse_sse_data(data, acc)
                    if parsed is not None:
                        if parsed.finish_reason:
                            finish_reason = parsed.finish_reason
                        if parsed.message or parsed.reasoning:
                            yield parsed
                    if acc.finish_reason:
                        finish_reason = acc.finish_reason
        except httpx.RequestError as e:
            raise LLMError(f"流式请求失败: {e}") from e

        tool_calls = acc.build_tool_calls()
        if tool_calls:
            yield Chunk(tool_calls=tool_calls, finish_reason=finish_reason)
        elif finish_reason:
            yield Chunk(finish_reason=finish_reason)


# Convenience singleton.
_default_client: LLMClient | None = None


def get_client() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client
