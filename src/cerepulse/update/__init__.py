"""Update checking, downloading, installing and rollback."""

from __future__ import annotations

from cerepulse.update.channel import Channel
from cerepulse.update.checker import Release, check_for_update, previous_release
from cerepulse.update.downloader import (
    Download,
    DownloadError,
    StageError,
    archive_path,
    clear_downloads,
    download_installer,
    fetch_checksum,
    installer_path,
    stage_archive,
)
from cerepulse.update.installer import (
    InstallError,
    apply_update,
    clear_failed_portable_folder,
    is_installed_build,
    rollback_candidates,
    rollback_to,
)
from cerepulse.update.mode import BuildMode, build_mode, can_self_update
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
    "BuildMode",
    "Channel",
    "Download",
    "DownloadError",
    "StageError",
    "archive_path",
    "build_mode",
    "can_self_update",
    "clear_failed_portable_folder",
    "stage_archive",
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
    "previous_release",
    "record_update",
    "rollback_candidates",
    "rollback_to",
    "should_show_whats_new",
    "update_history",
]
