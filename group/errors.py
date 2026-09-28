"""Explicit control-plane rejections; none imply an external effect was undone."""


class GroupError(RuntimeError):
    """Base class for rejected Group operations."""


class CapacityError(GroupError):
    """Admission capacity was exhausted before accepting this operation."""


class QueueCapacityError(CapacityError):
    """Transient command/store saturation; the rejected operation was not admitted."""


class InvocationLimitError(CapacityError):
    """A durable invocation ceiling refused further work before acceptance."""

    def __init__(self, reason):
        if reason not in ('limited', 'timeout'):
            raise ValueError('Unknown invocation limit reason')
        self.reason = reason
        super().__init__(f'Invocation {reason}; no additional work was accepted')


class ConflictError(GroupError):
    """An identity or operation key was reused with different contents."""


class StalePlanError(ConflictError):
    """The plan's snapshot revision is no longer authoritative."""


class LifecycleError(GroupError):
    """The invocation or resource is not in an admissible state."""


class StoreBusyError(GroupError):
    """Another local owner holds this store."""


class RecoveryRequiredError(GroupError):
    """Unfinished durable state needs reconciliation before reuse."""


class StoreFailedError(GroupError):
    """Persistence is unavailable; new work must not launch."""
