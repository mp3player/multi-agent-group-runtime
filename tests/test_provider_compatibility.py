"""Explicit HTTP environment control and narrowly scoped provider compatibility."""
import json

import httpx
import pytest

from application.agent_builder import build_agent
from application.agent_config import AgentAppConfig
from core.llm import LLMClient
from core.llm_runtime.sse import ToolCallAccumulator, parse_sse_data
from tests.test_context_provider import provider_client, request, sse, USAGE


def terminal_frame(**overrides):
    choice = {'index': 0, 'delta': {'content': '<turn|>'},
              'finish_reason': 'stop', 'stop_reason': 106}
    choice.update(overrides)
    return {'model': 'google/gemma-4-31B-it',
            'system_fingerprint': 'vllm-0.26.0-tp4-6250f0a0',
            'choices': [choice]}


@pytest.mark.parametrize('mode', ['stream', 'astream'])
@pytest.mark.parametrize('profile,expected', [('standard', 'answer<turn|>'), ('vllm_gemma4', 'answer')])
async def test_provider_terminal_marker_is_filtered_only_when_opted_in(monkeypatch, mode, profile, expected):
    body = sse({'choices': [{'delta': {'content': 'answer'}, 'finish_reason': None}]})
    body += sse(terminal_frame()) + sse({'choices': [], 'usage': USAGE}) + 'data: [DONE]\n\n'
    async with provider_client(monkeypatch, lambda _: httpx.Response(200, text=body),
                               stream_compatibility=profile) as (client, monitor, _):
        chunks = await request(client, mode)
    assert ''.join(chunk.message for chunk in chunks) == expected
    assert chunks[-1].finish_reason == 'stop'
    assert chunks[-1].usage == USAGE
    assert len(monitor.records()) == 1


@pytest.mark.parametrize('override', [
    {'delta': {'content': 'literal <turn|>'}},
    {'delta': {'content': '<turn|>\n'}},
    {'finish_reason': None}, {'finish_reason': 'length'},
    {'stop_reason': None}, {'stop_reason': '106'}, {'stop_reason': 107},
    {'delta': {'content': '<turn|>', 'tool_calls': [{'index': 0, 'id': 'c',
       'function': {'name': 'echo', 'arguments': '{}'}}]}},
])
def test_legitimate_text_or_unrecognized_terminal_frames_are_preserved(override):
    frame = terminal_frame(**override)
    chunk = parse_sse_data(json.dumps(frame), ToolCallAccumulator(), stream_compatibility='vllm_gemma4')
    assert chunk.message == frame['choices'][0]['delta']['content']


@pytest.mark.parametrize('field,value', [('model', 'other-model'), ('model', None),
                                         ('system_fingerprint', 'other-server'), ('system_fingerprint', None)])
def test_compatibility_requires_matching_provider_and_model(field, value):
    frame = terminal_frame()
    frame[field] = value
    chunk = parse_sse_data(json.dumps(frame), ToolCallAccumulator(), stream_compatibility='vllm_gemma4')
    assert chunk.message == '<turn|>'


def test_terminal_marker_preserves_reasoning_and_finish_signal():
    acc = ToolCallAccumulator()
    frame = terminal_frame(delta={'content': '<turn|>', 'reasoning_content': 'kept'})
    chunk = parse_sse_data(json.dumps(frame), acc, stream_compatibility='vllm_gemma4')
    assert chunk.message == '' and chunk.reasoning == 'kept'
    assert acc.finish_reason == 'stop'
    acc.validate_complete()


@pytest.mark.parametrize('mode', ['invoke', 'ainvoke'])
async def test_nonstream_response_is_unchanged_by_stream_profile(monkeypatch, mode):
    raw = {'choices': [{'message': {'content': '<turn|>'}, 'finish_reason': 'stop'}]}
    async with provider_client(monkeypatch, lambda _: httpx.Response(200, json=raw),
                               stream_compatibility='vllm_gemma4') as (client, _, _):
        assert await request(client, mode) == raw


def test_provider_config_defaults_and_explicit_settings_reach_builder(tmp_path):
    default = AgentAppConfig.from_mapping({}).llm
    assert default.trust_env is True
    assert default.stream_compatibility == 'standard'
    config = AgentAppConfig.from_mapping({
        'BaseURL': 'http://fixture.invalid/v1', 'BaseKey': 'test-key',
        'MAS_LLM_TRUST_ENV': '0', 'MAS_LLM_STREAM_COMPATIBILITY': 'vllm_gemma4',
        'MAS_CONTEXT_ARCHIVE_DIR': str(tmp_path / 'archives'),
    })
    agent = build_agent(config)
    assert agent.llm.trust_env is False
    assert agent.llm.stream_compatibility == 'vllm_gemma4'


