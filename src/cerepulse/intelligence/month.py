"""Week and month rollups, including the hours bank.

One subtlety drives the design. The monthly grid gives ``Tot. Hrs.`` per day, but that is
the **gross span** from first in to last out — breaks included. True worked time only comes
from a day's punch log, and fetching all of those costs one postback per day.

So a month is normally a mix: a few days with real punch detail, the rest grid-only. Rather
than silently comparing incompatible numbers, grid-only days are estimated as
``gross span - break allowance`` and counted in :attr:`MonthAnalysis.estimated_days`, so the
UI can say how much of the total is exact.

Working days are derived from the portal's own weekly-off markings rather than assuming
Saturday and Sunday, so the numbers stay right for shift workers.
"""

from __future__ import annotations

import statistics
from calendar import monthrange
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, time, timedelta

from cerepulse.intelligence.day import DayAnalysis, DayState
from cerepulse.intelligence.policy import ShiftPolicy
from cerepulse.models.attendance import AttendanceDay, DayStatus
from cerepulse.models.leave import Holiday
from cerepulse.models.values import Duration


@dataclass(frozen=True, slots=True)
class DayRollup:
    """One day reduced to what the rollups need."""

    day: date
    worked: Duration
    status: DayStatus
    estimated: bool
    is_working_day: bool

    @property
    def counts_toward_target(self) -> bool:
        return self.is_working_day


@dataclass(frozen=True, slots=True)
class WeekAnalysis:
    """Seven days of work against target."""

    week_start: date
    days: tuple[DayRollup, ...]
    total_worked: Duration
    target: Duration

    @property
    def delta(self) -> Duration:
        """Surplus (positive) or deficit (negative) against the week's target."""
        return self.total_worked - self.target

    @property
    def working_days(self) -> int:
        return sum(1 for day in self.days if day.is_working_day)


@dataclass(frozen=True, slots=True)
class MonthAnalysis:
    """A month of attendance, plus the forecast needed to finish it even."""

    year: int
    month: int
    days: tuple[DayRollup, ...]
    total_worked: Duration
    total_overtime: Duration
    short_days: int
    estimated_days: int
    average_in_time: time | None
    working_days_elapsed: int
    working_days_remaining: int
    month_target: Duration
    bank_delta: Duration
    required_daily_average: Duration | None

    @property
    def is_ahead(self) -> bool:
        return self.bank_delta.minutes >= 0

    @property
    def working_days_total(self) -> int:
        return self.working_days_elapsed + self.working_days_remaining


