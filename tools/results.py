"""Typed tool failures that retain the builtin tools' text return interface."""


class ToolFailure(str):
    """A failed operation, distinct from successful output containing error text."""
