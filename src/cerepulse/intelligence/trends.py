"""Trends across many months: habits, records, month-over-month, and a forecast.

Everything else in the intelligence layer looks at one day, one week or one month. This
module is the only part that needs history, which is what makes it the reason Part 2 exists.

Three decisions shape the whole file.

**Medians, not means.** One 3 AM deployment night should not become "your typical start is
half past ten". Every habit figure is a median for that reason, and each carries the sample
size it was computed from so the UI can say "across 14 days" instead of implying a law of
nature.

**Estimated days are counted, never hidden.** Only a day with punch detail gives true worked
time; the rest are ``gross span - break allowance``. A break figure is stricter still and
comes only from punch logs, because the grid contains nothing to derive it from. Every
report states its own footing via :attr:`Habits.measured_days` and
:attr:`TrendReport.estimated_days`.

**A record needs a floor.** "Your longest day" computed from three days is not a record, it
is the maximum of three numbers. Anything claiming to be personal history requires
:data:`MIN_SAMPLE`, and reports nothing rather than something flattering and meaningless.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta

from cerepulse.intelligence.day import DayAnalysis, DayState
from cerepulse.intelligence.month import week_start_for
from cerepulse.intelligence.policy import ShiftPolicy
from cerepulse.models.attendance import AttendanceDay
from cerepulse.models.values import Duration

#: Below this many measured days, no habit or record is reported at all.
MIN_SAMPLE = 5

#: How many recent working days count as "lately" when comparing against the norm.
RECENT_WINDOW = 10

#: A drift smaller than this is noise in when someone happens to reach the door.
DRIFT_THRESHOLD = Duration(15)


@dataclass(frozen=True, slots=True)
class DayFact:
    """One measurable working day, reduced to what trends need."""

    day: date
    worked: Duration
    first_in: time | None
    last_out: time | None
    #: Only ever set from a punch log. The grid has nothing to derive a break from.
    break_taken: Duration | None
    estimated: bool

    def met(self, policy: ShiftPolicy) -> bool:
        return self.worked >= policy.work_target


@dataclass(frozen=True, slots=True)
class WeekdayHabit:
    """What a given weekday usually looks like."""

    weekday: int  # Monday is 0
    typical_in: time | None
    typical_worked: Duration
    sample: int

    @property
    def name(self) -> str:
        return ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")[
            self.weekday
        ]


@dataclass(frozen=True, slots=True)
class Habits:
    """The shape of a normal working day for this person."""

    typical_in: time | None
    typical_out: time | None
    typical_worked: Duration | None
    typical_break: Duration | None
    #: Days that carried a punch log, which is all the break figure can be based on.
    break_sample: int
    weekdays: tuple[WeekdayHabit, ...]
    recent_in: time | None
    #: Recent in-time against the long-run norm. Positive means later than usual.
    in_time_drift: Duration | None
    measured_days: int

    @property
    def has_enough(self) -> bool:
        return self.measured_days >= MIN_SAMPLE

    @property
    def drifting(self) -> bool:
        return self.in_time_drift is not None and abs(self.in_time_drift.minutes) >= (
            DRIFT_THRESHOLD.minutes
        )


@dataclass(frozen=True, slots=True)
class DayRecord:
    """A single day that holds a record."""

    day: date
    value: str


@dataclass(frozen=True, slots=True)
class Records:
    """Streaks and personal bests."""

    #: Consecutive measured working days meeting target, counting back from the latest.
    current_streak: int
    best_streak: int
    days_since_short: int | None
    longest_day: DayRecord | None
    earliest_start: DayRecord | None
    best_week: DayRecord | None
    measured_days: int

    @property
    def has_enough(self) -> bool:
        return self.measured_days >= MIN_SAMPLE


@dataclass(frozen=True, slots=True)
class MonthSummary:
    """One month, for comparison against its neighbours."""

    year: int
    month: int
    worked: Duration
    target: Duration
    overtime: Duration
    short_days: int
    working_days: int
    average_in: time | None
    estimated_days: int

    @property
    def delta(self) -> Duration:
        return self.worked - self.target

    @property
    def label(self) -> str:
        return date(self.year, self.month, 1).strftime("%b %Y")

    @property
    def daily_average(self) -> Duration | None:
        if not self.working_days:
            return None
        return Duration(round(self.worked.minutes / self.working_days))


@dataclass(frozen=True, slots=True)
class Forecast:
    """Where the current month lands if nothing changes."""

    #: Bank position projected to month end, assuming remaining days match the recent pace.
    projected_delta: Duration | None
    #: What each remaining day has to be to finish level. None when already level or ahead.
    required_daily: Duration | None
    #: The pace the projection assumes — the recent median, so the user can sanity-check it.
    assumed_daily: Duration | None
    working_days_remaining: int
    #: Whether one short day this week can be absorbed by the surplus already banked.
    short_day_affordable: bool
    #: How much surplus exists to spend. Negative means there is none.
    headroom: Duration


@dataclass(frozen=True, slots=True)
class TrendReport:
    """Everything the Insights screen shows."""

    habits: Habits
    records: Records
    months: tuple[MonthSummary, ...]
    forecast: Forecast | None
    measured_days: int
    estimated_days: int
    span: tuple[date, date] | None

    @property
    def exact_days(self) -> int:
        return self.measured_days - self.estimated_days

    @property
    def latest(self) -> MonthSummary | None:
        return self.months[-1] if self.months else None

    @property
    def previous(self) -> MonthSummary | None:
        return self.months[-2] if len(self.months) > 1 else None


def analyze_trends(
    days: Sequence[AttendanceDay],
    *,
    policy: ShiftPolicy | None = None,
    analyses: dict[date, DayAnalysis] | None = None,
    today: date | None = None,
    working_days_remaining: int = 0,
) -> TrendReport:
    """Build the full report from every cached day, across as many months as there are."""
    policy = policy or ShiftPolicy.default()
    facts = build_facts(days, policy=policy, analyses=analyses)

    return TrendReport(
        habits=analyze_habits(facts),
        records=analyze_records(facts, policy=policy),
        months=tuple(summarize_months(facts, policy=policy)),
        forecast=forecast(
            facts,
            policy=policy,
            today=today or date.today(),
            working_days_remaining=working_days_remaining,
        ),
        measured_days=len(facts),
        estimated_days=sum(1 for fact in facts if fact.estimated),
        span=(facts[0].day, facts[-1].day) if facts else None,
    )


# --- facts ------------------------------------------------------------------------------


def build_facts(
    days: Sequence[AttendanceDay],
    *,
    policy: ShiftPolicy,
    analyses: dict[date, DayAnalysis] | None = None,
) -> list[DayFact]:
    """Reduce raw days to the measurable working ones, oldest first.

    Days the portal marks worked but holds nothing for are dropped rather than scored as
    zero. Averaging them in would drag every habit downwards and invent a deficit the
    employee can neither verify nor act on — the same reasoning as
    :attr:`~cerepulse.intelligence.month.MonthAnalysis.unmeasured_days`.
    """
    analyses = analyses or {}
    facts: list[DayFact] = []

    for day in sorted(days, key=lambda item: item.day):
        if not day.status.counts_as_worked:
            continue

        analysis = analyses.get(day.day)
        exact = analysis is not None and analysis.state is DayState.COMPLETE
        if exact:
            assert analysis is not None
            worked, break_taken = analysis.worked, analysis.break_taken
        else:
            worked, break_taken = _clamp(day.total_hours - policy.break_target), None

        if worked.minutes <= 0:
            continue

        facts.append(
            DayFact(
                day=day.day,
                worked=worked,
                first_in=day.first_in,
                last_out=day.last_out,
                break_taken=break_taken,
                estimated=not exact,
            )
        )
    return facts


# --- habits -----------------------------------------------------------------------------


def analyze_habits(facts: Sequence[DayFact]) -> Habits:
    """What a normal day looks like, and whether lately has been normal."""
    breaks = [fact.break_taken for fact in facts if fact.break_taken is not None]
    recent = facts[-RECENT_WINDOW:]

    typical_in = _median_time(fact.first_in for fact in facts)
    recent_in = _median_time(fact.first_in for fact in recent)
    drift = None
    if typical_in is not None and recent_in is not None and len(facts) > len(recent):
        drift = Duration(_minutes(recent_in) - _minutes(typical_in))

    return Habits(
        typical_in=typical_in,
        typical_out=_median_time(fact.last_out for fact in facts),
        typical_worked=_median_duration(fact.worked for fact in facts),
        typical_break=_median_duration(breaks) if breaks else None,
        break_sample=len(breaks),
        weekdays=tuple(_weekday_habits(facts)),
        recent_in=recent_in,
        in_time_drift=drift,
        measured_days=len(facts),
    )


def _weekday_habits(facts: Sequence[DayFact]) -> list[WeekdayHabit]:
    grouped: dict[int, list[DayFact]] = {}
    for fact in facts:
        grouped.setdefault(fact.day.weekday(), []).append(fact)

    habits = []
    for weekday in sorted(grouped):
        same = grouped[weekday]
        habits.append(
            WeekdayHabit(
                weekday=weekday,
                typical_in=_median_time(fact.first_in for fact in same),
                typical_worked=_median_duration(fact.worked for fact in same) or Duration(0),
                sample=len(same),
            )
        )
    return habits


# --- records ----------------------------------------------------------------------------


def analyze_records(facts: Sequence[DayFact], *, policy: ShiftPolicy) -> Records:
    """Streaks and bests. Everything here is bounded by what was actually measured."""
    met = [fact.met(policy) for fact in facts]

    current = 0
    for hit in reversed(met):
        if not hit:
            break
        current += 1

    best = run = 0
    for hit in met:
        run = run + 1 if hit else 0
        best = max(best, run)

    since_short = None
    for index, hit in enumerate(reversed(met)):
        if not hit:
            since_short = index
            break

    return Records(
        current_streak=current,
        best_streak=best,
        days_since_short=since_short,
        longest_day=_longest_day(facts),
        earliest_start=_earliest_start(facts),
        best_week=_best_week(facts),
        measured_days=len(facts),
    )


def _longest_day(facts: Sequence[DayFact]) -> DayRecord | None:
    if not facts:
        return None
    best = max(facts, key=lambda fact: fact.worked.minutes)
    return DayRecord(best.day, str(best.worked))


def _earliest_start(facts: Sequence[DayFact]) -> DayRecord | None:
    starts = [fact for fact in facts if fact.first_in is not None]
    if not starts:
        return None
    best = min(starts, key=lambda fact: _minutes(fact.first_in))
    return DayRecord(best.day, _clock(best.first_in))


def _best_week(facts: Sequence[DayFact]) -> DayRecord | None:
    """The week with the most hours worked, keyed on its Monday."""
    weeks: dict[date, int] = {}
    for fact in facts:
        weeks[week_start_for(fact.day)] = (
            weeks.get(week_start_for(fact.day), 0) + fact.worked.minutes
        )
    if not weeks:
        return None
    start = max(weeks, key=lambda key: weeks[key])
    return DayRecord(start, str(Duration(weeks[start])))


# --- month over month -------------------------------------------------------------------


def summarize_months(facts: Sequence[DayFact], *, policy: ShiftPolicy) -> list[MonthSummary]:
    """One summary per month present in the facts, oldest first."""
    grouped: dict[tuple[int, int], list[DayFact]] = {}
    for fact in facts:
        grouped.setdefault((fact.day.year, fact.day.month), []).append(fact)

    summaries = []
    for (year, month), same in sorted(grouped.items()):
        worked = _sum(fact.worked for fact in same)
        summaries.append(
            MonthSummary(
                year=year,
                month=month,
                worked=worked,
                target=Duration(len(same) * policy.work_target.minutes),
                overtime=_sum(
                    _clamp(fact.worked - policy.work_target)
                    for fact in same
                    if fact.worked > policy.work_target
                ),
                short_days=sum(1 for fact in same if not fact.met(policy)),
                working_days=len(same),
                average_in=_median_time(fact.first_in for fact in same),
                estimated_days=sum(1 for fact in same if fact.estimated),
            )
        )
    return summaries


# --- forecast ---------------------------------------------------------------------------


def forecast(
    facts: Sequence[DayFact],
    *,
    policy: ShiftPolicy,
    today: date,
    working_days_remaining: int,
) -> Forecast | None:
    """Where this month lands if the recent pace holds.

    Deliberately projects from the **recent median**, not the month's average: someone who
    has picked up their pace in the last fortnight is not well described by a figure that
    includes their slow start, and the assumption is reported alongside the projection so it
    can be judged rather than trusted.
    """
    this_month = [
        fact for fact in facts if (fact.day.year, fact.day.month) == (today.year, today.month)
    ]
    if not this_month:
        return None

    worked = _sum(fact.worked for fact in this_month)
    elapsed_target = Duration(len(this_month) * policy.work_target.minutes)
    banked = worked - elapsed_target

    assumed = _median_duration(fact.worked for fact in facts[-RECENT_WINDOW:])
    projected = None
    if assumed is not None:
        future = (assumed.minutes - policy.work_target.minutes) * working_days_remaining
        projected = Duration(banked.minutes + future)

    required = None
    if banked.minutes < 0 and working_days_remaining > 0:
        needed = policy.work_target.minutes * working_days_remaining - banked.minutes
        required = Duration(-(-needed // working_days_remaining))  # ceil

    # "Affordable" means a day of half the target could be taken and the month would still
    # finish level — not merely that some surplus exists.
    cost = policy.work_target.minutes // 2
    return Forecast(
        projected_delta=projected,
        required_daily=required,
        assumed_daily=assumed,
        working_days_remaining=working_days_remaining,
        short_day_affordable=banked.minutes >= cost,
        headroom=banked,
    )


# --- helpers ----------------------------------------------------------------------------


def _median_time(values: Iterable[time | None]) -> time | None:
    minutes = [_minutes(value) for value in values if value is not None]
    if not minutes:
        return None
    middle = round(statistics.median(minutes))
    return time(middle // 60 % 24, middle % 60)


def _median_duration(values: Iterable[Duration]) -> Duration | None:
    minutes = [value.minutes for value in values]
    if not minutes:
        return None
    return Duration(round(statistics.median(minutes)))


def _minutes(value: time | None) -> int:
    return 0 if value is None else value.hour * 60 + value.minute


def _clock(value: time | None) -> str:
    if value is None:
        return "--:--"
    return value.strftime("%I:%M %p").lstrip("0")


def _sum(values: Iterable[Duration]) -> Duration:
    total = Duration(0)
    for value in values:
        total = total + value
    return total


def _clamp(duration: Duration) -> Duration:
    return duration if duration.minutes > 0 else Duration(0)


def working_days_left(today: date, *, off_weekdays: set[int], holidays: set[date]) -> int:
    """Working days after ``today`` in its month, from the employee's own off-day pattern."""
    from calendar import monthrange

    _, last = monthrange(today.year, today.month)
    remaining = 0
    cursor = today + timedelta(days=1)
    while cursor <= date(today.year, today.month, last):
        if cursor.weekday() not in off_weekdays and cursor not in holidays:
            remaining += 1
        cursor += timedelta(days=1)
    return remaining


__all__ = [
    "DRIFT_THRESHOLD",
    "MIN_SAMPLE",
    "RECENT_WINDOW",
    "DayFact",
    "DayRecord",
    "Forecast",
    "Habits",
    "MonthSummary",
    "Records",
    "TrendReport",
    "WeekdayHabit",
    "analyze_habits",
    "analyze_records",
    "analyze_trends",
    "build_facts",
    "forecast",
    "summarize_months",
    "working_days_left",
]
