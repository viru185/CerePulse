"""Parsing the leave, outdoor-duty and comp-off application lists.

Nothing read these before 0.10, so the Records timeline could say a June week *was* outdoor
duty — the muster records that much — but not whether the request behind it was ever
approved. The columns here were taken from live captures of all three lists rather than from
the vendor's documentation, which describes none of them.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from cerepulse.core.errors import ParserError
from cerepulse.models.application import ApplicationKind, RequestStatus
from cerepulse.parsers.applications import STATUS_SELECT_ID, parse_applications

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def outdoor_html() -> str:
    return (FIXTURES / "outdoor_duty_list.html").read_text(encoding="utf-8")


@pytest.fixture
def leave_html() -> str:
    return (FIXTURES / "leave_applications.html").read_text(encoding="utf-8")


def test_every_row_becomes_an_application(outdoor_html: str) -> None:
    applications = parse_applications(outdoor_html, ApplicationKind.OUTDOOR_DUTY)
    assert len(applications) == 3
    assert all(item.kind is ApplicationKind.OUTDOOR_DUTY for item in applications)


def test_the_portals_own_id_is_kept(outdoor_html: str) -> None:
    """Unlike the swipe grid these carry an App. Id, so identity does not have to be
    rebuilt from the fields and a re-sync cannot duplicate an edited row."""
    assert [
        item.app_id for item in parse_applications(outdoor_html, ApplicationKind.OUTDOOR_DUTY)
    ] == [
        "6856",
        "7301",
        "7150",
    ]


def test_a_half_day_marker_does_not_break_the_date(outdoor_html: str) -> None:
    """The cell reads "14-Jun-26 Sun2nd Half" — the weekday and the marker run together."""
    first, *_ = parse_applications(outdoor_html, ApplicationKind.OUTDOOR_DUTY)
    assert first.start == date(2026, 6, 14)
    assert first.end == date(2026, 6, 19)


def test_the_type_suffix_is_stripped_off_the_day_count(outdoor_html: str) -> None:
    """Apply Days reads "5.50 OD". Reading it whole would make every count zero."""
    first, *_ = parse_applications(outdoor_html, ApplicationKind.OUTDOOR_DUTY)
    assert first.days == 5.5


def test_a_missing_end_date_means_a_single_day(outdoor_html: str) -> None:
    """Not an open-ended application running to the end of time."""
    *_, last = parse_applications(outdoor_html, ApplicationKind.OUTDOOR_DUTY)
    assert last.start == last.end == date(2026, 7, 21)
    assert last.is_single_day


def test_every_status_the_portal_offers_is_understood(outdoor_html: str) -> None:
    statuses = [
        item.status for item in parse_applications(outdoor_html, ApplicationKind.OUTDOOR_DUTY)
    ]
    assert statuses == [RequestStatus.APPROVED, RequestStatus.IN_PROCESS, RequestStatus.REJECTED]


def test_a_pending_application_is_open(outdoor_html: str) -> None:
    _, pending, _ = parse_applications(outdoor_html, ApplicationKind.OUTDOOR_DUTY)
    assert pending.is_open


def test_covers_spans_the_whole_range(outdoor_html: str) -> None:
    first, *_ = parse_applications(outdoor_html, ApplicationKind.OUTDOOR_DUTY)
    assert first.covers(date(2026, 6, 17))
    assert not first.covers(date(2026, 6, 20))


def test_the_leave_grids_extra_column_is_read(leave_html: str) -> None:
    """Only the leave list has Leave Type, which is why the tail is read positionally."""
    (application,) = parse_applications(leave_html, ApplicationKind.LEAVE)
    assert application.leave_type == "CO-"
    assert application.days == 1.0


def test_the_outdoor_grid_has_no_leave_type_and_says_so(outdoor_html: str) -> None:
    """Nine columns, not ten. Reading index 8 blindly would put LeaveCategory here."""
    assert all(
        item.leave_type == ""
        for item in parse_applications(outdoor_html, ApplicationKind.OUTDOOR_DUTY)
    )


def test_a_status_view_with_nothing_in_it_is_not_an_error() -> None:
    """Four of the five views are usually empty — that is the normal case, not a fault."""
    page = f'<html><body><select id="{STATUS_SELECT_ID}"></select></body></html>'
    assert parse_applications(page, ApplicationKind.COMP_OFF) == []


def test_a_page_that_is_not_an_application_list_raises() -> None:
    with pytest.raises(ParserError, match="comp-off"):
        parse_applications("<html><body></body></html>", ApplicationKind.COMP_OFF)