def test_environment_factory_captures_http_and_compatibility_settings(monkeypatch, tmp_path):
    values = {'BaseURL': 'http://fixture.invalid/v1', 'BaseKey': 'test-key',
              'MAS_LLM_TRUST_ENV': '0', 'MAS_LLM_STREAM_COMPATIBILITY': 'vllm_gemma4'}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    client = LLMClient.from_env(tmp_path / 'absent.env')
    monkeypatch.setenv('MAS_LLM_TRUST_ENV', '1')
    assert client.trust_env is False
    assert client.stream_compatibility == 'vllm_gemma4'


@pytest.mark.parametrize('factory', [
    lambda: AgentAppConfig.from_mapping({'MAS_LLM_STREAM_COMPATIBILITY': 'typo'}),
    lambda: LLMClient('http://fixture.invalid/v1', 'test-key', stream_compatibility='typo'),
])
def test_invalid_compatibility_profile_is_rejected(factory):
    with pytest.raises(ValueError, match='stream.*compatibility|STREAM_COMPATIBILITY'):
        factory()


@pytest.mark.parametrize('trust_env', [False, True])
@pytest.mark.parametrize('mode', ['invoke', 'stream'])
async def test_sync_http_calls_receive_explicit_environment_setting(monkeypatch, mode, trust_env):
    captured = []
    raw = {'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}]}
    body = sse({'choices': [{'delta': {'content': 'ok'}, 'finish_reason': 'stop'}]})
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(
            200, text=body if json.loads(r.content)['stream'] else json.dumps(raw))), trust_env=False) as wire:
        def post(url, **kwargs):
            captured.append(kwargs.pop('trust_env'))
            return wire.post(url, **kwargs)
        def stream(method, url, **kwargs):
            captured.append(kwargs.pop('trust_env'))
            return wire.stream(method, url, **kwargs)
        monkeypatch.setattr(httpx, 'post', post)
        monkeypatch.setattr(httpx, 'stream', stream)
        await request(LLMClient('http://fixture.invalid/v1', 'test-key', trust_env=trust_env), mode)
    assert captured == [trust_env]


async def test_async_http_opt_out_ignores_invalid_ambient_proxy(monkeypatch):
    monkeypatch.setenv('ALL_PROXY', 'socks://127.0.0.1:7890/')
    monkeypatch.setenv('all_proxy', 'socks://127.0.0.1:7890/')
    client = LLMClient('http://fixture.invalid/v1', 'test-key', trust_env=False)
    try:
        first = await client._get_async_client()
        assert await client._get_async_client() is first
        assert not first.is_closed
    finally:
        await client.aclose()
    assert first.is_closed
    # The default remains HTTPX's existing environment behavior.
    default = LLMClient('http://fixture.invalid/v1', 'test-key')
    with pytest.raises(ValueError, match='proxy'):
        await default._get_async_client()


@pytest.mark.parametrize('mode', ['stream', 'astream'])
@pytest.mark.parametrize('reason', ['length', None])
async def test_compatibility_never_converts_incomplete_output_to_success(monkeypatch, mode, reason):
    from core.llm_runtime.errors import LLMError
    body = sse(terminal_frame(finish_reason=reason)) + 'data: [DONE]\n\n'
    async with provider_client(monkeypatch, lambda _: httpx.Response(200, text=body),
                               stream_compatibility='vllm_gemma4') as (client, _, _):
        with pytest.raises(LLMError):
            await request(client, mode)


@pytest.mark.parametrize('mode', ['stream', 'astream'])
async def test_compatibility_preserves_fragmented_tool_calls(monkeypatch, mode):
    frames = [terminal_frame(delta={'tool_calls': [{'index': 0, 'id': 'c',
              'function': {'name': 'echo', 'arguments': '{"text":'}}]},
              finish_reason=None, stop_reason=None),
              terminal_frame(delta={'tool_calls': [{'index': 0,
              'function': {'arguments': '"<turn|>"}'}}]},
              finish_reason='tool_calls', stop_reason=None)]
    body = ''.join(sse(frame) for frame in frames) + 'data: [DONE]\n\n'
    async with provider_client(monkeypatch, lambda _: httpx.Response(200, text=body),
                               stream_compatibility='vllm_gemma4') as (client, _, _):
        chunks = await request(client, mode)
    assert chunks[-1].finish_reason == 'tool_calls'
    calls = chunks[-1].tool_calls
    assert len(calls) == 1
    assert (calls[0].id, calls[0].name, calls[0].arguments) == ('c', 'echo', '{"text":"<turn|>"}')
