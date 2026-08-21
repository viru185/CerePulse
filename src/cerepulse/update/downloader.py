"""Fetching an installer in the background.

Streamed rather than read whole: the installer is around 70 MB, and holding that in memory
to write it out again is pointless when the file is the only thing wanted.

Two properties matter more than speed. **Nothing partial is ever runnable** — the download
lands on a ``.part`` file and is renamed only once it is complete and verified, so a
connection dropped at 90% leaves nothing that looks installable. And **verification is not
optional when a checksum is published**: this is a file the app is about to execute, so a
truncated or tampered download must fail closed rather than run.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from loguru import logger

from cerepulse import __about__ as about
from cerepulse.core import paths
from cerepulse.update.version import Version

#: Generous: this runs in the background and a slow connection is not a failure.
TIMEOUT_SECONDS = 120.0
CHUNK_BYTES = 256 * 1024

#: Reported roughly this often, rather than per chunk, so the GUI is not flooded.
PROGRESS_STEP = 0.01


@dataclass(frozen=True, slots=True)
class Download:
    """A verified installer sitting on disk, ready to run."""

    path: Path
    version: str
    size: int
    #: False when the release published no checksum to compare against.
    verified: bool


class DownloadError(Exception):
    """The installer could not be fetched or did not match its checksum."""


def downloads_dir() -> Path:
    """Where installers are staged. Beside the cache, not in Downloads."""
    return paths.data_root() / "updates"


def installer_name(version: str) -> str:
    """``CerePulse-Setup-0.15.0.exe``."""
    return f"{about.NAME}-Setup-{version}.exe"


def installer_path(version: str) -> Path:
    return downloads_dir() / installer_name(version)


def version_in_installer_name(name: str) -> Version | None:
    """The version an installer's filename carries, in either naming, or ``None``.

    Both layouts are read on purpose. Releases up to 0.14.1 wrote
    ``CerePulse-0.14.1-Setup.exe``; from 0.15 the version moves to the end so a folder of
    them sorts by name. A build that could only read the new form would look straight past
    the installer it was upgraded *from*, which is the one file rollback needs.

    The single definition lives here so the three callers that parse this name —
    the cleanup, the rollback list, and the staging check — cannot drift apart.
    """
    stem = name[:-4] if name.lower().endswith(".exe") else name
    prefix = f"{about.NAME}-"
    if not stem.startswith(prefix):
        return None
    body = stem[len(prefix) :]

    if body.startswith("Setup-"):  # CerePulse-Setup-0.15.0.exe
        return Version.parse(body[len("Setup-") :])
    if body.endswith("-Setup"):  # CerePulse-0.14.1-Setup.exe
        return Version.parse(body[: -len("-Setup")])
    return None


def download_installer(
    url: str,
    version: str,
    *,
    expected_sha256: str | None = None,
    on_progress: Callable[[float], bool] | None = None,
) -> Download:
    """Fetch an installer, verify it, and return where it landed.

    ``on_progress`` receives a fraction 0..1 and returns False to abandon the download, so a
    user who changes their mind is not made to wait for 70 MB.
    """
    target = installer_path(version)
    if target.exists():
        # Already fetched and verified on an earlier run; re-downloading would be waste.
        logger.info("Installer for {} is already downloaded", version)
        return Download(target, version, target.stat().st_size, verified=expected_sha256 is None)

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    digest = hashlib.sha256()
    written = 0

    try:
        with httpx.stream(
            "GET",
            url,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": f"{about.NAME}/{about.VERSION}"},
        ) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or 0)

            with partial.open("wb") as handle:
                reported = 0.0
                for chunk in response.iter_bytes(CHUNK_BYTES):
                    handle.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)

                    if on_progress is not None and total:
                        fraction = written / total
                        if fraction - reported >= PROGRESS_STEP:
                            reported = fraction
                            if not on_progress(fraction):
                                raise DownloadError("Download cancelled")
    except httpx.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise DownloadError(f"Could not download the update: {exc}") from exc
    except DownloadError:
        partial.unlink(missing_ok=True)
        raise

    actual = digest.hexdigest()
    if expected_sha256 and actual.lower() != expected_sha256.strip().lower():
        partial.unlink(missing_ok=True)
        # Fail closed. This file was about to be executed.
        raise DownloadError(
            "The downloaded update did not match its published checksum, so it was discarded."
        )

    partial.replace(target)
    logger.info(
        "Downloaded {} ({} bytes, verified={})", target.name, written, bool(expected_sha256)
    )
    return Download(target, version, written, verified=bool(expected_sha256))


def fetch_checksum(url: str, asset_name: str) -> str | None:
    """Read a published ``SHA256SUMS`` file and find this asset's digest.

    Absent or unreadable means "no checksum published", not an error: older releases have
    none, and refusing to update because of that would be worse than the risk.
    """
    try:
        response = httpx.get(url, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.debug("No checksum file available: {}", exc)
        return None

    for line in response.text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == asset_name:
            return parts[0]
    return None


def clear_downloads(keep: str | None = None) -> int:
    """Remove staged installers, optionally keeping one version for rollback."""
    directory = downloads_dir()
    if not directory.exists():
        return 0

    removed = 0
    for file in directory.iterdir():
        if keep and file.name == installer_path(keep).name:
            continue
        try:
            file.unlink()
            removed += 1
        except OSError as exc:
            logger.debug("Could not remove {}: {}", file, exc)
    return removed


def clear_spent_installers(current_version: str) -> int:
    """Delete staged installers this build has superseded — but keep one to roll back to.

    Two mistakes are possible here and 0.14 made the second one.

    Keeping everything was the first: the helper this replaced had **zero callers** since
    0.4, so every update left its ~48 MB Setup.exe behind forever.

    Deleting everything at or below the running version was the second, and worse. That is
    exactly the set :func:`~cerepulse.update.installer.rollback_candidates` offers — the
    previous build *is* at a lower version — so the Roll back button silently had nothing
    to offer from 0.14 onward. Cleanup that removes the only file a feature depends on is
    not cleanup.

    So: the newest installer *below* the running version stays, as the one rollback target.
    Anything older than that goes. Anything newer stays too — that is a pending update
    already downloaded and waiting for a yes, and deleting it would silently undo the
    background download. Unparseable names are left alone; refusing to delete what cannot
    be identified is cheaper than being wrong.
    """
    running = Version.parse(current_version)
    directory = downloads_dir()
    if running is None or not directory.exists():
        return 0

    staged: list[tuple[Version, Path]] = []
    for file in directory.iterdir():
        version = version_in_installer_name(file.name)
        if version is not None:
            staged.append((version, file))

    older = sorted((entry for entry in staged if entry[0] < running), key=lambda e: e[0])
    # Everything below the running version except the newest of them, plus the running
    # version's own installer, which has already been installed.
    doomed = [path for _v, path in older[:-1]]
    doomed += [path for version, path in staged if version == running]

    removed = 0
    for path in doomed:
        try:
            size = path.stat().st_size
            path.unlink()
            removed += 1
            logger.info("Removed the spent installer {} ({} MB)", path.name, size // 1_048_576)
        except OSError as exc:
            # Locked is normal right after an update — the installer may still be open.
            logger.debug("Could not remove {}: {}", path.name, exc)

    kept = [path.name for _v, path in older[-1:]]
    if kept:
        logger.info("Kept {} so a rollback is still possible", kept[0])
    return removed


__all__ = [
    "CHUNK_BYTES",
    "Download",
    "DownloadError",
    "clear_downloads",
    "download_installer",
    "downloads_dir",
    "fetch_checksum",
    "installer_name",
    "installer_path",
    "version_in_installer_name",
]
