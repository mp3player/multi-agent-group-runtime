"""Pure decision interfaces; strategy selection always remains explicit."""

from typing import Protocol

from group.records import DispatchPlan, Snapshot
from group.profiles import CollaborationProfile
from group.scheduling import SchedulingDecision, SchedulingView


class CollaborationStrategy(Protocol):
    """Bundle communication choices with a detached, synchronous decision rule.

    Implementations must return promptly. Runtime admission validates their
    proposals; this interface is a trusted extension point, not a sandbox.
    """

    name: str
    version: str
    profile: CollaborationProfile

    def decide(self, view: SchedulingView) -> SchedulingDecision: ...


class SchedulingPolicy(Protocol):
    """Legacy manual proposal interface, not the automatic driver interface.

    JSON policy state lives in Snapshot and is committed with a valid plan.
    Applications compose decision functions here and explicitly commit results.
    Waiting uses runtime.wait_for_change(snapshot.revision); there is no loop
    in this protocol that could implicitly wake or choose a member.
    """

    def decide(self, snapshot: Snapshot) -> DispatchPlan: ...
