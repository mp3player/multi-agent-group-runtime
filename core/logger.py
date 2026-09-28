"""Explicit console logging setup for the CLI; imports have no side effects."""

import logging


def setup_logging(level: int | str = logging.INFO) -> None:
    """Set the CLI log level, preserving any host-installed handlers."""
    if isinstance(level, str):
        level = level.upper()
    root = logging.getLogger()
    root.setLevel(level)
    logging.basicConfig(
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

def get_logger(name: str) -> logging.Logger:
    """Compatibility alias for logging.getLogger."""
    return logging.getLogger(name)
