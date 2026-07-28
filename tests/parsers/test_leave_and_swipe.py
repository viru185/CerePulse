"""Leave register, holiday list, and swipe-request parsing."""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest

from cerepulse.models.leave import LeaveCategory
from cerepulse.models.swipe import SwipeStatus
from cerepulse.parsers.leave import (
    current_balances,
    parse_holidays,
    parse_leave_register,
)
from cerepulse.parsers.swipe import parse_swipe_requests

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def register_html() -> str:
    return (FIXTURES / "leave_register.html").read_text(encoding="utf-8")


@pytest.fixture
def swipe_html() -> str:
    return (FIXTURES / "swipe_requests.html").read_text(encoding="utf-8")


# --- leave register -------------------------------------------------------------------


def test_parses_ledger_rows_and_skips_separators(register_html: str) -> None:
    """The 'Muster Data' row is a single-cell separator, not a transaction."""
    transactions = parse_leave_register(register_html)
    assert len(transactions) == 6
    assert all(txn.leave_type != "Muster Data" for txn in transactions)


def test_dated_transactions_keep_their_date(register_html: str) -> None:
    dated = [txn for txn in parse_leave_register(register_html) if txn.transaction_date]
    assert dated[0].transaction_date == date(2026, 4, 30)
    assert "Monthly Incr" in dated[0].remark


def test_current_balances_prefer_the_summary_row(register_html: str) -> None:
    """PL has an undated summary of 6.00 plus older dated rows; the summary wins."""
    balances = {b.leave_type: b for b in current_balances(parse_leave_register(register_html))}
    assert balances["PL"].available_balance == 6.00


def test_all_leave_types_are_reduced(register_html: str) -> None:
    balances = current_balances(parse_leave_register(register_html))
    assert {b.leave_type for b in balances} == {"PL", "CO- / CO+", "CF", "ML"}


def test_comp_off_is_recognized_despite_its_label(register_html: str) -> None:
    """The portal labels comp off 'CO- / CO+', so matching is on the leading code."""
    balances = {b.leave_type: b for b in current_balances(parse_leave_register(register_html))}
    comp_off = balances["CO- / CO+"]
    assert comp_off.is_comp_off
    assert comp_off.category is LeaveCategory.COMP_OFF
    assert comp_off.available_balance == 2.00


@pytest.mark.parametrize(
    ("leave_type", "category"),
    [
        ("PL", LeaveCategory.PLANNED),
        ("CF", LeaveCategory.CARRY_FORWARD),
        ("CO- / CO+", LeaveCategory.COMP_OFF),
        ("ML", LeaveCategory.MEDICAL),
        ("XYZ", LeaveCategory.OTHER),
    ],
)
def test_leave_categories(leave_type: str, category: LeaveCategory) -> None:
    assert LeaveCategory.classify(leave_type) is category


def test_unpopulated_register_yields_nothing() -> None:
    """Before the View postback the grid is absent — that is not an error."""
    assert parse_leave_register("<html><body>no grid</body></html>") == []


# --- holidays -------------------------------------------------------------------------


def test_parses_holidays() -> None:
    html = """
      <table id="ctl00_BodyContentPlaceHolder_GridView1">
        <tr><th>Date</th><th>Day</th><th>Remarks</th></tr>
        <tr><td>14-Jan-26</td><td>Wednesday</td><td>Uttrayan</td></tr>
        <tr><td>26-Jan-26</td><td>Monday</td><td>Republic day</td></tr>
      </table>
    """
    holidays = parse_holidays(html)
    assert [h.day for h in holidays] == [date(2026, 1, 14), date(2026, 1, 26)]
    assert holidays[1].name == "Republic day"


# --- swipe requests -------------------------------------------------------------------


def test_parses_swipe_requests(swipe_html: str) -> None:
    requests = parse_swipe_requests(swipe_html)
    assert len(requests) == 3
    assert requests[0].for_date == date(2026, 7, 24)
    assert requests[0].remark == "Work from home."


def test_trailing_weekday_in_the_date_is_ignored(swipe_html: str) -> None:
    """The cell reads '24-Jul-26 Fri'."""
    assert parse_swipe_requests(swipe_html)[0].for_date == date(2026, 7, 24)


def test_only_the_relevant_time_column_is_filled(swipe_html: str) -> None:
    in_request, out_request, _ = parse_swipe_requests(swipe_html)
    assert in_request.direction == "In"
    assert in_request.in_time == time(9, 0)
    assert in_request.out_time is None
    assert out_request.direction == "Out"
    assert out_request.out_time == time(18, 45)
    assert out_request.in_time is None


def test_status_parsing_and_openness(swipe_html: str) -> None:
    requests = parse_swipe_requests(swipe_html)
    assert requests[0].status is SwipeStatus.IN_PROCESS
    assert requests[0].is_open
    assert requests[2].status is SwipeStatus.APPROVED
    assert not requests[2].is_open
    assert requests[2].approve_date == date(2026, 7, 12)


def test_unknown_status_does_not_raise() -> None:
    assert SwipeStatus.parse("Escalated") is SwipeStatus.UNKNOWN


def test_no_swipe_grid_yields_nothing() -> None:
    assert parse_swipe_requests("<html><body></body></html>") == []