def analyze_month(
    days: list[AttendanceDay],
    *,
    year: int,
    month: int,
    policy: ShiftPolicy | None = None,
    analyses: dict[date, DayAnalysis] | None = None,
    holidays: list[Holiday] | None = None,
    today: date | None = None,
) -> MonthAnalysis:
    """Roll a month's grid up, using punch-level analysis wherever it is available."""
    policy = policy or ShiftPolicy.default()
    analyses = analyses or {}
    rollups = tuple(_rollup(day, policy, analyses) for day in days)

    worked_days = [r for r in rollups if r.is_working_day]
    total_worked = _sum(r.worked for r in worked_days)
    total_overtime = _sum(
        _clamp(r.worked - policy.work_target) for r in worked_days if r.worked > policy.work_target
    )
    short_days = sum(
        1 for r in worked_days if r.worked < policy.work_target and r.status.counts_as_worked
    )

    working_days_elapsed = len(worked_days)
    working_days_remaining = _remaining_working_days(
        days, year=year, month=month, holidays=holidays or [], today=today
    )

    elapsed_target = Duration(working_days_elapsed * policy.work_target.minutes)
    month_target = Duration(
        (working_days_elapsed + working_days_remaining) * policy.work_target.minutes
    )
    bank_delta = total_worked - elapsed_target

    required = None
    if bank_delta.minutes < 0 and working_days_remaining > 0:
        needed = policy.work_target.minutes * working_days_remaining - bank_delta.minutes
        required = Duration(-(-needed // working_days_remaining))  # ceil

    return MonthAnalysis(
        year=year,
        month=month,
        days=rollups,
        total_worked=total_worked,
        total_overtime=total_overtime,
        short_days=short_days,
        estimated_days=sum(1 for r in worked_days if r.estimated),
        average_in_time=_average_in_time(days),
        working_days_elapsed=working_days_elapsed,
        working_days_remaining=working_days_remaining,
        month_target=month_target,
        bank_delta=bank_delta,
        required_daily_average=required,
    )


def analyze_week(
    days: list[AttendanceDay],
    *,
    week_start: date,
    policy: ShiftPolicy | None = None,
    analyses: dict[date, DayAnalysis] | None = None,
) -> WeekAnalysis:
    """Roll up the seven days beginning at ``week_start``."""
    policy = policy or ShiftPolicy.default()
    analyses = analyses or {}
    week_end = week_start + timedelta(days=6)

    rollups = tuple(
        _rollup(day, policy, analyses) for day in days if week_start <= day.day <= week_end
    )
    working = [r for r in rollups if r.is_working_day]

    return WeekAnalysis(
        week_start=week_start,
        days=rollups,
        total_worked=_sum(r.worked for r in working),
        target=Duration(len(working) * policy.work_target.minutes),
    )


# --- helpers --------------------------------------------------------------------------


def _rollup(
    day: AttendanceDay, policy: ShiftPolicy, analyses: dict[date, DayAnalysis]
) -> DayRollup:
    analysis = analyses.get(day.day)
    if analysis is not None and analysis.state is not DayState.EMPTY:
        worked, estimated = analysis.worked, False
    else:
        # Grid-only: Tot. Hrs. is the gross span, so subtract the break allowance to get a
        # comparable worked figure. Marked estimated so the UI can qualify the total.
        worked = _clamp(day.total_hours - policy.break_target)
        estimated = day.total_hours.minutes > 0

    return DayRollup(
        day=day.day,
        worked=worked,
        status=day.status,
        estimated=estimated,
        is_working_day=day.status.counts_as_worked,
    )


def _remaining_working_days(
    days: list[AttendanceDay],
    *,
    year: int,
    month: int,
    holidays: list[Holiday],
    today: date | None,
) -> int:
    """Count working days left in the month after the last day the grid covers."""
    if not days:
        return 0

    last_known = max(day.day for day in days)
    cursor = max(last_known, today or last_known)
    _, last_day_number = monthrange(year, month)
    month_end = date(year, month, last_day_number)

    off_weekdays = _weekly_off_weekdays(days)
    holiday_dates = {holiday.day for holiday in holidays}

    remaining = 0
    candidate = cursor + timedelta(days=1)
    while candidate <= month_end:
        if candidate.weekday() not in off_weekdays and candidate not in holiday_dates:
            remaining += 1
        candidate += timedelta(days=1)
    return remaining


def _weekly_off_weekdays(days: list[AttendanceDay]) -> set[int]:
    """Infer which weekdays are non-working from how the portal marked them.

    Beats assuming Saturday and Sunday: the pattern is read from the employee's own roster.
    """
    off = {day.day.weekday() for day in days if day.status is DayStatus.WEEKLY_OFF}
    worked = {day.day.weekday() for day in days if day.status.counts_as_worked}
    return off - worked


def _average_in_time(days: list[AttendanceDay]) -> time | None:
    stamps = [day.first_in for day in days if day.first_in is not None]
    if not stamps:
        return None
    average = round(statistics.fmean(stamp.hour * 60 + stamp.minute for stamp in stamps))
    return time(average // 60 % 24, average % 60)


def _sum(values: Iterable[Duration]) -> Duration:
    total = Duration(0)
    for value in values:
        total = total + value
    return total


def _clamp(duration: Duration) -> Duration:
    return duration if duration.minutes > 0 else Duration(0)


def week_start_for(day: date, *, starts_on: int = 0) -> date:
    """The start of ``day``'s week. ``starts_on`` is a weekday number, Monday is 0."""
    offset = (day.weekday() - starts_on) % 7
    return day - timedelta(days=offset)
