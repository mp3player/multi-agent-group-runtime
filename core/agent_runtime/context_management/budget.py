"""Pure budgets and conservative estimates for complete model inputs.

The default counter charges one unit per UTF-8 byte of serialized input,
including JSON structure, and adds request, message and tool framing. This
deliberately errs high for common tokenizers; it is not exact tokenization or
a guarantee about an arbitrary provider's private prompt serialization.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from core.agent_runtime.context_management.errors import (
    ContextCapacityUnknown,
    ContextManagementError,
)
from core.llm_runtime.payload import to_dict_list
from models import Message


def _integer(name: str, value: int, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "positive" if minimum == 1 else "nonnegative"
        raise ValueError(f"{name} must be a {qualifier} integer")


@dataclass(frozen=True)
class ModelBudget:
    """Shared input/output capacity supplied by configuration or trusted metadata.

    An independent input limit only further restricts this capacity. It cannot
    substitute for an unknown shared context window. Construction without a
    window is permitted so a session can remain available for inspection;
    attempting inference through ``input_budget`` then fails explicitly.
    """

    context_window: int | None = None
    input_limit: int | None = None
    safety_ratio: float = 0.10
    minimum_reserve: int = 512

    def __post_init__(self) -> None:
        if self.context_window is not None:
            _integer("context_window", self.context_window, minimum=1)
        if self.input_limit is not None:
            _integer("input_limit", self.input_limit, minimum=1)
        _integer("minimum_reserve", self.minimum_reserve, minimum=0)
        if (
            isinstance(self.safety_ratio, bool)
            or not isinstance(self.safety_ratio, (int, float))
            or not 0 <= self.safety_ratio < 1
            or not math.isfinite(self.safety_ratio)
        ):
            raise ValueError("safety_ratio must be a finite number in [0, 1)")

    def input_budget(self, output_tokens: int, *, extra_reserve: int = 0) -> int:
        """Return usable input capacity without changing the output request."""
        _integer("output_tokens", output_tokens, minimum=1)
        _integer("extra_reserve", extra_reserve, minimum=0)
        if self.context_window is None:
            raise ContextCapacityUnknown(
                "Model context capacity is unknown; configure context_window "
                "or supply trusted model capacity metadata before inference."
            )
        reserve = max(
            self.minimum_reserve, math.ceil(self.context_window * self.safety_ratio),
        ) + extra_reserve
        available = self.context_window - output_tokens - reserve
        if self.input_limit is not None:
            available = min(self.input_limit, available)
        if available <= 0:
            raise ValueError(
                "Model input budget must be positive: "
                f"context_window={self.context_window}, output_tokens={output_tokens}, "
                f"reserve={reserve}, input_limit={self.input_limit}. "
                "Increase context capacity or explicitly reduce output/reserve settings."
            )
        return available


@dataclass(frozen=True)
class TokenEstimate:
    """An input estimate annotated with the counting method used."""

    tokens: int
    method: str


class TokenCounter:
    """Estimate the exact provider-visible message projection and tool schemas.

    Framing contributes 16 units per request, 8 per visible message and 8 per
    tool schema, in addition to all serialized UTF-8 input bytes. The reserve
    in ``ModelBudget`` remains necessary for estimation uncertainty.

    Inject a subclass overriding ``estimate`` and ``text_tokens`` to use a
    model-specific tokenizer. ``truncate`` consults ``text_tokens`` and keeps
    a fitting Unicode prefix; subclasses may override it for tighter packing
    when their tokenizer's prefix counts are not monotonic.
    """

    def estimate(
        self, messages: list[Message], tools: list[dict] | None = None,
    ) -> TokenEstimate:
        """Count serialized wire input, excluding untransmitted reasoning."""
        try:
            wire_messages = to_dict_list(messages)
            payload = {"messages": wire_messages}
            if tools:
                payload["tools"] = tools
            serialized = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
            )
            size = self.text_tokens(serialized)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ContextManagementError(
                "Cannot estimate model input; its wire payload must be valid UTF-8 JSON."
            ) from exc
        framing = 16 + 8 * len(wire_messages) + 8 * len(tools or [])
        return TokenEstimate(size + framing, "conservative_utf8_bytes")

    def text_tokens(self, text: str) -> int:
        """Return the conservative UTF-8 byte cost of unframed text."""
        return len(text.encode("utf-8"))

    def truncate(self, text: str, tokens: int) -> str:
        """Return a fitting prefix without splitting a Unicode code point."""
        _integer("tokens", tokens, minimum=0)
        if self.text_tokens(text) <= tokens:
            return text
        lower, upper = 0, len(text)
        while lower < upper:
            midpoint = (lower + upper + 1) // 2
            if self.text_tokens(text[:midpoint]) <= tokens:
                lower = midpoint
            else:
                upper = midpoint - 1
        return text[:lower]


__all__ = ["ModelBudget", "TokenEstimate", "TokenCounter"]
