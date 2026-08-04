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


# --- other secrets --------------------------------------------------------------------
#
# The portal password is not the only thing that must stay out of config.toml. The same
# store, under reserved names rather than a username, so one Credential Manager entry per
# secret and nothing new to back up or migrate.

#: The user's own TomTom API key, for arrival estimates.
TOMTOM_KEY = "api:tomtom"


def store_secret(name: str, value: str) -> bool:
    """Persist a named secret. Returns False if the keyring refused.

    Clearing on an empty value rather than storing one: "" is how a user says *remove this*,
    and an empty string in the store would read as a configured-but-broken key forever.
    """
    if not value:
        clear_secret(name)
        return True
    try:
        keyring.set_password(SERVICE_NAME, name, value)
    except KeyringError:
        logger.exception("Could not store {} in the Windows Credential Manager", name)
        return False
    # Deliberately no value, no length, no prefix. A secret's shape is still information.
    logger.info("Stored the secret {}", name)
    return True


def get_secret(name: str) -> str:
    """The stored secret, or an empty string when absent or the keyring is unavailable."""
    try:
        return keyring.get_password(SERVICE_NAME, name) or ""
    except KeyringError:
        logger.exception("Could not read {} from the Windows Credential Manager", name)
        return ""


def clear_secret(name: str) -> None:
    """Remove a stored secret. Succeeds silently when nothing is stored."""
    try:
        keyring.delete_password(SERVICE_NAME, name)
    except keyring.errors.PasswordDeleteError:
        return
    except KeyringError:
        logger.exception("Could not clear the secret {}", name)
        return
    logger.info("Cleared the secret {}", name)
