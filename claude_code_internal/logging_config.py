"""Structured logging setup for claude_code_internal.

Usage::

    from claude_code_internal.logging_config import get_logger

    logger = get_logger(__name__)
    logger.info("Token refreshed successfully")
"""

from __future__ import annotations

import logging
import sys

_PACKAGE_LOGGER_NAME = "claude_code_internal"
_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DEFAULT_LEVEL = logging.INFO

_configured = False


def setup_logging(
    level: int = _DEFAULT_LEVEL,
    fmt: str = _DEFAULT_FORMAT,
) -> None:
    """Configure the root package logger (idempotent)."""
    global _configured  # noqa: PLW0603
    if _configured:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(fmt))
    root = logging.getLogger(_PACKAGE_LOGGER_NAME)
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the package namespace.

    Automatically calls :func:`setup_logging` on first use so callers
    never need to worry about handler configuration.
    """
    setup_logging()
    if not name.startswith(_PACKAGE_LOGGER_NAME):
        name = f"{_PACKAGE_LOGGER_NAME}.{name}"
    return logging.getLogger(name)
