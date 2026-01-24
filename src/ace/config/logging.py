"""Logging configuration using structlog."""

import logging
import os
import sys

import structlog


def configure_logging(debug: bool = False, log_format: str | None = None) -> None:
    """Configure structured logging with structlog."""
    log_level = logging.DEBUG if debug else logging.INFO
    format_value = (log_format or os.getenv("ACE_LOG_FORMAT", "console")).lower()

    shared_processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if format_value == "console":
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=False),
        ]
    else:
        processors = shared_processors + [
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Quiet noisy third-party HTTP logs
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a logger instance."""
    return structlog.get_logger(name)
