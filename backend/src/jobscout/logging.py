"""Structured JSON logging with structlog.

Privacy rule: CV text and posting bodies must never reach the logs. Callers log
lengths/hashes instead, and ``redact_processor`` drops any key on the deny-list as a
second line of defence.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

# Keys whose values must never be logged.
REDACTED_KEYS = frozenset(
    {
        "cv_text",
        "cv",
        "raw_text",
        "text",
        "description",
        "api_key",
        "authorization",
        "password",
        "profile",
        "content",
    }
)


def redact_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict.keys()):
        if key.lower() in REDACTED_KEYS:
            event_dict[key] = "[REDACTED]"
    return event_dict


def configure_logging(level: str = "INFO", json: bool = True) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=level.upper())
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        # Resolve sys.stderr at call time (works with test capture and reloads).
        logger_factory=lambda *_: structlog.PrintLogger(file=sys.stderr),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
