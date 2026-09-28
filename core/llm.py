"""LLM integration module for OpenAI-compatible endpoints over httpx.

Explicit constructors do not read environment. ``from_env`` reads:
    BaseURL    endpoint URL, for example https://xxx/v2/...
    BaseKey    API Key
    BaseModel  default model name
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Iterator
from pathlib import Path

import httpx

from core import defaults
from core.llm_runtime import (
    ContextWindowExceeded,
    LLMError,
    RateLimitError,
    ServerError,
    ToolCallAccumulator,
    aiter_sse_data,
    iter_sse_data,
    parse_sse_data,
    raise_for_status,
    to_dict_list,
)
from models import Chunk, Message
from core.llm_runtime.errors import raise_provider_error, validate_invoke_completion
from core.llm_runtime.sse import normalize_usage, validate_stream_compatibility


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
        timeout: float = defaults.TIMEOUT,
        usage_monitor: Any | None = None,
        usage_label: str = "",
        stream_usage: bool = False,
        trust_env: bool = True,
        stream_compatibility: str = "standard",
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.timeout = timeout
        self.usage_monitor = usage_monitor
        self.usage_label = usage_label or self.model
        self.stream_usage = stream_usage
        self.trust_env = trust_env
        validate_stream_compatibility(stream_compatibility)
        self.stream_compatibility = stream_compatibility
        self.usage_error_count = 0
        self.last_usage_error: str | None = None
        if not self.base_url or not self.api_key:
            raise LLMError("Missing model endpoint or API key; pass them explicitly or use LLMClient.from_env()")
        if self.base_url.endswith("/chat/completions"):
            self._chat_endpoint = self.base_url
        else:
            self._chat_endpoint = f"{self.base_url}/chat/completions"
        self._async_client: httpx.AsyncClient | None = None

    @classmethod
    def from_env(
        cls, env_path: str | Path | None = None, *,
        usage_monitor: Any | None = None, usage_label: str = "",
    ) -> "LLMClient":
        """Explicit convenience factory using the application's config parser."""
        from application.agent_config import AgentAppConfig
        config = AgentAppConfig.from_env(env_path).llm
        return cls(base_url=config.base_url, api_key=config.api_key, model=config.model,
                   timeout=config.timeout, usage_monitor=usage_monitor, usage_label=usage_label,
                   stream_usage=config.stream_usage, trust_env=config.trust_env,
                   stream_compatibility=config.stream_compatibility)

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
        if stream and self.stream_usage:
            payload["stream_options"] = {**payload.get("stream_options", {}), "include_usage": True}
        return payload

    async def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(
                timeout=self.timeout,
                trust_env=self.trust_env,
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
                trust_env=self.trust_env,
            )
        except httpx.RequestError as e:
            raise LLMError(f"Request failed: {e}") from e

        raise_for_status(resp.status_code, resp.text)
        try:
            data = resp.json()
        except ValueError as e:
            raise LLMError(f"Response is not valid JSON: {e}\n{resp.text}") from e
        self._record_usage(data, model=payload["model"])
        if isinstance(data, dict) and data.get("error") is not None:
            raise_provider_error(data["error"], f"Model response error: {data['error']}")
        validate_invoke_completion(data)
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
        - the final Chunk carries finish_reason, usage and response_model
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
                trust_env=self.trust_env,
            ) as resp:
                body = None
                if resp.status_code >= 400:
                    body = resp.read().decode("utf-8", errors="ignore")
                raise_for_status(resp.status_code, body)
                for data in iter_sse_data(resp.iter_lines()):
                    parsed = parse_sse_data(data, acc, stream_compatibility=self.stream_compatibility)
                    if acc.done:
                        break
                    if parsed is not None:
                        if parsed.finish_reason:
                            finish_reason = parsed.finish_reason
                        # Yield content/reasoning deltas immediately.
                        if parsed.message or parsed.reasoning:
                            yield Chunk(message=parsed.message, reasoning=parsed.reasoning,
                                        response_model=acc.response_model or payload["model"])
                    # finish_reason can also be tracked by the accumulator.
                    if acc.finish_reason:
                        finish_reason = acc.finish_reason
        except httpx.RequestError as e:
            raise LLMError(f"Streaming request failed: {e}") from e
        finally:
            self._record_usage({"usage": acc.usage, "model": acc.response_model}, model=payload["model"])

        # EOF is not proof of completion. Never expose unfinished calls to the
        # Agent, even if their accumulated arguments happen to be valid JSON.
        acc.validate_complete()
        tool_calls = acc.build_tool_calls()
        if tool_calls:
            yield Chunk(tool_calls=tool_calls, finish_reason=finish_reason, usage=acc.usage,
                        response_model=acc.response_model or payload["model"])
        elif finish_reason:
            # No tool calls, but still emit the finish signal.
            yield Chunk(finish_reason=finish_reason, usage=acc.usage,
                        response_model=acc.response_model or payload["model"])

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
            raise LLMError(f"Request failed: {e}") from e

        raise_for_status(resp.status_code, resp.text)
        try:
            data = resp.json()
        except ValueError as e:
            raise LLMError(f"Response is not valid JSON: {e}\n{resp.text}") from e
        self._record_usage(data, model=payload["model"])
        if isinstance(data, dict) and data.get("error") is not None:
            raise_provider_error(data["error"], f"Model response error: {data['error']}")
        validate_invoke_completion(data)
        return data

    def _record_usage(self, response: dict[str, Any], *, model: str) -> None:
        """Record complete billing counts without altering partial response metadata.

        Incomplete counts are observable through usage_error_count and
        last_usage_error, and never become measured zeros in monitor totals.
        """
        if self.usage_monitor is None:
            return
        try:
            usage = normalize_usage(response.get("usage"))
            if usage is None:
                if response.get("usage"):
                    raise ValueError("Invalid model usage count")
                return
            missing = [field for field in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                       if field not in usage]
            if missing:
                raise ValueError('Incomplete model usage counters: missing ' + ', '.join(missing))
            response = {**response, "usage": usage}
            record = getattr(self.usage_monitor, "record", None)
            if record is not None:
                response_model = response.get("model")
                if not isinstance(response_model, str) or not response_model:
                    response = {**response, "model": model}
                record(self.usage_label, response, model=model)
        except Exception as exc:
            self.usage_error_count += 1
            self.last_usage_error = f'{type(exc).__name__}: {exc}'[:240]

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
        - the final Chunk carries finish_reason, usage and response_model
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
                    parsed = parse_sse_data(data, acc, stream_compatibility=self.stream_compatibility)
                    if acc.done:
                        break
                    if parsed is not None:
                        if parsed.finish_reason:
                            finish_reason = parsed.finish_reason
                        if parsed.message or parsed.reasoning:
                            yield Chunk(message=parsed.message, reasoning=parsed.reasoning,
                                        response_model=acc.response_model or payload["model"])
                    if acc.finish_reason:
                        finish_reason = acc.finish_reason
        except httpx.RequestError as e:
            raise LLMError(f"Streaming request failed: {e}") from e
        finally:
            self._record_usage({"usage": acc.usage, "model": acc.response_model}, model=payload["model"])

        acc.validate_complete()
        tool_calls = acc.build_tool_calls()
        if tool_calls:
            yield Chunk(tool_calls=tool_calls, finish_reason=finish_reason, usage=acc.usage,
                        response_model=acc.response_model or payload["model"])
        elif finish_reason:
            yield Chunk(finish_reason=finish_reason, usage=acc.usage,
                        response_model=acc.response_model or payload["model"])


# Convenience singleton.
_default_client: LLMClient | None = None


def get_client() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = LLMClient.from_env()
    return _default_client
