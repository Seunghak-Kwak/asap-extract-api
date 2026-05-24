import logging
import sys

import structlog

from app.config import settings

_configured = False


def configure() -> None:
    global _configured
    if _configured:
        return
    level = getattr(logging, settings().log_level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )
    _configured = True


def log() -> structlog.stdlib.BoundLogger:
    return structlog.get_logger()
