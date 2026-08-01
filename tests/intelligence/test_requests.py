"""Duplicate detection and status-change detection for swipe requests.

Both exist because of what the portal's grid does *not* carry. There is no request id, no
filed date and no approver anywhere in it, so a duplicate can only be recognised by what a
request is for, and an approval can only be noticed by comparing two fetches. Neither is
knowable from a single snapshot.
"""

from __future__ import annotations

from datetime import date, time

from cerepulse.intelligence.attention import (
    decision_insight,
    duplicate_requests,
    status_changes,
)
from cerepulse.intelligence.insights import InsightKind, Severity
from cerepulse.models.swipe import SwipeRequest, SwipeStatus

DAY = date(2026, 7, 30)


def request(
    *,
    day: date = DAY,
    direction: str = "Out",
    status: SwipeStatus = SwipeStatus.IN_PROCESS,
    out: time | None = time(18, 30),
    remark: str = "",
) -> SwipeRequest:
    return SwipeRequest(
        for_date=day,
        direction=direction,
        in_time=None,
        out_time=out,
        status=status,
        remark=remark,
    )


# --- duplicates -------------------------------------------------------------------------


def test_two_live_requests_for_the_same_punch_are_a_duplicate() -> None:
    found = duplicate_requests([request(), request()])
    assert list(found) == [(DAY, "out")]
    assert len(found[(DAY, "out")]) == 2


def test_an_in_and_an_out_on_one_day_are_not_duplicates() -> None:
    """A day can legitimately need both punches corrected."""
    assert duplicate_requests([request(direction="In"), request(direction="Out")]) == {}


def test_refiling_after_a_rejection_is_not_a_duplicate() -> None:
    """It is the correct response to the rejection, not a copy of it."""
    assert duplicate_requests([request(status=SwipeStatus.REJECTED), request()]) == {}


def test_a_cancelled_request_does_not_count_against_its_replacement() -> None:
    assert duplicate_requests([request(status=SwipeStatus.CANCELLED), request()]) == {}


def test_the_mode_comparison_ignores_case_and_padding() -> None:
    assert duplicate_requests([request(direction="out"), request(direction=" Out ")])


# --- status changes ---------------------------------------------------------------------


def test_a_pending_request_becoming_approved_is_reported() -> None:
    before = [request(status=SwipeStatus.IN_PROCESS)]
    after = [request(status=SwipeStatus.APPROVED)]

    (change,) = status_changes(before, after)
    assert change.was is SwipeStatus.IN_PROCESS
    assert change.verb == "approved"


def test_a_rejection_is_reported_too() -> None:
    (change,) = status_changes(
        [request(status=SwipeStatus.IN_PROCESS)], [request(status=SwipeStatus.REJECTED)]
    )
    assert change.verb == "rejected"


def test_nothing_changing_reports_nothing() -> None:
    both = [request(status=SwipeStatus.APPROVED)]
    assert status_changes(both, both) == []


def test_a_newly_filed_request_is_not_a_change() -> None:
    """The user filed it, so they already know. Only a decision is news."""
    assert status_changes([], [request()]) == []


def test_a_request_that_vanishes_is_not_a_change() -> None:
    assert status_changes([request()], []) == []


def test_a_decision_is_only_reported_once() -> None:
    """The second sync sees the same approved row and must stay quiet."""
    approved = [request(status=SwipeStatus.APPROVED)]
    assert status_changes(approved, approved) == []


def test_requests_are_matched_by_what_they_are_for() -> None:
    """No id exists, so the date, the mode and the times are the only identity available."""
    before = [request(day=date(2026, 7, 29)), request(day=DAY)]
    after = [
        request(day=date(2026, 7, 29)),
        request(day=DAY, status=SwipeStatus.APPROVED),
    ]

    (change,) = status_changes(before, after)
    assert change.request.for_date == DAY


# --- the notification --------------------------------------------------------------------


def test_one_decision_names_the_day_and_the_punch() -> None:
    changes = status_changes(
        [request()], [request(status=SwipeStatus.APPROVED, remark="Forgot to swipe")]
    )
    insight = decision_insight(changes)

    assert insight is not None
    assert insight.kind is InsightKind.SWIPE_DECIDED
    assert insight.severity is Severity.SUCCESS
    assert "30 Jul" in insight.detail
    assert "Out" in insight.detail


def test_a_batch_becomes_one_insight_not_several() -> None:
    """Three toasts for one clearing of an approver's queue is three interruptions."""
    before = [request(day=date(2026, 7, d)) for d in (27, 28, 29)]
    after = [
        request(day=date(2026, 7, 27), status=SwipeStatus.APPROVED),
        request(day=date(2026, 7, 28), status=SwipeStatus.APPROVED),
        request(day=date(2026, 7, 29), status=SwipeStatus.REJECTED),
    ]

    insight = decision_insight(status_changes(before, after))

    assert insight is not None
    assert "3 swipe requests" in insight.title
    assert "2 approved" in insight.detail
    assert "1 rejected" in insight.detail
    # A rejection in the batch means it needs acting on, whatever else was approved.
    assert insight.severity is Severity.WARNING


def test_no_changes_produce_no_insight() -> None:
    assert decision_insight([]) is None
