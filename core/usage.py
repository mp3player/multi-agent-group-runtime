"""LLM usage monitoring helpers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
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

    def __init__(self, on_record: Callable[[UsageRecord], None] | None = None,
                 *, max_records: int = 1000) -> None:
        if type(max_records) is not int or max_records < 0:
            raise ValueError('max_records must be a non-negative integer')
        self._records: deque[UsageRecord] = deque(maxlen=max_records)
        self._totals: dict[str, UsageRecord] = {}
        self._lock = threading.Lock()
        self._on_record = on_record

    def record(self, label: str, response: dict[str, Any], *, model: str = "") -> None:
        """Record usage fields from an OpenAI-compatible response."""
        usage = response.get("usage")
        if not usage:
            return
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
            current = self._totals.setdefault(label, UsageRecord(label=label, model=record.model))
            current.prompt_tokens += record.prompt_tokens
            current.completion_tokens += record.completion_tokens
            current.total_tokens += record.total_tokens
            current.reasoning_tokens += record.reasoning_tokens
            current.cached_tokens += record.cached_tokens
            current.cache_hit_tokens += record.cache_hit_tokens
            current.cache_miss_tokens += record.cache_miss_tokens
        if self._on_record is not None:
            try:
                self._on_record(replace(record))
            except Exception:
                pass

    def records(self) -> list[UsageRecord]:
        """Return detached snapshots of the retained recent records."""
        with self._lock:
            return [replace(record) for record in self._records]

    def clear(self) -> None:
        """Clear all collected records."""
        with self._lock:
            self._records.clear()
            self._totals.clear()

    def summary(self) -> dict[str, UsageRecord]:
        """Return lifetime totals by label, independent of recent retention."""
        with self._lock:
            return {label: replace(record) for label, record in self._totals.items()}
