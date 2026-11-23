"""Small persistent facts about the app itself, in ``state.json`` beside the cache.

App state rather than user preferences: nobody should see these in Settings, and clearing
the config must not make the release notes reappear, lose the update history, or re-send
every notification already shown today.

Everything tolerates a missing or corrupt file by behaving as though it were empty. A broken
state file must never stop the app opening.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from cerepulse.core import paths

FILENAME = "state.json"


def state_file() -> Path:
    return paths.data_root() / FILENAME


def read() -> dict[str, object]:
    target = state_file()
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("Could not read {}: {}", target, exc)
        return {}
    return data if isinstance(data, dict) else {}


def write(data: dict[str, object]) -> None:
    target = state_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write app state: {}", exc)


def get(key: str) -> object:
    return read().get(key)


def put(key: str, value: object) -> None:
    """Set one key, keeping every other key the file holds."""
    data = read()
    data[key] = value
    write(data)
