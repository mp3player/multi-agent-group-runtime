"""Provider context errors and request-scoped usage across all client modes."""
from contextlib import asynccontextmanager
import json

import httpx
import pytest

from core.llm import LLMClient
from core.llm_runtime import errors
from core.llm_runtime.errors import LLMError, RateLimitError, ServerError, raise_for_status
from core.llm_runtime.sse import ToolCallAccumulator, parse_sse_data
from core.usage import UsageMonitor
from models import User


MODES = ('invoke', 'ainvoke', 'stream', 'astream')
USAGE = {'prompt_tokens': 11, 'completion_tokens': 3, 'total_tokens': 14}


def sse(value):
    return 'data: ' + json.dumps(value) + '\n\n'


def stream_body(*, usage=USAGE, model='resolved-model', reason='stop'):
    frames = [{'model': model, 'choices': [{'delta': {'content': 'answer'}, 'finish_reason': None}]},
              {'choices': [{'delta': {}, 'finish_reason': reason}]}]
    if usage is not None:
        frames += [{'choices': [], 'usage': usage}, {'choices': [], 'usage': usage}]
    return ''.join(sse(frame) for frame in frames) + 'data: [DONE]\n\n'


@asynccontextmanager
async def provider_client(monkeypatch, handler, **options):
    requests = []
    def provide(request):
        body = json.loads(request.content)
        requests.append(body)
        return handler(body)
    transport = httpx.MockTransport(provide)
    monitor = UsageMonitor()
    client = LLMClient('http://fixture.invalid/v1', 'test-key', 'default-model',
                       usage_monitor=monitor, **options)
    with httpx.Client(transport=transport, trust_env=False) as sync_http:
        monkeypatch.setattr(httpx, 'post', lambda url, *, trust_env=True, **kw: sync_http.post(url, **kw))
        monkeypatch.setattr(httpx, 'stream', lambda method, url, *, trust_env=True, **kw: sync_http.stream(method, url, **kw))
        client._async_client = httpx.AsyncClient(transport=transport, trust_env=False)
        try:
            yield client, monitor, requests
        finally:
            await client.aclose()


async def request(client, mode, **kwargs):
    messages = [User('task')]
    if mode == 'invoke':
        return client.invoke(messages, **kwargs)
    if mode == 'ainvoke':
        return await client.ainvoke(messages, **kwargs)
    if mode == 'stream':
        return list(client.stream(messages, **kwargs))
    return [chunk async for chunk in client.astream(messages, **kwargs)]


@pytest.mark.parametrize('mode', ['invoke', 'ainvoke'])
@pytest.mark.parametrize('payload', [
    [], {}, {'choices': []}, {'choices': [None]},
    {'choices': [{'finish_reason': '   '}]},
    {'choices': [{'finish_reason': []}]},
    {'choices': [{'finish_reason': 1}]},
    {'choices': [{'finish_reason': 'length'}]},
    {'choices': [{'finish_reason': 'stop'}, {'finish_reason': None}]},
])
async def test_nonstream_client_rejects_invalid_completion_envelopes(monkeypatch, mode, payload):
    async with provider_client(monkeypatch, lambda _: httpx.Response(200, json=payload)) as (client, _, _):
        with pytest.raises(LLMError):
            await request(client, mode)


@pytest.mark.parametrize('code', ['context_length_exceeded', 'context_window_exceeded'])
@pytest.mark.parametrize('mode', MODES)
async def test_structured_http_overflow_is_typed(monkeypatch, mode, code):
    async with provider_client(monkeypatch, lambda body: httpx.Response(
        400, json={'error': {'code': code, 'message': 'input too large'}})) as (client, _, _):
        with pytest.raises(LLMError) as caught:
            await request(client, mode)
    assert type(caught.value).__name__ == 'ContextWindowExceeded'
    assert isinstance(caught.value, getattr(errors, 'ContextWindowExceeded'))


@pytest.mark.parametrize('status,body,expected', [
    (400, {'error': {'message': 'context length exceeded'}}, LLMError),
    (413, {'error': {'code': 'request_too_large'}}, LLMError),
    (400, {'error': {'code': 'length'}}, LLMError),
    (400, {'error': {'code': 'content_filter'}}, LLMError),
    (401, {'error': {'code': 'context_length_exceeded'}}, LLMError),
    (429, {'error': {'code': 'context_length_exceeded'}}, RateLimitError),
    (500, {'error': {'code': 'context_length_exceeded'}}, ServerError),
    (400, {'error': {'code': ['context_length_exceeded']}}, LLMError),
])
def test_http_errors_do_not_guess_overflow(status, body, expected):
    with pytest.raises(LLMError) as caught:
        raise_for_status(status, json.dumps(body))
    assert type(caught.value) is expected


