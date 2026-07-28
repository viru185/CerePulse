"""Attendance domain models.

Two grids from the portal feed these:

* The monthly summary (``GridView1`` on MyAttendanceReport) — one row per day, with shift
  timings, first in / last out, a day portion, total hours, late mark and OT.
* The day-detail punch log (``GridView2`` in the async panel) — the raw swipe events, which
  is the same shape ninetofive pastes in by hand.

Everything derived (break time, expected out, shortfall) is computed later by the
intelligence layer; these models hold only what the portal actually reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from enum import Enum

from cerepulse.models.values import Duration


class PunchDirection(Enum):
    """Direction of a single swipe."""

    IN = "In"
    OUT = "Out"

    @classmethod
    def parse(cls, text: str) -> PunchDirection:
        value = text.strip().casefold()
        if value in {"in", "i"}:
            return cls.IN
        if value in {"out", "o"}:
            return cls.OUT
        raise ValueError(f"Unrecognized punch direction {text!r}")


class DayStatus(Enum):
    """Coarse classification of a day, derived from the portal's user-type codes.

    The portal's own codes (``DP`` = day present, ``ABS`` = absent, ``WO`` = weekly off,
    ``HL`` = holiday, ``LV`` = leave) are preserved verbatim on the day; this enum is the
    normalized view the UI groups on.
    """

    PRESENT = "present"
    HALF_DAY = "half_day"
    ABSENT = "absent"
    WEEKLY_OFF = "weekly_off"
    HOLIDAY = "holiday"
    LEAVE = "leave"
    UNKNOWN = "unknown"

    @property
    def counts_as_worked(self) -> bool:
        """Whether the day contributes working time, for streaks and monthly rollups."""
        return self in {DayStatus.PRESENT, DayStatus.HALF_DAY}


@dataclass(frozen=True, slots=True)
class Punch:
    """A single swipe event from the day-detail log."""

    at: time
    direction: PunchDirection
    ip_address: str = ""
    machine: str = ""
    approver_remark: str = ""


@dataclass(frozen=True, slots=True)
class AttendanceDay:
    """One row of the monthly summary, optionally enriched with its punch log.

    Fields map directly onto the captured ``GridView1`` columns. ``punches`` is filled only
    after the day's detail panel has been fetched; an empty list means "not yet loaded",
    which the repository distinguishes from "loaded and genuinely empty" via
    :attr:`detail_loaded`.
    """

    day: date
    weekday: str
    status: DayStatus
    shift_code: str = ""
    shift_in: time | None = None
    shift_out: time | None = None
    first_in: time | None = None
    last_out: time | None = None
    user_type_1: str = ""
    user_type_2: str = ""
    portion: float = 0.0
    total_hours: Duration = field(default_factory=lambda: Duration(0))
    late_mark: Duration = field(default_factory=lambda: Duration(0))
    ot_hours: Duration = field(default_factory=lambda: Duration(0))
    remarks: str = ""
    punches: tuple[Punch, ...] = ()
    detail_loaded: bool = False

    @property
    def is_present(self) -> bool:
        return self.status.counts_as_worked

    @property
    def has_punches(self) -> bool:
        return bool(self.punches)


@dataclass(frozen=True, slots=True)
class AttendanceMonth:
    """A month of attendance for one employee."""

    employee_code: str
    year: int
    month: int
    days: tuple[AttendanceDay, ...] = ()

    @property
    def present_days(self) -> int:
        return sum(1 for day in self.days if day.is_present)

    @property
    def total_hours(self) -> Duration:
        total = Duration(0)
        for day in self.days:
            total = total + day.total_hours
        return total

    def day_on(self, when: date) -> AttendanceDay | None:
        for day in self.days:
            if day.day == when:
                return day
        return None
