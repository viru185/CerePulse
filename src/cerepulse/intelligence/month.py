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
    #: Marked as worked by the portal, but carrying no punches and no hours at all.
    unmeasured: bool = False
    #: Today, still being worked. Its target is not owed yet.
    in_progress: bool = False

    @property
    def counts_toward_target(self) -> bool:
        return self.is_working_day


@dataclass(frozen=True, slots=True)
class WeekAnalysis:
    """Seven days of work against target, and what the rest of the week would take.

    ``total_worked`` and ``target`` both cover **completed** working days only, so the two
    are always comparable. Counting today's four hours against a full eight-hour target
    reports a deficit that exists only because it is lunchtime and shrinks by itself as the
    afternoon passes — the same mistake :func:`analyze_month` already avoids. Today's hours
    are reported separately as :attr:`in_progress`.
    """

    week_start: date
    days: tuple[DayRollup, ...]
    total_worked: Duration
    target: Duration
    #: Hours logged so far on a day still being worked. Not counted in the delta.
    in_progress: Duration = Duration(0)
    #: Working days in this week not yet finished, today included when it is still running.
    days_ahead: int = 0
    policy: ShiftPolicy = ShiftPolicy()

    @property
    def delta(self) -> Duration:
        """Surplus (positive) or deficit (negative) against the week's target."""
        return self.total_worked - self.target

    @property
    def working_days(self) -> int:
        return sum(1 for day in self.days if day.is_working_day)

    @property
    def full_target(self) -> Duration:
        """The whole week's target, including the days still to come."""
        return self.target + Duration(self.days_ahead * self.policy.work_target.minutes)

    @property
    def progress(self) -> float:
        """Fraction of the whole week done. Today's partial hours count here, deliberately.

        The delta answers "am I behind"; this answers "how far through the week am I", and
        for that question the hours already worked today are exactly as real as yesterday's.
        """
        if self.full_target.minutes <= 0:
            return 0.0
        return (self.total_worked + self.in_progress).minutes / self.full_target.minutes

    @property
    def projected_total(self) -> Duration:
        """Where the week lands if every remaining day makes its target exactly."""
        return self.total_worked + Duration(self.days_ahead * self.policy.work_target.minutes)

    @property
    def measured(self) -> tuple[DayRollup, ...]:
        """Completed working days carrying hours — the only ones worth comparing."""
        return tuple(
            day
            for day in self.days
            if day.is_working_day and not day.in_progress and day.worked.minutes > 0
        )

    @property
    def best_day(self) -> DayRollup | None:
        """The longest day, or nothing when there is not yet a comparison to make.

        One day is not a best day: with a single measurement "best" and "worst" name the
        same row, which reads as a finding and is not one.
        """
        days = self.measured
        return max(days, key=lambda day: day.worked.minutes) if len(days) > 1 else None

    @property
    def worst_day(self) -> DayRollup | None:
        days = self.measured
        return min(days, key=lambda day: day.worked.minutes) if len(days) > 1 else None


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
    #: Days the portal calls worked but for which it holds no punches or hours at all.
    unmeasured_days: int
    average_in_time: time | None
    working_days_elapsed: int
    working_days_remaining: int
    month_target: Duration
    bank_delta: Duration
    required_daily_average: Duration | None
    #: The policy every figure above was measured against. Carried on the result so a view
    #: can scale a chart against the same target the numbers beside it used, rather than
    #: reaching for a default of its own.
    policy: ShiftPolicy = ShiftPolicy()

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
    rollups = tuple(_rollup(day, policy, analyses, today) for day in days)

    # An in-progress day owes nothing yet. Counting today's four hours against a full
    # eight-hour target reports a deficit that exists only because it is lunchtime, and it
    # shrinks by itself as the afternoon passes — which is the signature of a number that
    # was never real. It is counted as a day still to come instead.
    worked_days = [r for r in rollups if r.is_working_day and not r.in_progress]
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
    ) + sum(1 for r in rollups if r.in_progress)

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
        unmeasured_days=sum(1 for r in rollups if r.unmeasured),
        average_in_time=_average_in_time(days),
        working_days_elapsed=working_days_elapsed,
        working_days_remaining=working_days_remaining,
        month_target=month_target,
        bank_delta=bank_delta,
        required_daily_average=required,
        policy=policy,
    )


