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


def installer_path(version: str) -> Path:
    return downloads_dir() / f"{about.NAME}-{version}-Setup.exe"


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
    """Delete every staged installer the running version has already made obsolete.

    Existed as :func:`clear_downloads` since 0.4 with **zero callers**, so every update
    left its ~48 MB Setup.exe behind forever — a directory quietly growing by a build per
    release. Installers *newer* than the running version stay: those are pending updates,
    downloaded and waiting for the user's yes, and deleting one would silently undo the
    background download. Anything unparseable stays too — refusing to delete what we
    cannot identify is cheaper than being wrong.
    """
    from cerepulse.update.version import Version

    running = Version.parse(current_version)
    directory = downloads_dir()
    if running is None or not directory.exists():
        return 0

    prefix, suffix = f"{about.NAME}-", "-Setup.exe"
    removed = 0
    for file in directory.iterdir():
        if not (file.name.startswith(prefix) and file.name.endswith(suffix)):
            continue
        staged = Version.parse(file.name[len(prefix) : -len(suffix)])
        if staged is None or staged > running:
            continue
        try:
            size = file.stat().st_size
            file.unlink()
            removed += 1
            logger.info(
                "Removed the spent installer {} ({} MB)", file.name, size // 1_048_576
            )
        except OSError as exc:
            # Locked is normal right after an update — the installer may still be open.
            logger.debug("Could not remove {}: {}", file.name, exc)
    return removed


__all__ = [
    "CHUNK_BYTES",
    "Download",
    "DownloadError",
    "clear_downloads",
    "download_installer",
    "downloads_dir",
    "fetch_checksum",
    "installer_path",
]
