"""LLM usage monitoring helpers."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Callable


@dataclass(slots=True)
class UsageRecord:
    """One LLM response usage snapshot."""

    label: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0


class UsageMonitor:
    """Collect usage records from LLMClient calls."""

    def __init__(self, on_record: Callable[[UsageRecord], None] | None = None) -> None:
        self._records: list[UsageRecord] = []
        self._lock = threading.Lock()
        self._on_record = on_record

    def record(self, label: str, response: dict[str, Any], *, model: str = "") -> None:
        """Record usage fields from an OpenAI-compatible response."""
        usage = response.get("usage") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        record = UsageRecord(
            label=label,
            model=str(response.get("model") or model or ""),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            reasoning_tokens=int(completion_details.get("reasoning_tokens") or 0),
            cached_tokens=int(prompt_details.get("cached_tokens") or 0),
            cache_hit_tokens=int(usage.get("prompt_cache_hit_tokens") or 0),
            cache_miss_tokens=int(usage.get("prompt_cache_miss_tokens") or 0),
        )
        with self._lock:
            self._records.append(record)
        if self._on_record is not None:
            try:
                self._on_record(record)
            except Exception:
                pass

    def records(self) -> list[UsageRecord]:
        """Return a snapshot of all records."""
        with self._lock:
            return list(self._records)

    def clear(self) -> None:
        """Clear all collected records."""
        with self._lock:
            self._records.clear()

    def summary(self) -> dict[str, UsageRecord]:
        """Aggregate usage by label."""
        totals: dict[str, UsageRecord] = {}
        with self._lock:
            records = list(self._records)
        for record in records:
            current = totals.get(record.label)
            if current is None:
                totals[record.label] = UsageRecord(label=record.label, model=record.model)
                current = totals[record.label]
            current.prompt_tokens += record.prompt_tokens
            current.completion_tokens += record.completion_tokens
            current.total_tokens += record.total_tokens
            current.reasoning_tokens += record.reasoning_tokens
            current.cached_tokens += record.cached_tokens
            current.cache_hit_tokens += record.cache_hit_tokens
            current.cache_miss_tokens += record.cache_miss_tokens
        return totals
