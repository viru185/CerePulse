"""Structured logging with mandatory secret redaction.

Chapter 14 section 8 forbids logging passwords, auth tokens and session identifiers. That
is enforced here rather than left to call sites: every record passes through
:func:`redact` before it reaches a sink, so a careless ``logger.debug(payload)`` cannot
leak a session cookie.

Redaction also solves a practical problem — ``__VIEWSTATE`` alone is ~3 KB per request and
would otherwise dominate the log file.
"""

from __future__ import annotations

import re
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from cerepulse.core import paths

if TYPE_CHECKING:  # `Record` exists only in loguru's type stubs, not at runtime.
    from loguru import Record

# Form fields and cookies whose values must never reach a log sink.
_SENSITIVE_KEYS = (
    "__VIEWSTATE",
    "__VIEWSTATEGENERATOR",
    "__EVENTVALIDATION",
    "txtPassword",
    "txtHdnPass",
    "hEnSa",
    "password",
    "pwd",
    "ASP.NET_SessionId",
    ".ASPXFORMSAUTH",
)

_PLACEHOLDER = "<redacted>"

# key=value  (form-encoded / query string / cookie pair)
_KV_PATTERNS = [
    (re.compile(rf"({re.escape(key)}\s*=\s*)[^&;\s]+", re.IGNORECASE), rf"\1{_PLACEHOLDER}")
    for key in _SENSITIVE_KEYS
]

# "key": "value"  (JSON / dict repr, with either quote style)
_JSON_PATTERNS = [
    (
        re.compile(rf"(['\"]{re.escape(key)}['\"]\s*:\s*)['\"][^'\"]*['\"]", re.IGNORECASE),
        rf"\1'{_PLACEHOLDER}'",
    )
    for key in _SENSITIVE_KEYS
]

# Whole-header redaction — a Cookie header may carry several secrets at once.
_HEADER_PATTERN = re.compile(r"^(\s*(?:set-)?cookie\s*:\s*).*$", re.IGNORECASE | re.MULTILINE)


def redact(text: str) -> str:
    """Strip secret values out of ``text``, preserving the surrounding structure."""
    for pattern, replacement in _KV_PATTERNS:
        text = pattern.sub(replacement, text)
    for pattern, replacement in _JSON_PATTERNS:
        text = pattern.sub(replacement, text)
    return _HEADER_PATTERN.sub(rf"\1{_PLACEHOLDER}", text)


def _patcher(record: Record) -> None:
    """Redact the rendered message and any string values bound via ``extra``."""
    record["message"] = redact(record["message"])
    for key, value in record["extra"].items():
        if isinstance(value, str):
            record["extra"][key] = redact(value)


_CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss.SSS}</green> "
    "<level>{level: <8}</level> "
    "<cyan>{extra[correlation_id]}</cyan> "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>"
)

_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[correlation_id]} | "
    "{name}:{function}:{line} - {message}"
)


def configure_logging(
    *,
    level: str = "INFO",
    log_dir: Path | None = None,
    console: bool = True,
) -> None:
    """Install console and rotating-file sinks. Safe to call once at startup."""
    logger.remove()
    logger.configure(extra={"correlation_id": "-"}, patcher=_patcher)

    # A windowed PyInstaller build has no console, and Python sets sys.stderr to None
    # rather than to a null stream. Handing that to loguru raises, which crashed the app
    # before it could open a window. It only reproduces when launched from Explorer or the
    # Start Menu: starting the same executable from a terminal inherits usable handles.
    if console and sys.stderr is not None:
        logger.add(sys.stderr, level=level, format=_CONSOLE_FORMAT, backtrace=False)

    directory = log_dir or paths.logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    logger.add(
        directory / "cerepulse_{time:YYYY-MM-DD}.log",
        level=level,
        format=_FILE_FORMAT,
        rotation="10 MB",
        retention="30 days",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,  # never expand local variables — they may hold credentials
    )


@contextmanager
def workflow(name: str) -> Iterator[str]:
    """Bind a correlation ID for one logical workflow (Chapter 10 section 5).

    >>> with workflow("attendance-refresh"):
    ...     logger.info("started")
    """
    correlation_id = f"{name}:{uuid.uuid4().hex[:8]}"
    with logger.contextualize(correlation_id=correlation_id):
        yield correlation_id
