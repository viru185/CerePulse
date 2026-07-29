"""Insights — the single channel through which "smart" behavior reaches the UI and tray.

Every actionable observation is an :class:`Insight`, so the views, the notification policy
and the clipboard summary all consume one uniform shape rather than each re-deriving
conclusions from raw numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(Enum):
    """How loudly an insight should present. Ordered, so callers can filter by threshold."""

    SUCCESS = 1
    INFO = 2
    WARNING = 3
    CRITICAL = 4

    def __lt__(self, other: Severity) -> bool:
        return self.value < other.value

    def __le__(self, other: Severity) -> bool:
        return self.value <= other.value


class InsightKind(Enum):
    """What an insight is about. Used for per-alert notification toggles."""

    ON_TRACK = "on_track"
    SHORT_HOURS = "short_hours"
    OVERTIME = "overtime"
    EARLY_EXIT = "early_exit"
    SWIPE_NEEDED = "swipe_needed"
    SWIPE_FILED = "swipe_filed"
    LONG_BREAK = "long_break"
    MISSING_PUNCH = "missing_punch"
    STILL_WORKING = "still_working"
    NO_PUNCHES = "no_punches"
    LEAVE_EXPIRING = "leave_expiring"
    HOURS_BANK_DEFICIT = "hours_bank_deficit"
    ANOMALY = "anomaly"
    BREAK_HEADROOM = "break_headroom"


class ActionKind(Enum):
    """What the UI should do when an insight's action is invoked."""

    OPEN_SWIPE_REQUEST = "open_swipe_request"
    OPEN_ATTENDANCE = "open_attendance"


@dataclass(frozen=True, slots=True)
class Action:
    """A suggested next step. CerePulse is read-only, so actions only ever navigate."""

    kind: ActionKind
    label: str


@dataclass(frozen=True, slots=True)
class Insight:
    """One thing worth telling the user."""

    kind: InsightKind
    severity: Severity
    title: str
    detail: str = ""
    action: Action | None = None
