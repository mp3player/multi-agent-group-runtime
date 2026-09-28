"""Complete-request budget checks independent of providers and sessions."""

import math

import pytest

from core.agent_runtime.context_management import (
    ContextCapacityUnknown, ContextManagementError, ModelBudget, TokenCounter, TokenEstimate,
)
from models import AI, Message, Reasoning, ToolCall, User


@pytest.mark.parametrize("input_limit", [None, 4000])
def test_unknown_capacity_blocks_even_when_input_limit_is_known(input_limit):
    with pytest.raises(ContextCapacityUnknown, match="context_window"):
        ModelBudget(input_limit=input_limit).input_budget(1024)


@pytest.mark.parametrize(
    ("capacity", "output", "extra", "expected"),
    [(8192, 1024, 0, 6348), (4000, 1000, 0, 2488), (4000, 1000, 128, 2360)],
)
def test_budget_deducts_output_and_larger_of_ratio_or_minimum_reserve(
    capacity, output, extra, expected,
):
    assert ModelBudget(context_window=capacity).input_budget(
        output, extra_reserve=extra,
    ) == expected


@pytest.mark.parametrize(("limit", "expected"), [(4000, 4000), (7000, 6348)])
def test_input_limit_only_reduces_available_capacity(limit, expected):
    assert ModelBudget(context_window=8192, input_limit=limit).input_budget(1024) == expected


def test_explicit_reserve_policy_supports_small_known_models():
    assert ModelBudget(
        context_window=100, safety_ratio=0, minimum_reserve=0,
    ).input_budget(20, extra_reserve=5) == 75


@pytest.mark.parametrize("field", ["context_window", "input_limit"])
@pytest.mark.parametrize("value", [0, -1, True, 4096.0, "4096"])
def test_capacity_settings_require_positive_integers(field, value):
    with pytest.raises(ValueError, match=field):
        ModelBudget(**{field: value})


@pytest.mark.parametrize("value", [-1, True, 512.0, "512"])
def test_minimum_reserve_requires_nonnegative_integer(value):
    with pytest.raises(ValueError, match="minimum_reserve"):
        ModelBudget(minimum_reserve=value)


@pytest.mark.parametrize("value", [-0.1, 1, math.nan, math.inf, -math.inf, True, "0.1"])
def test_safety_ratio_rejects_invalid_and_nonfinite_values(value):
    with pytest.raises(ValueError, match="safety_ratio"):
        ModelBudget(safety_ratio=value)


@pytest.mark.parametrize("value", [0, -1, True, 100.0, "100", None])
def test_output_budget_requires_positive_integer(value):
    with pytest.raises(ValueError, match="output_tokens"):
        ModelBudget(context_window=4096).input_budget(value)


@pytest.mark.parametrize("value", [-1, True, 1.0, "1"])
def test_extra_reserve_requires_nonnegative_integer(value):
    with pytest.raises(ValueError, match="extra_reserve"):
        ModelBudget(context_window=4096).input_budget(100, extra_reserve=value)


@pytest.mark.parametrize("output", [512, 513, 1024])
def test_nonpositive_input_budget_is_rejected_without_shrinking_output(output):
    with pytest.raises(ValueError, match="input budget"):
        ModelBudget(context_window=1024).input_budget(output)


def test_extra_reserve_cannot_exhaust_input_budget():
    with pytest.raises(ValueError, match="input budget"):
        ModelBudget(context_window=1024).input_budget(256, extra_reserve=256)


def test_estimate_counts_wire_json_and_framing_with_an_explicit_method():
    # 45 serialized input bytes, 16 request and 8 message framing units.
    estimate = TokenCounter().estimate([User("Hi")])
    assert estimate == TokenEstimate(69, "conservative_utf8_bytes")


def test_empty_input_still_counts_request_framing():
    assert TokenCounter().estimate([]).tokens == 31


def test_nonascii_content_is_counted_by_utf8_bytes():
    assert TokenCounter().estimate([User("\u4f60\u597d🙂")]).tokens == 77


def test_tool_schema_and_descriptions_are_counted_without_mutation():
    tools = [{"type": "function", "function": {"name": "read", "description": "\u8bfb🙂"}}]
    estimate = TokenCounter().estimate([User("Hi")], tools)
    # Wire JSON is 126 bytes, plus 16 request/8 message/8 tool framing units.
    assert estimate.tokens == 158
    assert tools == [{"type": "function", "function": {"name": "read", "description": "\u8bfb🙂"}}]


def test_empty_tool_list_has_no_schema_cost():
    assert TokenCounter().estimate([User("Hi")], []).tokens == 69


def test_tool_arguments_ids_and_results_are_included():
    result = Message("tool", "ok")
    result.tool_call_id = "call1"
    messages = [AI(tool_calls=[ToolCall("call1", "read", '{"path":"\u4e2d"}')]), result]
    # Exact provider projection has 210 bytes before 32 framing units.
    assert TokenCounter().estimate(messages).tokens == 242


def test_reasoning_omitted_by_provider_projection_is_not_counted():
    estimate = TokenCounter().estimate([
        Reasoning("untransmitted" * 100), AI("Hi", reasoning="private" * 100),
    ])
    assert estimate.tokens == 74


@pytest.mark.parametrize("tools", [[{"bad": {1, 2}}], [{"bad": math.nan}]])
def test_unserializable_input_raises_instead_of_returning_zero(tools):
    with pytest.raises(ContextManagementError, match="estimate"):
        TokenCounter().estimate([User("Hi")], tools)


@pytest.mark.parametrize(("text", "expected"), [("", 0), ("abcd", 4), ("a\u4e2d🙂b", 9)])
def test_text_counter_is_conservative_for_ascii_and_unicode(text, expected):
    assert TokenCounter().text_tokens(text) == expected


@pytest.mark.parametrize(
    ("budget", "expected"),
    [(0, ""), (1, "a"), (2, "a"), (3, "a"), (4, "a\u4e2d"),
     (7, "a\u4e2d"), (8, "a\u4e2d🙂"), (9, "a\u4e2d🙂b"), (100, "a\u4e2d🙂b")],
)
def test_truncation_preserves_unicode_codepoints_and_respects_budget(budget, expected):
    result = TokenCounter().truncate("a\u4e2d🙂b", budget)
    assert result == expected
    assert result.encode("utf-8").decode("utf-8") == expected


@pytest.mark.parametrize("value", [-1, True, 2.0, "2"])
def test_truncation_requires_nonnegative_integer_budget(value):
    with pytest.raises(ValueError, match="tokens"):
        TokenCounter().truncate("text", value)


def test_truncation_uses_injected_text_counter():
    class CharacterCounter(TokenCounter):
        def text_tokens(self, text):
            return len(text)

    counter = CharacterCounter()
    assert counter.truncate("a\u4e2d🙂b", 3) == "a\u4e2d🙂"
