"""Small persistent facts about updating.

Kept in a file beside the cache rather than in the config, because these are app state
rather than user preferences — nobody should have to see them in Settings, and clearing the
config should not make the release notes reappear or lose the update history.

Everything here tolerates a missing or corrupt file by behaving as though it were empty. A
broken state file must never stop the app opening.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger

from cerepulse import __about__ as about
from cerepulse.core import paths
from cerepulse.update.version import is_newer

FILENAME = "state.json"
KEY = "last_seen_version"
LAST_CHECK_KEY = "last_update_check"
HISTORY_KEY = "update_history"

#: Enough to answer "what changed recently, and did an update go wrong?" without the file
#: growing without limit.
HISTORY_LIMIT = 20


@dataclass(frozen=True, slots=True)
class UpdateEvent:
    """One thing that happened to this installation's version."""

    version: str
    at: datetime
    outcome: str  # installed | rolled-back | failed
    note: str = ""

    @property
    def succeeded(self) -> bool:
        return self.outcome == "installed"


def state_file() -> Path:
    return paths.data_root() / FILENAME


def _read() -> dict[str, object]:
    target = state_file()
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("Could not read {}: {}", target, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, object]) -> None:
    target = state_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write update state: {}", exc)


# --- release notes -----------------------------------------------------------------------


def last_seen_version() -> str | None:
    """The version whose notes were last shown, or None on a first run."""
    value = _read().get(KEY)
    return str(value) if value else None


def mark_seen(version: str | None = None) -> None:
    """Record that the notes for this version have been shown."""
    data = _read()
    data[KEY] = version or about.VERSION
    _write(data)


def should_show_whats_new(current: str | None = None) -> bool:
    """Whether the What's New dialog is due.

    Only on an *upgrade*. A first run is excluded — someone installing for the first time
    does not need telling what changed since a version they never had — and so is a
    downgrade, because a rollback is not an occasion to celebrate new features.
    """
    version = current or about.VERSION
    seen = last_seen_version()
    if seen is None:
        mark_seen(version)
        return False
    return is_newer(version, seen)


# --- check timing ------------------------------------------------------------------------


def last_checked() -> datetime | None:
    """When an update check last completed, for the About screen."""
    raw = _read().get(LAST_CHECK_KEY)
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def mark_checked(at: datetime | None = None) -> None:
    data = _read()
    data[LAST_CHECK_KEY] = (at or datetime.now()).isoformat()
    _write(data)


# --- history -----------------------------------------------------------------------------


def update_history() -> list[UpdateEvent]:
    """What has happened to this installation, newest first."""
    raw = _read().get(HISTORY_KEY)
    if not isinstance(raw, list):
        return []

    events: list[UpdateEvent] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            events.append(
                UpdateEvent(
                    version=str(item["version"]),
                    at=datetime.fromisoformat(str(item["at"])),
                    outcome=str(item.get("outcome", "installed")),
                    note=str(item.get("note", "")),
                )
            )
        except (KeyError, ValueError):
            continue
    return sorted(events, key=lambda event: event.at, reverse=True)


def record_update(version: str, outcome: str, note: str = "") -> None:
    """Append one event, keeping the most recent :data:`HISTORY_LIMIT`."""
    data = _read()
    entries = data.get(HISTORY_KEY)
    history = list(entries) if isinstance(entries, list) else []
    history.append(
        {
            "version": version,
            "at": datetime.now().isoformat(),
            "outcome": outcome,
            "note": note,
        }
    )
    data[HISTORY_KEY] = history[-HISTORY_LIMIT:]
    _write(data)
