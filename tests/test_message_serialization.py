"""Legacy message files preserve outcomes and complete streaming metadata."""

import json

import pytest

from core.session import Session
from core.session_codec import _decode, _encode, message_digest
from core.session_store import JsonSessionStore
from models import AI, Chunk, JsonMessageSerde, Message, Reasoning, System, ToolCall, User


@pytest.mark.parametrize("tool_success,ends_run", [(True, False), (False, True), (True, True)])
def test_legacy_message_round_trip_preserves_tool_outcome(tool_success, ends_run):
    message = Message("tool", "operation complete")
    message.tool_call_id = "work-1"
    message.tool_success = tool_success
    message.ends_run = ends_run

    serde = JsonMessageSerde()
    restored = serde.load(json.loads(json.dumps(serde.save(message))))

    assert type(restored) is Message
    assert restored.role == "tool"
    assert restored.message == "operation complete"
    assert restored.tool_call_id == "work-1"
    assert restored.tool_success is tool_success
    assert restored.ends_run is ends_run


def test_legacy_file_round_trip_preserves_chunk_usage_and_model(tmp_path):
    chunk = Chunk(
        message="done",
        reasoning="checked",
        tool_calls=[ToolCall("work-1", "work", {"text": "résumé"})],
        finish_reason="tool_calls",
        usage={"prompt_tokens": 12, "completion_tokens": 7, "details": {"cached_tokens": 3}},
        response_model="reported-model",
    )
    serde = JsonMessageSerde()
    path = tmp_path / "messages.json"

    serde.save_to_file([chunk], path)
    restored, = serde.load_from_file(path)

    assert type(restored) is Chunk
    assert restored.message == "done"
    assert restored.reasoning == "checked"
    assert restored.finish_reason == "tool_calls"
    assert restored.tool_calls[0].arguments == '{"text": "résumé"}'
    assert restored.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 7,
        "details": {"cached_tokens": 3},
    }
    assert restored.response_model == "reported-model"


def test_legacy_file_without_new_fields_keeps_historical_defaults(tmp_path):
    path = tmp_path / "old-messages.json"
    path.write_text(json.dumps([
        {"type": "System", "role": "system", "message": "rules"},
        {"type": "User", "role": "user", "message": "task"},
        {"type": "Reasoning", "role": "reasoning", "message": "check"},
        {"type": "AI", "role": "assistant", "message": "done"},
        {"type": "Chunk", "role": "assistant", "message": "delta"},
        {"type": "ToolCall", "role": "assistant", "id": "work-1", "name": "work"},
        {"type": "Message", "role": "tool", "message": "ok", "tool_call_id": "work-1"},
    ]), encoding="utf-8")

    messages = JsonMessageSerde().load_from_file(path)

    assert [type(message) for message in messages] == [
        System, User, Reasoning, AI, Chunk, ToolCall, Message,
    ]
    assert [message.message for message in messages] == [
        "rules", "task", "check", "done", "delta", "", "ok",
    ]
    assert messages[3].reasoning == messages[4].reasoning == ""
    assert messages[3].tool_calls is messages[4].tool_calls is None
    assert messages[4].finish_reason is None
    assert messages[4].usage is None
    assert messages[4].response_model is None
    assert messages[5].arguments == ""
    assert messages[6].tool_call_id == "work-1"
    assert messages[6].tool_success is False
    assert messages[6].ends_run is False


@pytest.mark.parametrize("message_type", [AI, Chunk, ToolCall])
@pytest.mark.parametrize("arguments,expected", [
    ({"text": "résumé"}, '{"text": "résumé"}'),
    (' {"text":"résumé"} ', ' {"text":"résumé"} '),
])
def test_legacy_tool_arguments_keep_text_format(message_type, arguments, expected):
    call = ToolCall("work-1", "work", arguments)
    message = call if message_type is ToolCall else message_type("done", tool_calls=[call])
    serde = JsonMessageSerde()

    encoded = serde.save(message)
    restored = serde.load(encoded)

    assert "content" not in encoded
    if message_type is not ToolCall:
        assert encoded["message"] == "done"
    restored_call = restored if message_type is ToolCall else restored.tool_calls[0]
    assert (restored_call.id, restored_call.name, restored_call.arguments) == (
        "work-1", "work", expected,
    )


def test_strict_message_encoding_retains_canonical_fields_and_argument_types():
    message = AI("résumé", "check", [
        ToolCall("dict-1", "work", {"text": "café", "values": [1, False, None]}),
        ToolCall("text-1", "work", ' {"n": 1} '),
    ])
    expected = {
        "type": "AI", "role": "assistant", "content": "résumé", "reasoning": "check",
        "tool_calls": [
            {"id": "dict-1", "name": "work", "arguments": {"text": "café", "values": [1, False, None]}},
            {"id": "text-1", "name": "work", "arguments": ' {"n": 1} '},
        ],
    }

    encoded = _encode(message)
    restored = _decode(encoded)

    assert encoded == expected
    assert message_digest(encoded) == "6f4ca399eb07de3d109f4423eb412d8d7f378c882eb7ae2957d0f3e760e061ce"
    assert restored.tool_calls[0].arguments == {"text": "café", "values": [1, False, None]}
    assert restored.tool_calls[1].arguments == ' {"n": 1} '
    message.tool_calls[0].arguments["values"].append("later mutation")
    assert encoded == expected


def test_strict_tool_outcome_metadata_is_separate_from_legacy_message_fields():
    message = Message("tool", "done")
    message.tool_call_id = "work-1"
    message.tool_success = message.ends_run = True
    expected = {
        "type": "Message", "role": "tool", "content": "done", "tool_call_id": "work-1",
        "metadata": {"tool_success": True, "ends_run": True},
    }

    assert _encode(message) == expected
    restored = _decode(expected)
    assert restored.tool_success is True
    assert restored.ends_run is True
    expected.pop("metadata")
    assert _encode(message, legacy=True) == expected
    old = _decode(expected, legacy=True)
    assert old.tool_success is False
    assert old.ends_run is False


@pytest.mark.parametrize("change", [
    lambda data: data.update(message="legacy content"),
    lambda data: data["tool_calls"][0].update(extra=True),
    lambda data: data["tool_calls"][0].pop("arguments"),
    lambda data: data["tool_calls"][0].update(id=""),
    lambda data: data["tool_calls"][0].update(arguments=[1]),
    lambda data: data["tool_calls"][0].update(arguments={"bad": float("nan")}),
])
def test_strict_decode_still_rejects_noncanonical_tool_calls(change):
    data = {
        "type": "AI", "role": "assistant", "content": "", "reasoning": "",
        "tool_calls": [{"id": "work-1", "name": "work", "arguments": {}}],
    }
    change(data)
    with pytest.raises(ValueError):
        _decode(data)


def test_snapshot_save_still_rejects_chunks(tmp_path):
    session = Session()
    session.add(Chunk("stream delta", usage={"total_tokens": 3}, response_model="model"))

    with pytest.raises(ValueError, match="invalid snapshot message type or role"):
        JsonSessionStore().save(session, tmp_path / "session.json")
    assert not list(tmp_path.iterdir())
