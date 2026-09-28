"""Detached observations and decisions at the automatic scheduling boundary."""

from dataclasses import dataclass

from group.records import Assignment, DispatchPlan, Message, Opportunity, Page, Snapshot


@dataclass(frozen=True)
class ResponseEvidence:
    opportunity_id: str
    message_id: str
    member_id: str
    assignment_id: str
    outcome: str
    error: str | None
    reply_id: str | None
    resolved_by: str | None = None
    resolution_reply_id: str | None = None

    @property
    def satisfied(self):
        return bool((self.resolved_by and self.resolution_reply_id)
                    or (self.reply_id and self.outcome in ('completed', 'tool_stop') and not self.error))


@dataclass(frozen=True)
class SchedulingView:
    snapshot: Snapshot
    pending: tuple[Opportunity, ...]
    messages: Page[Message]
    evidence: tuple[ResponseEvidence, ...]
    runs: tuple[Assignment, ...]
    queue_capacity: int


@dataclass(frozen=True)
class SchedulingDecision:
    plan: DispatchPlan | None = None
    reason: str = ''


@dataclass(frozen=True)
class DriveResult:
    status: str
    reason: str
    admitted_count: int
    missing_reply_ids: tuple[str, ...] = ()
    revision: int = 0
    blocked_assignment_ids: tuple[str, ...] = ()
