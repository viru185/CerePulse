"""What can be synced, separately from everything else.

The app has always synced in one lump: press Refresh and five things happen, or do not. That
is fine until one of them is wrong — a stale holiday calendar could only be fixed by waiting
a day or clearing the whole cache, because ``refresh_holidays`` had no path from the UI at
all.

Each scope here is a thing with its own freshness, its own refresh, and its own reason to
be out of date. The sync panel lists them, the status line summarises them, and nothing has
to hard-code a list of five strings in three places.

The keys match what :class:`~cerepulse.repository.leave.SyncMetadataRepository` already
stores, so freshness comes from the same rows the TTL checks read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class Scope(Enum):
    """One independently syncable thing."""

    ATTENDANCE = "attendance"
    DAY_DETAIL = "day_detail"
    LEAVE = "leave"
    SWIPE_REQUESTS = "swipe_requests"
    APPLICATIONS = "applications"
    HOLIDAYS = "holidays"

    @property
    def label(self) -> str:
        return {
            Scope.ATTENDANCE: "Attendance",
            Scope.DAY_DETAIL: "Punch detail",
            Scope.LEAVE: "Leave balances",
            Scope.SWIPE_REQUESTS: "Swipe requests",
            Scope.APPLICATIONS: "Applications",
            Scope.HOLIDAYS: "Holidays",
        }[self]

    @property
    def explanation(self) -> str:
        """Why this one might be behind, which is usually the question being asked."""
        return {
            Scope.ATTENDANCE: "The month grid — days, statuses and total hours.",
            Scope.DAY_DETAIL: (
                "Individual punches. One portal request per day, so it fills in gradually."
            ),
            Scope.LEAVE: "Balances and the ledger behind them.",
            Scope.SWIPE_REQUESTS: "Requests you have filed and where they stand.",
            Scope.APPLICATIONS: (
                "Leave, outdoor duty and comp-off you have applied for, and their approval."
            ),
            Scope.HOLIDAYS: "The company calendar. Published once a year, so it is checked daily.",
        }[self]


@dataclass(frozen=True, slots=True)
class ScopeStatus:
    """One row of the sync panel."""

    scope: Scope
    last_synced: datetime | None
    #: Only meaningful for DAY_DETAIL: days in the current month still missing punches.
    pending: int = 0

    @property
    def never_synced(self) -> bool:
        return self.last_synced is None

    @property
    def is_complete(self) -> bool:
        """Whether there is anything outstanding. Only day detail can be partial."""
        return self.pending == 0


def attendance_key(year: int, month: int) -> str:
    """The metadata key for one month, matching what the repository writes."""
    return f"{Scope.ATTENDANCE.value}:{year:04d}-{month:02d}"


def scope_for_day(day: date) -> str:
    return attendance_key(day.year, day.month)


__all__ = ["Scope", "ScopeStatus", "attendance_key", "scope_for_day"]
