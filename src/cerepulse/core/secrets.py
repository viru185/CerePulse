"""Credential storage backed by the Windows Credential Manager (via ``keyring``).

Chapter 14 section 5: passwords are never written to config files or logs. Storing is
opt-in ("Remember me"); the username itself lives in normal config, only the password
lives here.
"""

from __future__ import annotations

import keyring
from keyring.errors import KeyringError
from loguru import logger

SERVICE_NAME = "CerePulse"


def store_password(username: str, password: str) -> bool:
    """Persist ``password`` for ``username``. Returns False if the keyring refused."""
    try:
        keyring.set_password(SERVICE_NAME, username, password)
    except KeyringError:
        logger.exception("Could not store credentials in the Windows Credential Manager")
        return False
    logger.info("Stored credentials for {}", username)
    return True


def get_password(username: str) -> str | None:
    """Return the stored password, or None if absent or the keyring is unavailable."""
    try:
        return keyring.get_password(SERVICE_NAME, username)
    except KeyringError:
        logger.exception("Could not read credentials from the Windows Credential Manager")
        return None


def clear_password(username: str) -> None:
    """Remove any stored password. Succeeds silently when nothing is stored."""
    try:
        keyring.delete_password(SERVICE_NAME, username)
    except keyring.errors.PasswordDeleteError:
        pass
    except KeyringError:
        logger.exception("Could not clear stored credentials")
        return
    logger.info("Cleared stored credentials for {}", username)


def has_password(username: str) -> bool:
    return bool(username) and get_password(username) is not None
