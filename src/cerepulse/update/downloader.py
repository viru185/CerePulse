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
import os
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from loguru import logger

from cerepulse import __about__ as about
from cerepulse.core import paths
from cerepulse.update.mode import BuildMode
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


def archive_name(version: str) -> str:
    """``CerePulse-portable-0.15.0.zip`` — the name the build tool publishes, defined once."""
    return f"{about.NAME}-portable-{version}.zip"


def archive_path(version: str) -> Path:
    return downloads_dir() / archive_name(version)


def asset_name(version: str, mode: BuildMode) -> str:
    return archive_name(version) if mode is BuildMode.PORTABLE else installer_name(version)


def asset_path(version: str, mode: BuildMode) -> Path:
    return downloads_dir() / asset_name(version, mode)


STAGED_DIR = "staged"


def staged_root() -> Path:
    return downloads_dir() / STAGED_DIR


def staged_app_dir(version: str) -> Path:
    """Where a portable zip is unpacked: ``updates/staged/<version>/CerePulse``."""
    return staged_root() / version / about.NAME


class StageError(Exception):
    """A portable archive could not be unpacked into something runnable."""


def stage_archive(archive: Path, version: str) -> Path:
    """Unpack a portable zip and return the app folder inside it.

    Idempotent: an already-staged folder with the exe in it is returned as is. The unpack
    goes into ``<version>.part`` and is renamed only once it is complete and checked, the
    same rule the download itself follows, so a half-unpacked folder is never mistaken for a
    ready one. Every member is confined to the target (a zip can name ``../``), and the
    target is opened with the ``\\?\\`` prefix on Windows because
    ``Data/updates/staged/<v>/CerePulse/_internal/PySide6/...`` under a long user path runs
    past MAX_PATH.
    """
    app_dir = staged_app_dir(version)
    if (app_dir / f"{about.NAME}.exe").exists():
        return app_dir

    root = staged_root() / version
    partial = staged_root() / f"{version}.part"
    if partial.exists():
        shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True, exist_ok=True)

    target_root = partial.resolve()
    extract_to = Path(f"\\\\?\\{target_root}") if os.name == "nt" else target_root
    try:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                destination = (target_root / member.filename).resolve()
                if target_root not in destination.parents and destination != target_root:
                    raise StageError(
                        f"The archive tried to write outside its folder: {member.filename}"
                    )
            bundle.extractall(extract_to)
    except (zipfile.BadZipFile, OSError) as exc:
        shutil.rmtree(partial, ignore_errors=True)
        raise StageError(f"Could not unpack {archive.name}: {exc}") from exc
    except StageError:
        shutil.rmtree(partial, ignore_errors=True)
        raise

    unpacked = partial / about.NAME
    for required in (f"{about.NAME}.exe", "_internal", "portable.marker"):
        if not (unpacked / required).exists():
            shutil.rmtree(partial, ignore_errors=True)
            raise StageError(f"{archive.name} is not a portable {about.NAME} build: no {required}")

    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    partial.replace(root)
    logger.info("Unpacked {} to {}", archive.name, root)
    return app_dir


def version_in_asset_name(name: str) -> Version | None:
    """The version a staged file's name carries, in any of the three layouts, or ``None``.

    Releases up to 0.14.1 wrote ``CerePulse-0.14.1-Setup.exe``; from 0.15 the version moves
    to the end, ``CerePulse-Setup-0.15.0.exe``, so a folder of them sorts by name; the
    portable build is ``CerePulse-portable-0.15.0.zip``. A build that could read only one
    form would look straight past the file it was upgraded *from*, which is the one file
    rollback needs. Only ``.exe`` and ``.zip`` are stripped, so a ``.part`` stays unparseable
    and untouched.

    The single definition lives here so the callers that parse this name — the cleanup, the
    rollback list, and the staging check — cannot drift apart.
    """
    lowered = name.lower()
    if lowered.endswith((".exe", ".zip")):
        stem = name[:-4]
    else:
        return None
    prefix = f"{about.NAME}-"
    if not stem.startswith(prefix):
        return None
    body = stem[len(prefix) :]

    if body.startswith("Setup-"):  # CerePulse-Setup-0.15.0.exe
        return Version.parse(body[len("Setup-") :])
    if body.endswith("-Setup"):  # CerePulse-0.14.1-Setup.exe
        return Version.parse(body[: -len("-Setup")])
    if body.startswith("portable-"):  # CerePulse-portable-0.15.0.zip
        return Version.parse(body[len("portable-") :])
    return None


#: The name the exe-only callers grew up with.
version_in_installer_name = version_in_asset_name


def download_installer(
    url: str,
    version: str,
    *,
    expected_sha256: str | None = None,
    on_progress: Callable[[float], bool] | None = None,
    mode: BuildMode = BuildMode.INSTALLED,
) -> Download:
    """Fetch an installer or a portable archive, verify it, and return where it landed.

    ``on_progress`` receives a fraction 0..1 and returns False to abandon the download, so a
    user who changes their mind is not made to wait for 70 MB. Nothing here cares which
    kind of file it is; only the name does.
    """
    target = asset_path(version, mode)
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

    And a third mistake, made by the fix for the second. The 0.15.0 rule also deleted *the
    running version's own installer* as spent — which is precisely the file the **next**
    version will want to roll back to. Each build erased its own installer at first launch, so
    when the following build arrived there was never anything below it to keep, and Roll back
    stayed empty through three releases while a rule that "kept one" looked correct. The
    running version's installer stays. Two installers on disk at most; ~100 MB is the price
    of a button that works.
    """
    running = Version.parse(current_version)
    directory = downloads_dir()
    if running is None or not directory.exists():
        return 0

    staged: list[tuple[Version, Path]] = []
    for file in directory.iterdir():
        if not file.is_file():
            continue
        version = version_in_asset_name(file.name)
        if version is not None:
            staged.append((version, file))

    exes = [(v, p) for v, p in staged if p.suffix.lower() == ".exe"]
    older = sorted((entry for entry in exes if entry[0] < running), key=lambda e: e[0])
    # Everything below the running version except the newest of them. The running version's
    # own installer is not spent: it is what the next build rolls back to.
    doomed = [path for _v, path in older[:-1]]
    # Portable archives at or below the running version go outright. Rollback for a portable
    # copy is the previous *folder* kept beside the app, not the zip, and a zip is ~100 MB.
    doomed += [path for v, path in staged if path.suffix.lower() == ".zip" and v <= running]
    # Likewise the unpacked folders: after a successful swap the folder is empty, and after
    # a failed one the zip is still there to unpack again in seconds.
    root = directory / STAGED_DIR
    if root.exists():
        for folder in root.iterdir():
            version = Version.parse(folder.name)
            if folder.is_dir() and version is not None and version <= running:
                shutil.rmtree(folder, ignore_errors=True)
                logger.info("Removed the unpacked update {}", folder.name)

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
