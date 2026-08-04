"""Observations about habits rather than about a day's arithmetic.

These exist because the app was almost silent. Most insight kinds either never notify or need
a fifteen-minute tick to land inside a narrow window — `ON_TRACK` requires being clocked in
*and* already over target — so a whole week could pass without a word, and the one
notification that did fire was not a real toast and left no trace.
"""

from __future__ import annotations

from datetime import date, datetime, time

from cerepulse.intelligence.day import analyze_day
from cerepulse.intelligence.insights import InsightKind
from cerepulse.intelligence.nudges import (
    BREAK_OVERDUE,
    LEAVE_LOOKBACK_DAYS,
    LEAVE_STALE_DAYS,
    day_nudges,
    leave_nudges,
)
from cerepulse.models.attendance import AttendanceDay, DayStatus, Punch, PunchDirection
from cerepulse.models.values import Duration

DAY = date(2026, 7, 28)


def at(clock: str) -> datetime:
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime(2026, 7, 28, hour, minute)


def punches(*pairs: tuple[str, str]) -> list[Punch]:
    out = []
    for clock, direction in pairs:
        hour, minute = (int(part) for part in clock.split(":"))
        out.append(Punch(at=time(hour, minute), direction=PunchDirection.parse(direction)))
    return out


def kinds(insights: list[object]) -> set[InsightKind]:
    return {item.kind for item in insights}  # type: ignore[attr-defined]


def muster(when: date, status: DayStatus = DayStatus.PRESENT) -> AttendanceDay:
    return AttendanceDay(
        day=when,
        weekday=when.strftime("%a"),
        status=status,
        total_hours=Duration(540),
    )


# --- no break yet -----------------------------------------------------------------------


def test_four_hours_at_the_desk_earns_a_word() -> None:
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=at("13:30"))
    assert kinds(day_nudges(analysis, now=at("13:30"))) == {InsightKind.NO_BREAK_YET}


def test_an_hour_in_does_not() -> None:
    """A nudge that arrives before it could possibly be warranted is the fastest route to
    someone switching notifications off."""
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=at("10:00"))
    assert day_nudges(analysis, now=at("10:00")) == []


def test_a_break_already_taken_stops_it() -> None:
    analysis = analyze_day(
        punches(("09:00", "in"), ("12:00", "out"), ("12:45", "in")), day=DAY, now=at("15:00")
    )
    assert day_nudges(analysis, now=at("15:00")) == []


def test_a_two_minute_gap_does_not_count_as_a_break() -> None:
    """Otherwise a walk to the kettle would satisfy it and the nudge would never fire."""
    analysis = analyze_day(
        punches(("09:00", "in"), ("11:00", "out"), ("11:02", "in")), day=DAY, now=at("14:00")
    )
    assert kinds(day_nudges(analysis, now=at("14:00"))) == {InsightKind.NO_BREAK_YET}


def test_a_finished_day_is_never_nudged() -> None:
    """Telling somebody who went home two hours ago to take a break is advice about a
    decision already made."""
    analysis = analyze_day(punches(("09:00", "in"), ("18:00", "out")), day=DAY)
    assert day_nudges(analysis, now=at("20:00")) == []


def test_being_on_a_break_right_now_is_not_nudged() -> None:
    """The last punch is an Out, so they are away from the desk — which is the thing being
    asked for."""
    analysis = analyze_day(punches(("09:00", "in"), ("14:00", "out")), day=DAY, now=at("14:10"))
    assert day_nudges(analysis, now=at("14:10")) == []


def test_the_threshold_is_where_it_says_it_is() -> None:
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=at("13:00"))
    just_under = at("09:00") + __import__("datetime").timedelta(minutes=BREAK_OVERDUE.minutes - 1)
    assert day_nudges(analysis, now=just_under) == []


# --- no leave in a long time ------------------------------------------------------------
#
# Read from the muster rather than the leave ledger, and that is not a stylistic choice. The
# obvious source is the ledger's `consumed_days`, and against this portal it does not work:
# every row it writes is a credit and `consumed_days` is 0.0 on all of them, for every leave
# type, across the whole year. A nudge built on it stays silent forever and looks exactly
# like a threshold set too high.


def test_a_long_stretch_without_leave_is_mentioned() -> None:
    days = [muster(date(2026, 1, 5), DayStatus.LEAVE), muster(date(2026, 3, 2))]
    assert kinds(leave_nudges(days, today=date(2026, 7, 28))) == {InsightKind.LEAVE_UNUSED}


def test_leave_taken_recently_says_nothing() -> None:
    days = [muster(date(2026, 7, 1), DayStatus.LEAVE)]
    assert leave_nudges(days, today=date(2026, 7, 28)) == []


def test_a_half_day_counts_as_a_day_off() -> None:
    days = [muster(date(2026, 7, 10), DayStatus.HALF_DAY)]
    assert leave_nudges(days, today=date(2026, 7, 28)) == []


def test_the_most_recent_day_off_is_the_one_that_counts() -> None:
    days = [
        muster(date(2026, 1, 5), DayStatus.LEAVE),
        muster(date(2026, 7, 20), DayStatus.LEAVE),
    ]
    assert leave_nudges(days, today=date(2026, 7, 28)) == []


def test_a_history_with_no_days_off_says_nothing() -> None:
    """The history the app holds is finite, and the difference between "you have not taken
    leave in a year" and "the cache only goes back three months" is not one this can tell."""
    assert leave_nudges([], today=date(2026, 7, 28)) == []
    assert leave_nudges([muster(date(2026, 3, 2))], today=date(2026, 7, 28)) == []


def test_leave_booked_for_the_future_does_not_count_as_taken() -> None:
    """A day off next month is not a day off that has happened."""
    days = [
        muster(date(2026, 1, 5), DayStatus.LEAVE),
        muster(date(2026, 9, 1), DayStatus.LEAVE),
    ]
    assert kinds(leave_nudges(days, today=date(2026, 7, 28))) == {InsightKind.LEAVE_UNUSED}


def test_the_threshold_is_a_season_not_a_month() -> None:
    """A nudge that arrives monthly is a nag, and a nag gets switched off."""
    assert LEAVE_STALE_DAYS >= 60


def test_the_lookback_outruns_the_threshold() -> None:
    """Otherwise the window itself causes the silence it is meant to detect."""
    assert LEAVE_LOOKBACK_DAYS > LEAVE_STALE_DAYS
