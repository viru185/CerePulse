"""Which days need something doing about them.

The bar is deliberately high. A screen that flags a third of the month teaches people to
ignore the flag, so most of these tests are about what does *not* qualify.
"""

from __future__ import annotations

from datetime import date, time

from cerepulse.intelligence.attention import (
    AttentionKind,
    find_attention,
    swipe_index,
    swipe_state,
)
from cerepulse.intelligence.day import analyze_day
from cerepulse.models.attendance import AttendanceDay, DayStatus, Punch, PunchDirection
from cerepulse.models.swipe import SwipeRequest, SwipeStatus
from cerepulse.models.values import Duration

DAY = date(2026, 7, 14)
TOMORROW = date(2026, 7, 15)


def workday(
    when: date = DAY,
    *,
    total: str = "9.00",
    status: DayStatus = DayStatus.PRESENT,
    first_in: str | None = "09:00",
    last_out: str | None = "18:00",
    punches: tuple[Punch, ...] = (),
) -> AttendanceDay:
    return AttendanceDay(
        day=when,
        weekday=when.strftime("%a"),
        status=status,
        first_in=_time(first_in),
        last_out=_time(last_out),
        total_hours=Duration.from_hhmm(total),
        punches=punches,
        detail_loaded=bool(punches),
    )


def _time(text: str | None) -> time | None:
    if text is None:
        return None
    hour, minute = (int(part) for part in text.split(":"))
    return time(hour, minute)


def request(status: SwipeStatus, *, when: date = DAY, direction: str = "In") -> SwipeRequest:
    return SwipeRequest(
        for_date=when,
        direction=direction,
        in_time=None,
        out_time=None,
        remark="",
        status=status,
    )


def found(days: list[AttendanceDay], **kwargs: object) -> dict[date, AttentionKind]:
    result = find_attention(days, today=TOMORROW, **kwargs)  # type: ignore[arg-type]
    return {day: attention.kind for day, attention in result.items()}


# --- what qualifies ---------------------------------------------------------------------


def test_a_short_day_with_no_request_is_outstanding() -> None:
    assert found([workday(total="7.00")]) == {DAY: AttentionKind.SHORT_NO_REQUEST}


def test_a_day_the_portal_holds_nothing_for_is_outstanding() -> None:
    day = workday(total="0.00", first_in=None, last_out=None)
    assert found([day]) == {DAY: AttentionKind.UNMEASURED}


def test_a_missing_punch_is_outstanding_even_when_the_hours_look_fine() -> None:
    """The hours look fine because they were inferred; that is the thing to fix."""
    punches = (
        Punch(at=time(9, 0), direction=PunchDirection.IN),
        Punch(at=time(12, 0), direction=PunchDirection.IN),
        Punch(at=time(19, 0), direction=PunchDirection.OUT),
    )
    day = workday(punches=punches, total="10.00")
    analyses = {DAY: analyze_day(list(punches), day=DAY)}

    assert found([day], analyses=analyses) == {DAY: AttentionKind.MISSING_PUNCH}


def test_a_rejected_request_is_outstanding_again() -> None:
    swipes = swipe_index([request(SwipeStatus.REJECTED)])
    assert found([workday(total="7.00")], swipes=swipes) == {DAY: AttentionKind.REQUEST_REJECTED}


# --- what does not ----------------------------------------------------------------------


def test_a_full_day_is_not_outstanding() -> None:
    assert found([workday()]) == {}


def test_a_day_a_minute_short_is_not_worth_a_swipe_request() -> None:
    """Live data threw one up. Flagging it red trains the eye to skip the flag."""
    assert found([workday(total="8.59")]) == {}


def test_a_day_well_short_still_is() -> None:
    assert found([workday(total="8.30")]) == {DAY: AttentionKind.SHORT_NO_REQUEST}


def test_a_short_day_with_a_pending_request_is_handled_not_outstanding() -> None:
    swipes = swipe_index([request(SwipeStatus.IN_PROCESS)])
    assert found([workday(total="7.00")], swipes=swipes) == {}


def test_an_approved_request_settles_it_too() -> None:
    swipes = swipe_index([request(SwipeStatus.APPROVED)])
    assert found([workday(total="7.00")], swipes=swipes) == {}


def test_a_cancelled_request_leaves_the_shortfall_the_users_problem() -> None:
    swipes = swipe_index([request(SwipeStatus.CANCELLED)])
    assert found([workday(total="7.00")], swipes=swipes) == {DAY: AttentionKind.SHORT_NO_REQUEST}


def test_weekends_holidays_and_leave_are_never_outstanding() -> None:
    days = [
        workday(date(2026, 7, 11), status=DayStatus.WEEKLY_OFF, total="0.00"),
        workday(date(2026, 7, 12), status=DayStatus.HOLIDAY, total="0.00"),
        workday(date(2026, 7, 13), status=DayStatus.LEAVE, total="0.00"),
    ]
    assert found(days) == {}


def test_today_is_unfinished_not_short() -> None:
    """Flagging today at lunchtime would put a red mark on every live day."""
    result = find_attention([workday(total="3.00")], today=DAY)
    assert result == {}


def test_tomorrow_has_not_happened_yet() -> None:
    result = find_attention([workday(TOMORROW, total="0.00")], today=DAY)
    assert result == {}


# --- one reason per day -----------------------------------------------------------------


def test_a_day_with_two_problems_is_listed_once() -> None:
    """Listing it twice would overstate how much of the month is broken."""
    punches = (
        Punch(at=time(9, 0), direction=PunchDirection.IN),
        Punch(at=time(12, 0), direction=PunchDirection.IN),
        Punch(at=time(14, 0), direction=PunchDirection.OUT),
    )
    day = workday(punches=punches, total="5.00")
    analyses = {DAY: analyze_day(list(punches), day=DAY)}
    result = find_attention([day], analyses=analyses, today=TOMORROW)

    assert len(result) == 1
    assert result[DAY].kind is AttentionKind.MISSING_PUNCH


def test_a_rejection_outranks_everything_else() -> None:
    day = workday(total="0.00", first_in=None, last_out=None)
    swipes = swipe_index([request(SwipeStatus.REJECTED)])
    assert found([day], swipes=swipes) == {DAY: AttentionKind.REQUEST_REJECTED}


# --- the swipe column -------------------------------------------------------------------


def test_requests_are_grouped_by_the_day_they_are_for() -> None:
    grouped = swipe_index(
        [
            request(SwipeStatus.APPROVED, direction="In"),
            request(SwipeStatus.IN_PROCESS, direction="Out"),
            request(SwipeStatus.APPROVED, when=TOMORROW),
        ]
    )
    assert len(grouped[DAY]) == 2
    assert len(grouped[TOMORROW]) == 1


def test_a_day_shows_the_status_that_needs_acting_on() -> None:
    """An approved In next to a rejected Out is not an approved day."""
    assert (
        swipe_state([request(SwipeStatus.APPROVED), request(SwipeStatus.REJECTED)])
        is SwipeStatus.REJECTED
    )
    assert (
        swipe_state([request(SwipeStatus.APPROVED), request(SwipeStatus.IN_PROCESS)])
        is SwipeStatus.IN_PROCESS
    )


def test_a_day_with_no_requests_has_no_state() -> None:
    assert swipe_state([]) is None