@pytest.mark.parametrize('field', ['code', 'type'])
@pytest.mark.parametrize('mode', ['stream', 'astream'])
async def test_structured_sse_overflow_is_typed(monkeypatch, mode, field):
    async with provider_client(monkeypatch, lambda body: httpx.Response(
        200, text=sse({'error': {field: 'context_length_exceeded'}}))) as (client, _, _):
        with pytest.raises(LLMError) as caught:
            await request(client, mode)
    assert type(caught.value).__name__ == 'ContextWindowExceeded'


@pytest.mark.parametrize('error', [
    {'message': 'maximum context length exceeded'}, {'code': 'length'},
    {'code': 'content_filter'}, {'code': 'context_length_exceeded_other'},
])
def test_sse_error_text_does_not_guess_overflow(error):
    with pytest.raises(LLMError) as caught:
        parse_sse_data(json.dumps({'error': error}), ToolCallAccumulator())
    assert type(caught.value) is LLMError


def test_usage_only_frame_is_retained_as_metadata_without_visible_delta():
    chunk = parse_sse_data(json.dumps({'model': 'resolved-model', 'choices': [], 'usage': USAGE}),
                           ToolCallAccumulator())
    assert chunk is not None
    assert chunk.usage == USAGE
    assert chunk.response_model == 'resolved-model'
    assert chunk.message == '' and chunk.reasoning == '' and not chunk.tool_calls


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('response_model,expected_model', [
    ('resolved-model', 'resolved-model'), (None, 'request-model'), ('', 'request-model'),
])
async def test_usage_is_recorded_once_with_effective_request_model(monkeypatch, mode, response_model, expected_model):
    raw = {'model': response_model, 'choices': [{'message': {'role': 'assistant', 'content': 'answer'},
                                             'finish_reason': 'stop'}], 'usage': USAGE}
    async with provider_client(monkeypatch, lambda body: httpx.Response(
        200, text=stream_body(model=response_model) if body['stream'] else json.dumps(raw))) as (client, monitor, requests):
        result = await request(client, mode, model='request-model')
    assert requests[0]['model'] == 'request-model'
    assert len(monitor.records()) == 1
    record = monitor.records()[0]
    assert (record.model, record.prompt_tokens, record.completion_tokens, record.total_tokens) == (expected_model, 11, 3, 14)
    if 'stream' in mode:
        assert ''.join(chunk.message for chunk in result) == 'answer'
        assert len([chunk for chunk in result if chunk.usage is not None]) == 1
        assert result[-1].usage == USAGE
        assert result[-1].response_model == expected_model
    else:
        assert result == raw  # Caller receives the original response shape.


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('usage,missing', [
    ({'completion_tokens': 3, 'total_tokens': 14}, ('prompt_tokens',)),
    ({'prompt_tokens': 11, 'total_tokens': 14}, ('completion_tokens',)),
    ({'prompt_tokens': 11, 'completion_tokens': 3}, ('total_tokens',)),
    ({'prompt_tokens': 17}, ('completion_tokens', 'total_tokens')),
])
async def test_partial_usage_keeps_metadata_without_inventing_billing(monkeypatch, mode, usage, missing):
    from core.agent import Agent
    from core.agent_runtime.options import AgentOptions
    from tests.runtime_fakes import execute

    raw = {'choices': [{'message': {'role': 'assistant', 'content': 'answer'},
                        'finish_reason': 'stop'}], 'usage': usage}
    async with provider_client(monkeypatch, lambda body: httpx.Response(
        200, text=stream_body(usage=usage) if body['stream'] else json.dumps(raw))) as (client, monitor, _):
        result = await request(client, mode)
        if 'stream' in mode:
            assert result[-1].usage == usage
        else:
            assert result == raw

        agent = Agent(client, options=AgentOptions(context_window=100000))
        agent_mode = {'invoke': 'run', 'ainvoke': 'arun', 'stream': 'run_stream', 'astream': 'arun_stream'}[mode]
        assert await execute(agent, agent_mode, 'task') == 'answer'
        status_usage = agent.context_status()['last_usage']
        assert status_usage['phase'] == 'main'
        for field in ('prompt_tokens', 'completion_tokens'):
            if field in usage:
                assert status_usage[field] == usage[field]
            else:
                assert field not in status_usage

    assert monitor.records() == []
    assert monitor.summary() == {}
    assert client.usage_error_count == 2  # Once per request, despite duplicate stream frames.
    assert 'incomplete' in client.last_usage_error.lower()
    assert all(field in client.last_usage_error for field in missing)


