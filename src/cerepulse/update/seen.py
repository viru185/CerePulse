"""Remembers which version the user has already been shown release notes for.

Kept in a small file beside the cache rather than in the config, because it is app state
rather than a user preference — nobody should have to see it in Settings, and clearing the
config should not cause the notes to reappear.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from cerepulse import __about__ as about
from cerepulse.core import paths

FILENAME = "state.json"
KEY = "last_seen_version"


def state_file() -> Path:
    return paths.data_root() / FILENAME


def last_seen_version() -> str | None:
    """The version whose notes were last shown, or None on a first run."""
    target = state_file()
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("Could not read {}: {}", target, exc)
        return None
    value = data.get(KEY)
    return str(value) if value else None


def mark_seen(version: str | None = None) -> None:
    """Record that the notes for this version have been shown."""
    target = state_file()
    data: dict[str, object] = {}
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}

    data[KEY] = version or about.VERSION
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not record the seen version: {}", exc)


def should_show_whats_new(current: str | None = None) -> bool:
    """Whether the What's New dialog is due.

    A genuinely first run is deliberately excluded: someone installing for the first time
    does not need to be told what changed since a version they never had.
    """
    version = current or about.VERSION
    seen = last_seen_version()
    if seen is None:
        mark_seen(version)
        return False
    return seen != version
