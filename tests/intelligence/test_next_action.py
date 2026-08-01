"""The one instruction Today leads with, and the presence it is derived from.

The case that motivated all of this: a day whose last punch is an Out means one thing at
one o'clock and the opposite thing at seven. Reading it the same way both times is what made
the app announce an early exit and demand a swipe request in the middle of lunch.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from cerepulse.intelligence.day import DayState, analyze_day
from cerepulse.intelligence.insights import ActionKind, Severity
from cerepulse.intelligence.next_action import NextActionKind, Presence, presence_of
from cerepulse.models.swipe import SwipeRequest, SwipeStatus
from tests.intelligence.conftest import DAY, at, punches

PAST = DAY - timedelta(days=3)


def analyse(*pairs: tuple[str, str], now: str | None = None, day: date = DAY) -> object:
    return analyze_day(
        punches(*pairs), day=day, now=at(now).replace(year=day.year, month=day.month, day=day.day)
    )


def today(*pairs: tuple[str, str], now: str) -> object:
    """A day being lived: ``now`` falls on the same date being analysed."""
    return analyze_day(punches(*pairs), day=DAY, now=at(now))


def finished(*pairs: tuple[str, str]) -> object:
    """A day in the past: ``now`` is a later date, so nothing about it is in progress."""
    return analyze_day(punches(*pairs), day=PAST, now=at("18:00"))


# --- presence ---------------------------------------------------------------------------


def test_clocked_in_is_working() -> None:
    analysis = today(("09:00", "in"), now="11:30")
    assert presence_of(analysis, is_today=True) is Presence.WORKING


def test_clocked_out_at_lunch_is_a_break_not_a_finished_day() -> None:
    """The bug in one line: 9 to 1 with four hours still owed is lunch, not going home."""
    analysis = today(("09:00", "in"), ("13:00", "out"), now="13:20")

    assert presence_of(analysis, is_today=True) is Presence.ON_BREAK
    assert analysis.state is DayState.INCOMPLETE
    assert not analysis.early_exit
    assert not analysis.swipe_request_needed


def test_the_same_punches_on_a_past_day_are_a_short_day() -> None:
    """Identical log, different date. Only then can the app call it an early exit."""
    analysis = finished(("09:00", "in"), ("13:00", "out"))

    assert presence_of(analysis, is_today=False) is Presence.FINISHED
    assert analysis.early_exit
    assert analysis.swipe_request_needed


def test_clocked_out_with_the_target_met_is_done_even_today() -> None:
    analysis = today(("09:00", "in"), ("18:30", "out"), now="18:40")
    assert presence_of(analysis, is_today=True) is Presence.FINISHED
    assert analysis.state is DayState.COMPLETE


def test_no_punches_at_all_has_not_started() -> None:
    analysis = today(now="09:10")
    assert presence_of(analysis, is_today=True) is Presence.NOT_STARTED


# --- the instruction --------------------------------------------------------------------


def test_an_empty_today_asks_you_to_clock_in() -> None:
    assert today(now="08:40").next_action.kind is NextActionKind.CLOCK_IN


def test_working_with_hours_left_names_the_time_you_are_free() -> None:
    action = today(("09:00", "in"), now="11:00").next_action

    assert action.kind is NextActionKind.KEEP_WORKING
    assert "6:00 PM" in action.headline
    assert action.at is not None


def test_working_past_the_target_says_you_can_leave() -> None:
    action = today(("08:00", "in"), now="17:30").next_action

    assert action.kind is NextActionKind.FREE_TO_GO
    assert action.severity is Severity.SUCCESS


def test_a_break_names_the_time_it_stops_being_free() -> None:
    """Back by last-out plus what is left of the allowance, not by some other number."""
    analysis = today(("09:00", "in"), ("12:30", "out"), now="12:45")
    action = analysis.next_action

    assert action.kind is NextActionKind.RETURN_FROM_BREAK
    assert action.at == analysis.last_out + timedelta(minutes=analysis.break_remaining.minutes)


def test_a_break_past_the_allowance_warns_that_every_minute_costs() -> None:
    """Ninety minutes of breaks against a one-hour allowance: the finish time is moving."""
    action = today(
        ("09:00", "in"), ("11:00", "out"), ("12:30", "in"), ("13:00", "out"), now="14:30"
    ).next_action

    assert action.kind is NextActionKind.RETURN_FROM_BREAK
    assert action.severity is Severity.WARNING


def test_a_short_finished_day_offers_the_swipe_request() -> None:
    action = finished(("09:00", "in"), ("13:00", "out")).next_action

    assert action.kind is NextActionKind.FILE_SWIPE_REQUEST
    assert action.action is not None
    assert action.action.kind is ActionKind.OPEN_SWIPE_REQUEST


def test_an_already_filed_request_is_not_asked_for_again() -> None:
    analysis = analyze_day(
        punches(("09:00", "in"), ("13:00", "out")),
        day=PAST,
        now=at("18:00"),
        swipe_requests=[
            SwipeRequest(
                for_date=PAST,
                direction="Out",
                in_time=None,
                out_time=None,
                status=SwipeStatus.IN_PROCESS,
                remark="Forgot",
            )
        ],
    )
    assert analysis.next_action.kind is not NextActionKind.FILE_SWIPE_REQUEST


def test_a_missing_punch_outranks_the_shortfall_it_caused() -> None:
    """The shortfall is derived from a reconstruction, so fixing the log comes first."""
    action = finished(("09:00", "in"), ("11:00", "in"), ("12:00", "out")).next_action

    assert action.kind is NextActionKind.CHECK_PUNCHES
    assert action.severity is Severity.WARNING


def test_a_full_day_asks_for_nothing() -> None:
    action = finished(("09:00", "in"), ("18:30", "out")).next_action

    assert action.kind is NextActionKind.NOTHING_TO_DO
    assert action.action is None


# --- completion -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("out", "expected"),
    [("12:00", 0.5), ("16:00", 1.0), ("20:00", 1.5)],
)
def test_completion_is_worked_over_target_and_is_not_clamped(out: str, expected: float) -> None:
    """A bar pinned at 100% cannot tell a day that finished on time from one three hours over."""
    analysis = analyze_day(punches(("08:00", "in"), (out, "out")), day=PAST, now=at("23:00"))
    assert analysis.completion == pytest.approx(expected, abs=0.02)


def test_completion_of_an_empty_day_is_zero() -> None:
    assert analyze_day([], day=PAST, now=at("23:00")).completion == 0.0
