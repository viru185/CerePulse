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

import hashlib
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
    ON_DUTY = "on_duty"
    UNKNOWN = "unknown"

    @property
    def counts_as_worked(self) -> bool:
        """Whether this day's hours are measured against the target.

        Outdoor duty is deliberately excluded even though the employee was working: there
        are no swipes to measure, so counting it would score a full day as zero hours and
        report a deficit the employee cannot act on. It is a real working day
        (:attr:`is_attended`) but not a measurable one.
        """
        return self in {DayStatus.PRESENT, DayStatus.HALF_DAY}

    @property
    def is_attended(self) -> bool:
        """Whether the employee was working, measurable hours or not."""
        return self.counts_as_worked or self is DayStatus.ON_DUTY

    @property
    def is_off(self) -> bool:
        """Whether the day was legitimately not worked, as opposed to unexplained."""
        return self in {DayStatus.WEEKLY_OFF, DayStatus.HOLIDAY, DayStatus.LEAVE}


#: The portal's own codes for outdoor duty, in either user-type column.
OUTDOOR_DUTY_CODES = frozenset({"OD", "ODT"})


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

    @property
    def has_outdoor_duty(self) -> bool:
        """Whether any part of this day was worked off site.

        Read from the raw portal codes rather than from :attr:`status`, because a day marked
        half on-duty and half present resolves to ``HALF_DAY`` — correctly, since half of it
        really was measured — and the on-duty half would otherwise vanish. The codes are kept
        verbatim on the day for exactly this kind of question.
        """
        codes = {self.user_type_1.strip().upper(), self.user_type_2.strip().upper()}
        return self.status is DayStatus.ON_DUTY or bool(codes & OUTDOOR_DUTY_CODES)


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

    def content_digest(self) -> str:
        """A stable digest of everything the grid reports for this month.

        Lets a sync skip writing a month that has not changed. A past month is identical on
        every refresh for the rest of the year, and rewriting its thirty-one rows each time
        is work nobody asked for.

        Punches are excluded on purpose. The grid never carries them, so a month whose punch
        detail has since been fetched still digests the same — which is correct, because the
        digest answers "has the grid changed", and the punch log is not the grid's to change.
        """
        blake = hashlib.blake2s(digest_size=16)
        blake.update(f"{self.employee_code}|{self.year:04d}-{self.month:02d}".encode())
        for day in sorted(self.days, key=lambda item: item.day):
            blake.update(
                "|".join(
                    (
                        day.day.isoformat(),
                        day.status.value,
                        str(day.first_in or ""),
                        str(day.last_out or ""),
                        str(day.total_hours.minutes),
                        str(day.late_mark.minutes),
                        str(day.ot_hours.minutes),
                        f"{day.portion:g}",
                        day.remarks,
                    )
                ).encode()
            )
        return blake.hexdigest()