@pytest.mark.parametrize('mode', MODES)
async def test_complete_zero_usage_is_known_and_billable(monkeypatch, mode):
    usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    raw = {'choices': [{'message': {'role': 'assistant', 'content': 'answer'},
                        'finish_reason': 'stop'}], 'usage': usage}
    async with provider_client(monkeypatch, lambda body: httpx.Response(
        200, text=stream_body(usage=usage) if body['stream'] else json.dumps(raw))) as (client, monitor, _):
        await request(client, mode)
    assert len(monitor.records()) == 1
    record = monitor.records()[0]
    assert (record.prompt_tokens, record.completion_tokens, record.total_tokens) == (0, 0, 0)
    assert client.usage_error_count == 0


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('enabled', [False, True])
async def test_stream_usage_request_option_requires_explicit_capability(monkeypatch, mode, enabled):
    raw = {'choices': [{'message': {'role': 'assistant', 'content': 'answer'}, 'finish_reason': 'stop'}]}
    async with provider_client(monkeypatch, lambda body: httpx.Response(
        200, text=stream_body() if body['stream'] else json.dumps(raw)),
        stream_usage=enabled) as (client, _, requests):
        await request(client, mode)
    if enabled and 'stream' in mode:
        assert requests[0]['stream_options'] == {'include_usage': True}
    else:
        assert 'stream_options' not in requests[0]


@pytest.mark.parametrize('mode', ['stream', 'astream'])
@pytest.mark.parametrize('usage', [None, {}, 'invalid', {'prompt_tokens': 'bad'},
                                    {'prompt_tokens': -1}, {'prompt_tokens': True}])
async def test_missing_or_malformed_stream_usage_stays_unknown(monkeypatch, mode, usage):
    async with provider_client(monkeypatch, lambda body: httpx.Response(
        200, text=stream_body(usage=usage))) as (client, monitor, _):
        chunks = await request(client, mode)
    assert ''.join(chunk.message for chunk in chunks) == 'answer'
    assert chunks[-1].usage is None
    assert monitor.records() == []


@pytest.mark.parametrize('mode', ['stream', 'astream'])
@pytest.mark.parametrize('fault', ['length', 'missing_finish', 'invalid_choices', 'cleanup'])
async def test_known_usage_is_billable_even_when_stream_fails(monkeypatch, mode, fault):
    body = sse({'model': 'billed-model', 'choices': [], 'usage': USAGE})
    if fault == 'invalid_choices':
        # Usage must survive even when validation fails in the same frame.
        body = sse({'model': 'billed-model', 'choices': 'invalid', 'usage': USAGE})
    elif fault in ('length', 'cleanup'):
        body += sse({'choices': [{'delta': {}, 'finish_reason': 'length' if fault == 'length' else 'stop'}]})
    class BrokenCloseStream(httpx.SyncByteStream, httpx.AsyncByteStream):
        def __iter__(self):
            yield body.encode()
        async def __aiter__(self):
            yield body.encode()
        def close(self):
            raise httpx.ReadError('cleanup failed')
        async def aclose(self):
            raise httpx.ReadError('cleanup failed')
    async with provider_client(monkeypatch, lambda request: httpx.Response(
        200, stream=BrokenCloseStream()) if fault == 'cleanup' else httpx.Response(
        200, text=body)) as (client, monitor, _):
        with pytest.raises(LLMError) as caught:
            await request(client, mode)
    assert type(caught.value) is LLMError
    assert len(monitor.records()) == 1
    assert (monitor.records()[0].model, monitor.records()[0].total_tokens) == ('billed-model', 14)


