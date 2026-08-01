"""Check GitHub Releases for a newer build.

Unauthenticated: the repository is public, so no token is needed and none is asked for. The
check is best-effort — a rate limit, an outage or no network must never interrupt someone
looking at their attendance, so every failure returns "no update" rather than raising.

The list endpoint is used rather than ``/releases/latest``, because ``latest`` excludes
prereleases server-side and the beta channel needs to see them. Filtering happens here, so
one request answers for either channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx
from loguru import logger

from cerepulse import __about__ as about
from cerepulse.update.channel import Channel
from cerepulse.update.version import Version, is_newer

#: Derived from the configured repository so a fork needs no code change.
API_URL = about.REPO_URL.replace("https://github.com/", "https://api.github.com/repos/")
RELEASES_URL = f"{API_URL}/releases"

#: Short, because this runs at startup and must never delay the window.
TIMEOUT_SECONDS = 6.0

#: Enough to find the newest release on either channel without paging.
PAGE_SIZE = 20


@dataclass(frozen=True, slots=True)
class Release:
    """A published release, as much of it as the app cares about."""

    version: str
    name: str
    notes: str
    url: str
    published_at: datetime | None = None
    installer_url: str = ""
    #: Bytes, from the asset metadata, so a download can show a percentage.
    installer_size: int = 0
    prerelease: bool = False

    @property
    def display_name(self) -> str:
        return self.name or f"Version {self.version}"

    @property
    def channel(self) -> Channel:
        return Channel.BETA if self.prerelease else Channel.STABLE

    @property
    def is_installable(self) -> bool:
        """Whether this release published something the app can actually install."""
        return bool(self.installer_url)


def check_for_update(
    current_version: str | None = None,
    *,
    channel: Channel = Channel.STABLE,
) -> Release | None:
    """The newest release on ``channel`` that is newer than this build, or None.

    A beta installation is offered stable releases too: once a stable build overtakes the
    beta someone is running, they should be moved onto it rather than stranded.
    """
    current = current_version or about.VERSION

    payload = _fetch()
    if payload is None:
        return None

    candidates = [
        release
        for release in (_parse(item) for item in payload)
        if release is not None
        and (channel.accepts_prereleases or not release.prerelease)
        and is_newer(release.version, current)
    ]
    if not candidates:
        logger.debug("No newer release on {} ({} is current)", channel.value, current)
        return None

    newest = max(candidates, key=lambda release: Version.parse(release.version) or Version(0, 0))
    logger.info("Update available on {}: {} (running {})", channel.value, newest.version, current)
    return newest


def _fetch() -> list[dict[str, object]] | None:
    try:
        response = httpx.get(
            RELEASES_URL,
            params={"per_page": PAGE_SIZE},
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

    return payload if isinstance(payload, list) else None


def _parse(payload: object) -> Release | None:
    if not isinstance(payload, dict) or payload.get("draft"):
        return None

    tag = str(payload.get("tag_name") or "")
    if not tag or Version.parse(tag) is None:
        return None

    url, size = _installer_asset(payload)
    return Release(
        version=tag.lstrip("vV"),
        name=str(payload.get("name") or ""),
        notes=str(payload.get("body") or ""),
        url=str(payload.get("html_url") or about.REPO_URL),
        published_at=_parse_timestamp(payload.get("published_at")),
        installer_url=url,
        installer_size=size,
        prerelease=bool(payload.get("prerelease")),
    )


def _installer_asset(payload: dict[str, object]) -> tuple[str, int]:
    """The Setup .exe and its size, when the release publishes one."""
    assets = payload.get("assets")
    if not isinstance(assets, list):
        return "", 0
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", "")).lower()
        if name.endswith(".exe") and "setup" in name:
            size = asset.get("size")
            return str(asset.get("browser_download_url", "")), int(size) if size else 0
    return "", 0


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
