"""Detached values crossing collaboration and scheduling boundaries."""

from __future__ import annotations

from dataclasses import dataclass, fields
import math
from typing import Generic, TypeVar

T = TypeVar('T')


@dataclass(frozen=True)
class GroupLimits:
    max_members: int = 32
    max_active: int = 4
    max_queued: int = 64
    max_pending: int = 1024
    message_bytes: int = 16384
    input_bytes: int = 65536
    policy_state_bytes: int = 16384
    page_size: int = 20
    max_page_size: int = 100
    queue_jobs: int = 64
    max_runs: int = 100
    invocation_timeout: float = 300.0

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == 'invocation_timeout':
                if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                    raise ValueError('invocation_timeout must be finite and positive')
                continue
            if type(value) is not int or value < 1:
                raise ValueError(f'{field.name} must be a positive integer')
        if self.page_size > self.max_page_size:
            raise ValueError('page_size cannot exceed max_page_size')


@dataclass(frozen=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    next_cursor: int
    high_water: int
    exhausted: bool


@dataclass(frozen=True)
class Message:
    sequence: int
    id: str
    invocation_id: str
    sender: str
    run_id: str | None
    content: str
    recipients: tuple[str, ...]
    reply_to: str | None


@dataclass(frozen=True)
class Opportunity:
    sequence: int
    id: str
    invocation_id: str
    message_id: str
    target: str | None
    state: str
    assignment_id: str | None
    outcome: str | None


@dataclass(frozen=True)
class Assignment:
    sequence: int
    id: str
    invocation_id: str
    member_id: str
    instruction: str
    opportunity_ids: tuple[str, ...]
    origin_key: str | None
    state: str
    outcome: str | None
    error: str | None


@dataclass(frozen=True)
class Receipt:
    invocation_id: str
    operation_key: str
    message_id: str
    opportunity_ids: tuple[str, ...]
    revision: int
    dispatch_allowed: bool


@dataclass(frozen=True)
class MemberView:
    id: str
    state: str
    assignment_id: str | None = None


@dataclass(frozen=True)
class Snapshot:
    invocation_id: str
    revision: int
    state: str
    policy_state: str
    members: tuple[MemberView, ...]
    pending_count: int
    queued_count: int
    active_count: int
    opportunities: Page[Opportunity]
    assignments: Page[Assignment]
    admitted_count: int = 0
    deadline_at: float | None = None
    max_runs: int = 0
    reason: str | None = None
    blocked_count: int = 0


@dataclass(frozen=True)
class RunProposal:
    member_id: str
    instruction: str
    opportunity_ids: tuple[str, ...] = ()
    origin_key: str | None = None
    required_source_ids: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'opportunity_ids', tuple(self.opportunity_ids))
        object.__setattr__(self, 'required_source_ids', tuple(self.required_source_ids))


@dataclass(frozen=True)
class Disposition:
    opportunity_id: str
    outcome: str


@dataclass(frozen=True)
class DispatchPlan:
    invocation_id: str
    revision: int
    runs: tuple[RunProposal, ...] = ()
    dispositions: tuple[Disposition, ...] = ()
    policy_state: str | None = None
    observed_through: int | None = None

    def __post_init__(self):
        object.__setattr__(self, 'runs', tuple(self.runs))
        object.__setattr__(self, 'dispositions', tuple(self.dispositions))