@pytest.mark.parametrize('mode', ['stream', 'astream'])
async def test_cumulative_frames_use_last_valid_usage_not_sum_or_last_malformed(monkeypatch, mode):
    body = sse({'choices': [], 'usage': {'prompt_tokens': 11, 'completion_tokens': 1, 'total_tokens': 12}})
    body += sse({'choices': [], 'usage': USAGE})
    body += sse({'choices': [], 'usage': {'prompt_tokens': 'invalid'}})
    body += sse({'choices': [{'delta': {}, 'finish_reason': 'stop'}]})
    async with provider_client(monkeypatch, lambda request: httpx.Response(200, text=body)) as (client, monitor, _):
        chunks = await request(client, mode)
    assert len(monitor.records()) == 1 and monitor.records()[0].total_tokens == 14
    assert chunks[-1].usage == USAGE


@pytest.mark.parametrize('mode', ['invoke', 'ainvoke'])
async def test_success_status_error_envelope_is_typed_and_known_usage_recorded(monkeypatch, mode):
    raw = {'error': {'code': 'context_length_exceeded'}, 'usage': USAGE}
    async with provider_client(monkeypatch, lambda body: httpx.Response(200, json=raw)) as (client, monitor, _):
        with pytest.raises(LLMError) as caught:
            await request(client, mode)
    assert type(caught.value).__name__ == 'ContextWindowExceeded'
    assert len(monitor.records()) == 1 and monitor.records()[0].total_tokens == 14


@pytest.mark.parametrize('status,expected', [(401, LLMError), (429, RateLimitError), (500, ServerError)])
def test_sse_structured_status_takes_precedence_over_context_code(status, expected):
    error = {'code': 'context_length_exceeded', 'status': status}
    with pytest.raises(LLMError) as caught:
        parse_sse_data(json.dumps({'error': error}), ToolCallAccumulator())
    assert type(caught.value) is expected


@pytest.mark.parametrize('mode', MODES)
async def test_invalid_response_model_falls_back_to_effective_request_model(monkeypatch, mode):
    raw = {'model': 42, 'choices': [{'message': {'role': 'assistant', 'content': 'answer'},
                                   'finish_reason': 'stop'}], 'usage': USAGE}
    async with provider_client(monkeypatch, lambda body: httpx.Response(
        200, text=stream_body(model=42) if body['stream'] else json.dumps(raw))) as (client, monitor, _):
        await request(client, mode, model='request-model')
    assert monitor.records()[0].model == 'request-model'


@pytest.mark.parametrize('mode', ['stream', 'astream'])
async def test_later_request_missing_usage_does_not_reuse_previous_request(monkeypatch, mode):
    response_number = 0
    def response(body):
        nonlocal response_number
        response_number += 1
        return httpx.Response(200, text=stream_body(usage=USAGE if response_number == 1 else None,
                                                   model='first-model' if response_number == 1 else None))
    async with provider_client(monkeypatch, response) as (client, monitor, _):
        first = await request(client, mode)
        second = await request(client, mode, model='second-model')
    assert first[-1].usage == USAGE and first[-1].response_model == 'first-model'
    assert second[-1].usage is None and second[-1].response_model == 'second-model'
    assert len(monitor.records()) == 1


@pytest.mark.parametrize('mode', ['invoke', 'ainvoke'])
@pytest.mark.parametrize('usage', [None, {}, 'invalid', {'prompt_tokens': 'bad'},
                                    {'prompt_tokens': -1}, {'prompt_tokens': True}])
async def test_missing_or_malformed_nonstream_usage_does_not_create_billing(monkeypatch, mode, usage):
    raw = {'choices': [{'message': {'role': 'assistant', 'content': 'answer'}, 'finish_reason': 'stop'}],
           'usage': usage}
    async with provider_client(monkeypatch, lambda body: httpx.Response(200, json=raw)) as (client, monitor, _):
        result = await request(client, mode)
    assert result == raw
    assert monitor.records() == []


@pytest.mark.parametrize('mode', ['stream', 'astream'])
async def test_monitor_failure_does_not_replace_valid_stream(monkeypatch, mode):
    class BrokenMonitor:
        def record(self, *args, **kwargs):
            raise RuntimeError('monitor unavailable')
    async with provider_client(monkeypatch, lambda body: httpx.Response(200, text=stream_body())) as (client, _, _):
        client.usage_monitor = BrokenMonitor()
        chunks = await request(client, mode)
    assert ''.join(chunk.message for chunk in chunks) == 'answer'
    assert chunks[-1].usage == USAGE
    assert client.usage_error_count == 1
