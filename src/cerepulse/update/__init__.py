"""Update checking and release-note tracking."""

from __future__ import annotations

from cerepulse.update.checker import Release, check_for_update
from cerepulse.update.seen import last_seen_version, mark_seen, should_show_whats_new
from cerepulse.update.version import Version, is_newer

__all__ = [
    "Release",
    "Version",
    "check_for_update",
    "is_newer",
    "last_seen_version",
    "mark_seen",
    "should_show_whats_new",
]
