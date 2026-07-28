"""Start-with-Windows registration.

Uses the per-user ``Run`` key rather than a scheduled task or the Startup folder: it needs
no elevation, is trivially reversible, and is the location users actually look in when they
want to turn something off.

Registration is skipped when running from source, because the entry would point at a
``python.exe`` in a virtual environment that may not survive.
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from cerepulse import __about__ as about

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = about.NAME


def is_frozen() -> bool:
    """True when running from a PyInstaller build rather than a source checkout."""
    return bool(getattr(sys, "frozen", False))


def executable_command() -> str:
    """The command Windows should run at sign-in, quoted for the registry."""
    return f'"{Path(sys.executable).resolve()}"'


def is_registered() -> bool:
    """Whether the app is currently set to start with Windows."""
    try:
        import winreg
    except ImportError:  # pragma: no cover — Windows only
        return False

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
        return bool(value)
    except (OSError, FileNotFoundError):
        return False


def set_registered(enabled: bool) -> bool:
    """Add or remove the Run entry. Returns whether the state now matches the request."""
    try:
        import winreg
    except ImportError:  # pragma: no cover — Windows only
        logger.warning("Start-with-Windows is only supported on Windows")
        return False

    if enabled and not is_frozen():
        # A dev-run entry would point into a virtual environment and break on rebuild.
        logger.info("Not registering startup: running from source, not a build")
        return False

    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, executable_command())
                logger.info("Registered {} to start with Windows", about.NAME)
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                    logger.info("Removed {} from Windows startup", about.NAME)
                except FileNotFoundError:
                    pass  # already absent
    except OSError as exc:
        logger.warning("Could not update the startup entry: {}", exc)
        return False

    return is_registered() == enabled
