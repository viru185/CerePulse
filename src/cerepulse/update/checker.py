"""Check GitHub Releases for a newer build.

Unauthenticated: the repository is public, so no token is needed and none is asked for. The
check is best-effort — a rate limit, an outage or no network must never interrupt someone
looking at their attendance, so every failure returns "no update" rather than raising.

The app never installs anything by itself. It reports what is available and opens the
release page; downloading and running an installer is the user's decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx
from loguru import logger

from cerepulse import __about__ as about
from cerepulse.update.version import is_newer

#: Derived from the configured repository so a fork needs no code change.
API_URL = about.REPO_URL.replace("https://github.com/", "https://api.github.com/repos/")
LATEST_RELEASE_URL = f"{API_URL}/releases/latest"

#: Short, because this runs at startup and must never delay the window.
TIMEOUT_SECONDS = 6.0


@dataclass(frozen=True, slots=True)
class Release:
    """A published release, as much of it as the app cares about."""

    version: str
    name: str
    notes: str
    url: str
    published_at: datetime | None = None
    installer_url: str = ""

    @property
    def display_name(self) -> str:
        return self.name or f"Version {self.version}"


def check_for_update(current_version: str | None = None) -> Release | None:
    """Return the latest release when it is newer than this build, otherwise None."""
    current = current_version or about.VERSION

    try:
        response = httpx.get(
            LATEST_RELEASE_URL,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"{about.NAME}/{about.VERSION}",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Never surface this: an update check failing is not the user's problem.
        logger.debug("Update check failed: {}", exc)
        return None

    release = _parse(payload)
    if release is None:
        return None
    if release.version == "" or not is_newer(release.version, current):
        logger.debug("No newer release ({} is current)", current)
        return None

    logger.info("Update available: {} (running {})", release.version, current)
    return release


def _parse(payload: dict[str, object]) -> Release | None:
    if payload.get("draft") or payload.get("prerelease"):
        return None

    tag = str(payload.get("tag_name") or "")
    if not tag:
        return None

    return Release(
        version=tag.lstrip("vV"),
        name=str(payload.get("name") or ""),
        notes=str(payload.get("body") or ""),
        url=str(payload.get("html_url") or about.REPO_URL),
        published_at=_parse_timestamp(payload.get("published_at")),
        installer_url=_installer_asset(payload),
    )


def _installer_asset(payload: dict[str, object]) -> str:
    """The Setup .exe, when the release publishes one."""
    assets = payload.get("assets")
    if not isinstance(assets, list):
        return ""
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", "")).lower()
        if name.endswith(".exe") and "setup" in name:
            return str(asset.get("browser_download_url", ""))
    return ""


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
