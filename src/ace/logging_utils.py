"""Helpers for human-readable log callouts."""

from __future__ import annotations

from typing import Any

import structlog


def log_key_event(logger: structlog.BoundLogger, title: str, **fields: Any) -> None:
    """Log a key event that should stand out in terminal output."""
    logger.info("key_event", title=title, **fields)
