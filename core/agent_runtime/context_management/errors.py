"""Errors that preserve the current context when preparation cannot proceed."""


class ContextManagementError(RuntimeError):
    """Context preparation failed without permission to discard messages."""


class ContextCapacityUnknown(ContextManagementError):
    """No explicit or trusted model context capacity is available."""


class InputTooLarge(ContextManagementError):
    """Required input cannot fit the configured model request budget."""


class CompactionError(ContextManagementError):
    """A complete, validated context compaction could not be produced."""


__all__ = [
    "ContextManagementError",
    "ContextCapacityUnknown",
    "InputTooLarge",
    "CompactionError",
]
