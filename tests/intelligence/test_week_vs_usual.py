"""This week against the baseline — the section that cannot go stale.

What these pin is restraint as much as arithmetic: small wobbles read as "as usual" rather
than as news, missing baselines produce no delta rather than a zero, and estimated figures
say so.
"""

from __future__ import annotations

from datetime import date, time

from cerepulse.intelligence.trends import DayFact, Habits, WeekdayHabit
from cerepulse.intelligence.week_vs_usual import (
    NOTABLE,
    compare_week,
    describe,
)
from cerepulse.models.values import Duration

MONDAY = date(2026, 8, 3)


def fact(
    day: date,
    *,
    worked: int = 8 * 60,
    first_in: time | None = time(9, 10),
    break_taken: int | None = 45,
    estimated: bool = False,
) -> DayFact:
    return DayFact(
        day=day,
        worked=Duration(worked),
        first_in=first_in,
        last_out=None,
        break_taken=Duration(break_taken) if break_taken is not None else None,
        estimated=estimated,
    )


def habits(*, measured: int = 40, typical_in: time | None = time(9, 10)) -> Habits:
    weekdays = tuple(
        WeekdayHabit(
            weekday=index, typical_in=typical_in, typical_worked=Duration(8 * 60), sample=8
        )
        for index in range(5)
    )
    return Habits(
        typical_in=typical_in,
        typical_out=time(18, 30),
        typical_worked=Duration(8 * 60),
        typical_break=Duration(45),
        break_sample=30,
        weekdays=weekdays,
        recent_in=typical_in,
        in_time_drift=None,
        measured_days=measured,
    )


def test_a_late_start_is_measured_against_that_weekdays_own_baseline() -> None:
    week = compare_week(
        [fact(MONDAY, first_in=time(9, 40))],
        habits(),
        week_start=MONDAY,
        today=MONDAY,
    )
    (monday,) = week.days
    assert monday.start_delta == Duration(30)
    assert "30m later than usual" in describe(monday)


def test_a_small_wobble_reads_as_usual_not_as_news() -> None:
    """±4 minutes reported as a delta teaches the reader to skim the section — the exact
    fate the old page met."""
    week = compare_week(
        [fact(MONDAY, first_in=time(9, 14))], habits(), week_start=MONDAY, today=MONDAY
    )
    (monday,) = week.days
    assert abs(monday.start_delta.minutes) < NOTABLE.minutes
    assert "started as usual" in describe(monday)
    assert not monday.is_notable


def test_overtime_shows_signed_against_the_usual_day() -> None:
    week = compare_week(
        [fact(MONDAY, worked=8 * 60 + 50)], habits(), week_start=MONDAY, today=MONDAY
    )
    line = describe(week.days[0])
    assert "+50m vs usual" in line


def test_a_missing_baseline_is_no_delta_not_a_zero() -> None:
    """A first week of history has nothing to compare against, and pretending otherwise
    would report every day as wildly unusual."""
    bare = Habits(
        typical_in=None,
        typical_out=None,
        typical_worked=None,
        typical_break=None,
        break_sample=0,
        weekdays=(),
        recent_in=None,
        in_time_drift=None,
        measured_days=3,
    )
    week = compare_week([fact(MONDAY)], bare, week_start=MONDAY, today=MONDAY)
    (monday,) = week.days
    assert monday.start_delta is None
    assert monday.worked_delta is None
    assert not week.has_baseline


def test_days_outside_the_week_or_after_today_are_absent() -> None:
    week = compare_week(
        [
            fact(MONDAY),
            fact(date(2026, 7, 31)),  # last week
            fact(date(2026, 8, 5)),  # after "today"
        ],
        habits(),
        week_start=MONDAY,
        today=date(2026, 8, 4),
    )
    assert [comparison.day for comparison in week.days] == [MONDAY]


def test_an_overrun_break_is_measured_against_the_median() -> None:
    week = compare_week([fact(MONDAY, break_taken=75)], habits(), week_start=MONDAY, today=MONDAY)
    assert "break 30m over your median" in describe(week.days[0])


def test_an_estimated_day_says_so() -> None:
    """The same rule the voice engine follows: figures repaired from the grid are never
    presented with the confidence of measured ones."""
    week = compare_week(
        [fact(MONDAY, estimated=True, break_taken=None)],
        habits(),
        week_start=MONDAY,
        today=MONDAY,
    )
    assert "estimated from the grid" in describe(week.days[0])


def test_the_baseline_size_is_carried_for_the_screen_to_state() -> None:
    week = compare_week([fact(MONDAY)], habits(measured=40), week_start=MONDAY, today=MONDAY)
    assert week.baseline_days == 40
    assert week.has_baseline
