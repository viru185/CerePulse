"""Update checking, downloading, installing and rollback."""

from __future__ import annotations

from cerepulse.update.channel import Channel
from cerepulse.update.checker import Release, check_for_update
from cerepulse.update.downloader import (
    Download,
    DownloadError,
    clear_downloads,
    download_installer,
    fetch_checksum,
    installer_path,
)
from cerepulse.update.installer import (
    InstallError,
    apply_update,
    is_installed_build,
    rollback_candidates,
    rollback_to,
)
from cerepulse.update.seen import (
    UpdateEvent,
    last_checked,
    last_seen_version,
    mark_checked,
    mark_seen,
    record_update,
    should_show_whats_new,
    update_history,
)
from cerepulse.update.version import Version, is_newer

__all__ = [
    "Channel",
    "Download",
    "DownloadError",
    "InstallError",
    "Release",
    "UpdateEvent",
    "Version",
    "apply_update",
    "check_for_update",
    "clear_downloads",
    "download_installer",
    "fetch_checksum",
    "installer_path",
    "is_installed_build",
    "is_newer",
    "last_checked",
    "last_seen_version",
    "mark_checked",
    "mark_seen",
    "record_update",
    "rollback_candidates",
    "rollback_to",
    "should_show_whats_new",
    "update_history",
]