def analyze_week(
    days: list[AttendanceDay],
    *,
    week_start: date,
    policy: ShiftPolicy | None = None,
    analyses: dict[date, DayAnalysis] | None = None,
    today: date | None = None,
    holidays: list[Holiday] | None = None,
) -> WeekAnalysis:
    """Roll up the seven days beginning at ``week_start``.

    ``days`` is the whole month rather than the week, because the days still ahead in this
    week have no grid rows yet and the only way to tell a working Friday from a rostered
    day off is the pattern in the rest of the month.
    """
    policy = policy or ShiftPolicy.default()
    analyses = analyses or {}
    week_end = week_start + timedelta(days=6)

    rollups = tuple(
        _rollup(day, policy, analyses, today) for day in days if week_start <= day.day <= week_end
    )
    working = [r for r in rollups if r.is_working_day]
    completed = [r for r in working if not r.in_progress]

    return WeekAnalysis(
        week_start=week_start,
        days=rollups,
        total_worked=_sum(r.worked for r in completed),
        target=Duration(len(completed) * policy.work_target.minutes),
        in_progress=_sum(r.worked for r in working if r.in_progress),
        days_ahead=_week_days_ahead(
            days,
            rollups,
            week_start=week_start,
            week_end=week_end,
            holidays=holidays or [],
            today=today,
        ),
        policy=policy,
    )


def _week_days_ahead(
    month: list[AttendanceDay],
    rollups: tuple[DayRollup, ...],
    *,
    week_start: date,
    week_end: date,
    holidays: list[Holiday],
    today: date | None,
) -> int:
    """Working days in this week that have not been finished yet.

    A day the grid has no row for is still ahead — that is the normal state of Thursday on
    a Tuesday, and the projection is worthless without it.
    """
    if today is None or today > week_end:
        return 0

    by_date = {rollup.day: rollup for rollup in rollups}
    off_weekdays = _weekly_off_weekdays(month)
    holiday_dates = {holiday.day for holiday in holidays}

    ahead = 0
    for offset in range(7):
        candidate = week_start + timedelta(days=offset)
        if candidate < today:
            continue
        rollup = by_date.get(candidate)
        if rollup is not None:
            # A day already in the grid speaks for itself; only a running one is still owed.
            ahead += int(rollup.is_working_day and rollup.in_progress)
        elif candidate.weekday() not in off_weekdays and candidate not in holiday_dates:
            ahead += 1
    return ahead


# --- helpers --------------------------------------------------------------------------


def _rollup(
    day: AttendanceDay,
    policy: ShiftPolicy,
    analyses: dict[date, DayAnalysis],
    today: date | None = None,
) -> DayRollup:
    analysis = analyses.get(day.day)
    if analysis is not None and analysis.state is not DayState.EMPTY:
        worked, estimated = analysis.worked, False
    else:
        # Grid-only: Tot. Hrs. is the gross span, so subtract the break allowance to get a
        # comparable worked figure. Marked estimated so the UI can qualify the total.
        worked = _clamp(day.total_hours - policy.break_target)
        estimated = day.total_hours.minutes > 0

    # The portal marks some days present with no punches and no hours at all — swipe data
    # that simply does not exist. Scoring those as a full day short invents a deficit the
    # employee can neither verify nor act on, so they are excluded from the bank and
    # reported separately instead.
    unmeasured = _is_unmeasured(day, analysis)

    return DayRollup(
        day=day.day,
        worked=worked,
        status=day.status,
        estimated=estimated and not unmeasured,
        is_working_day=day.status.counts_as_worked and not unmeasured,
        unmeasured=unmeasured,
        in_progress=_is_in_progress(day, analysis, today),
    )


def _is_in_progress(day: AttendanceDay, analysis: DayAnalysis | None, today: date | None) -> bool:
    """Whether this is today and the working day has not finished yet.

    Clocked in with no out-punch. The grid reports no total for such a day, so without this
    it looks identical to a day someone worked zero hours.
    """
    if today is None or day.day != today:
        return False
    if analysis is not None:
        return analysis.state is DayState.INCOMPLETE
    return day.first_in is not None and day.last_out is None


def _is_unmeasured(day: AttendanceDay, analysis: DayAnalysis | None) -> bool:
    """Whether a nominally worked day carries nothing that can be measured."""
    if not day.status.counts_as_worked:
        return False
    if analysis is not None and analysis.state is not DayState.EMPTY:
        return False
    return (
        day.total_hours.minutes == 0
        and day.first_in is None
        and day.last_out is None
        and not day.punches
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
