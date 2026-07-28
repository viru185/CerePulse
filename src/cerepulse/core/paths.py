"""Filesystem layout resolution.

Resolution order for the data root:

1. ``CEREPULSE_DATA_DIR`` environment variable — used by tests and dev runs so we never
   touch the real user profile.
2. **Portable mode** — a ``portable.marker`` file sitting beside the executable. The data
   root then becomes ``<exe dir>/Data``, which is what makes the portable zip genuinely
   self-contained on a USB stick.
3. ``%LOCALAPPDATA%\\CerePulse`` — the normal installed-app location.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

_ENV_DATA_DIR = "CEREPULSE_DATA_DIR"
_APP_DIR_NAME = "CerePulse"
_PORTABLE_MARKER = "portable.marker"


def install_dir() -> Path:
    """Directory the running executable / source tree lives in.

    Frozen (PyInstaller): the folder containing the executable. Source: the repo root
    (``src/cerepulse/core/paths.py`` -> three levels up).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def is_portable() -> bool:
    """True when a ``portable.marker`` sits beside the executable."""
    return (install_dir() / _PORTABLE_MARKER).exists()


@lru_cache(maxsize=1)
def data_root() -> Path:
    """Return the writable data root. See module docstring for resolution order."""
    override = os.environ.get(_ENV_DATA_DIR)
    if override:
        return Path(override)
    if is_portable():
        return install_dir() / "Data"
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / _APP_DIR_NAME


def config_dir() -> Path:
    return data_root() / "config"


def logs_dir() -> Path:
    return data_root() / "logs"


def cache_dir() -> Path:
    return data_root() / "cache"


def exports_dir() -> Path:
    return data_root() / "exports"


def config_file() -> Path:
    return config_dir() / "cerepulse.toml"


def database_file() -> Path:
    return cache_dir() / "cerepulse.db"


def ensure_dirs() -> None:
    """Create all data subdirectories. Idempotent; safe to call repeatedly."""
    for directory in (config_dir(), logs_dir(), cache_dir(), exports_dir()):
        directory.mkdir(parents=True, exist_ok=True)
