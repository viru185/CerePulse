"""Tray presence, desktop notifications, and Windows startup registration."""

from __future__ import annotations

from cerepulse.notify.policy import SILENT, TOGGLES, NotificationPolicy, notification_title
from cerepulse.notify.startup import is_registered, set_registered
from cerepulse.notify.tray import Tray

__all__ = [
    "SILENT",
    "TOGGLES",
    "NotificationPolicy",
    "Tray",
    "is_registered",
    "notification_title",
    "set_registered",
]
