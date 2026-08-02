"""PortalGateway: period selection, privilege recovery, and available periods.

These pin the two protocol mistakes that shipped in 0.1.1 — posting a submit button as if
it were a LinkButton, and mistaking a privileges page for a successful fetch.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from cerepulse.auth.manager import AuthManager, is_privilege_error
from cerepulse.core.config import AppConfig
from cerepulse.core.errors import PrivilegeError
from cerepulse.services.portal import (
    MONTH_SELECT,
    REFRESH_BUTTON,
    YEAR_SELECT,
    PortalGateway,
)
from cerepulse.transport.client import HttpClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

PRIVILEGE_PAGE = """
<html><body>
  <span id="lblErrMsg">You do not have sufficient  privileges (ROLE) to view this page.</span>
</body></html>
"""

PERIOD_PAGE = """
<html><body><form id="form1">
  <input type="hidden" name="__VIEWSTATE" value="STATE" />
  <select name="ctl00$BodyContentPlaceHolder$drpFromMonth">
    <option value="01">January</option>
    <option value="06">June</option>
    <option selected="selected" value="07">July</option>
  </select>
  <select name="ctl00$BodyContentPlaceHolder$drpFromYear">
    <option selected="selected" value="2026">2026</option>
  </select>
  <input type="submit" name="ctl00$BodyContentPlaceHolder$btnRefresh" value="Refresh" />
</form></body></html>
"""


def gateway() -> PortalGateway:
    config = AppConfig()
    client = HttpClient(config)
    return PortalGateway(client, AuthManager(client, config))


# --- privilege detection --------------------------------------------------------------


def test_the_privileges_page_is_recognised() -> None:
    response = httpx.Response(200, text=PRIVILEGE_PAGE)
    assert is_privilege_error(response)


def test_a_normal_page_is_not_a_privilege_error() -> None:
    assert not is_privilege_error(httpx.Response(200, text=PERIOD_PAGE))


def test_check_response_raises_a_distinct_error() -> None:
    """Distinct from SessionExpiredError: signing in again would not help."""
    config = AppConfig()
    client = HttpClient(config)
    auth = AuthManager(client, config)

    with pytest.raises(PrivilegeError, match="navigation token"):
        auth.check_response(httpx.Response(200, text=PRIVILEGE_PAGE))


# --- period selection -----------------------------------------------------------------


def test_period_payload_posts_the_button_not_an_event_target() -> None:
    """Refresh is <input type=submit>.

    Regression test for 0.1.1: sending __EVENTTARGET meant the server raised no event, the
    grid never rendered, and every non-default month failed with "table not found".
    """
    from cerepulse.transport.webforms import WebFormsState

    state = WebFormsState.from_html(PERIOD_PAGE)
    payload = state.submit(REFRESH_BUTTON, **{MONTH_SELECT: "06", YEAR_SELECT: "2026"})

    assert payload[REFRESH_BUTTON] == "Refresh"
    assert payload["__EVENTTARGET"] == ""
    assert payload[MONTH_SELECT] == "06"
    assert payload["__VIEWSTATE"] == "STATE"


def test_shows_period_matches_the_selected_options() -> None:
    portal = gateway()
    assert portal._shows_period(PERIOD_PAGE, 2026, 7)
    assert not portal._shows_period(PERIOD_PAGE, 2026, 6)
    assert not portal._shows_period(PERIOD_PAGE, 2025, 7)


# --- day detail -------------------------------------------------------------------------


def test_day_detail_selects_the_days_own_month_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """The postback target is a grid row, not a date.

    ``GridView1$ctl17$LnkDate`` means "the seventeenth row of whatever is on screen". The
    attendance page opens on the current month, so asking for a June day in August posted
    June's row index against August's grid and got an empty punch log back — silently, since
    an empty log is a legitimate answer. Five months of history cached zero punches that way.
    """
    from datetime import date

    from cerepulse.models.attendance import AttendanceDay, DayStatus
    from cerepulse.models.values import Duration
    from cerepulse.parsers.attendance import ParsedDay

    portal = gateway()
    selected: list[tuple[int, int]] = []

    monkeypatch.setattr(portal, "_url", lambda *_args: "/Atten/MyAttendanceReport.aspx")
    monkeypatch.setattr(portal._auth, "check_response", lambda response: response)
    monkeypatch.setattr(
        portal._client, "get", lambda *_a, **_k: httpx.Response(200, text=PERIOD_PAGE)
    )

    def select(url: str, html: str, year: int, month: int) -> str:
        selected.append((year, month))
        return html

    monkeypatch.setattr(portal, "_select_period", select)
    # Stop once the period has been decided; the postback itself is not what is under test.
    monkeypatch.setattr("cerepulse.services.portal.find_script_manager", lambda _html: None)

    june = ParsedDay(
        day=AttendanceDay(
            day=date(2026, 6, 15),
            weekday="Mon",
            status=DayStatus.PRESENT,
            total_hours=Duration(540),
        ),
        detail_ctl="ctl17",
    )

    with pytest.raises(Exception, match="ScriptManager"):
        portal.fetch_day_detail(june)

    assert selected == [(2026, 6)], "the June day must be asked for against June's grid"


# --- swipe requests across every status --------------------------------------------------


def _swipe_page(status: str, rows: str = "") -> str:
    """The swipe list as the portal renders it: one status showing, the rest on the filter."""
    options = "".join(
        f'<option {"selected" if value == status else ""} value="{value}">{value}</option>'
        for value in ("In Process", "Approved", "Rejected", "Lapsed", "History")
    )
    grid = (
        f'<table id="ctl00_BodyContentPlaceHolder_GridView1">'
        f"<tr><th></th><th>Edit</th><th>For Date</th><th>Mode</th><th>In time</th>"
        f"<th>Out time</th><th>Remark</th><th>Approve Date</th><th>Status</th>"
        f"<th>Type</th><th>Swipe Category</th></tr>{rows}</table>"
        if rows
        else ""
    )
    return (
        '<html><body><form id="form1">'
        '<input type="hidden" name="__VIEWSTATE" value="STATE" />'
        f'<select name="ctl00$BodyContentPlaceHolder$cboReports" '
        f'id="ctl00_BodyContentPlaceHolder_cboReports">{options}</select>'
        f"{grid}</form></body></html>"
    )


def _row(day: str, status: str, remark: str) -> str:
    return (
        f"<tr><td></td><td></td><td>{day}</td><td>In</td><td>9:00 AM</td><td></td>"
        f"<td>{remark}</td><td></td><td>{status}</td><td>Swipe</td><td>SwipeReq</td></tr>"
    )


def test_swipe_requests_are_fetched_from_every_status_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The grid shows one status at a time and opens on In Process.

    Fetching the page alone therefore returned pending requests and nothing else — which is
    why Records could not show an approval and why "your request was decided" could never
    fire: a decided request *leaves* the In Process grid instead of changing status in it.
    """
    from cerepulse.models.swipe import SwipeStatus
    from cerepulse.services.portal import SWIPE_STATUS_SELECT

    portal = gateway()
    asked: list[str] = []

    pages = {
        "In Process": _swipe_page("In Process", _row("24-Jul-26 Fri", "In Process", "WFH")),
        "Approved": _swipe_page("Approved", _row("10-Jul-26 Fri", "Approved", "Night work")),
        "Rejected": _swipe_page("Rejected", _row("03-Jul-26 Fri", "Rejected", "Forgot")),
        "Lapsed": _swipe_page("Lapsed"),  # empty, and legitimately so
    }

    monkeypatch.setattr(portal, "_url", lambda *_a: "/Leave/SwipeRequestList.aspx")
    monkeypatch.setattr(portal._auth, "check_response", lambda response: response)
    monkeypatch.setattr(
        portal._client, "get", lambda *_a, **_k: httpx.Response(200, text=pages["In Process"])
    )

    def post(_url: str, data: dict[str, str], **_kwargs: object) -> httpx.Response:
        asked.append(data[SWIPE_STATUS_SELECT])
        return httpx.Response(200, text=pages[data[SWIPE_STATUS_SELECT]])

    monkeypatch.setattr(portal._client, "post", post)
    requests = portal.fetch_swipe_requests()

    assert asked == ["Approved", "Rejected", "Lapsed"], "the default view costs no postback"
    assert {request.status for request in requests} == {
        SwipeStatus.IN_PROCESS,
        SwipeStatus.APPROVED,
        SwipeStatus.REJECTED,
    }
    assert [request.for_date.day for request in requests] == [24, 10, 3], "newest first"


def test_a_request_listed_under_two_statuses_keeps_the_decided_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requests have no id, so identity is rebuilt from the fields. A request cannot
    un-approve itself back to pending, so the decided reading wins."""
    from cerepulse.models.swipe import SwipeStatus
    from cerepulse.services.portal import SWIPE_STATUS_SELECT

    portal = gateway()
    same = ("10-Jul-26 Fri", "Night work")
    pages = {
        "In Process": _swipe_page("In Process", _row(same[0], "In Process", same[1])),
        "Approved": _swipe_page("Approved", _row(same[0], "Approved", same[1])),
        "Rejected": _swipe_page("Rejected"),
        "Lapsed": _swipe_page("Lapsed"),
    }

    monkeypatch.setattr(portal, "_url", lambda *_a: "/Leave/SwipeRequestList.aspx")
    monkeypatch.setattr(portal._auth, "check_response", lambda response: response)
    monkeypatch.setattr(
        portal._client, "get", lambda *_a, **_k: httpx.Response(200, text=pages["In Process"])
    )
    monkeypatch.setattr(
        portal._client,
        "post",
        lambda _url, data, **_k: httpx.Response(200, text=pages[data[SWIPE_STATUS_SELECT]]),
    )

    requests = portal.fetch_swipe_requests()

    assert len(requests) == 1
    assert requests[0].status is SwipeStatus.APPROVED


# --- available periods ----------------------------------------------------------------


def test_available_periods_are_bounded_by_the_year_dropdown() -> None:
    """The portal offers one year, so history cannot reach past that January."""
    periods = gateway().available_periods(PERIOD_PAGE)

    assert periods[0] == (2026, 7)
    assert (2026, 1) in periods
    assert all(year == 2026 for year, _ in periods)
    assert periods == sorted(periods, reverse=True)


def test_available_periods_from_the_real_capture() -> None:
    capture = FIXTURES / "attendance_period_page.html"
    if not capture.exists():
        pytest.skip("period fixture not captured")
    periods = gateway().available_periods(capture.read_text(encoding="utf-8"))
    assert len(periods) == 12


def test_missing_dropdowns_raise_rather_than_returning_nothing() -> None:
    from cerepulse.core.errors import ParserError

    with pytest.raises(ParserError, match="period dropdowns"):
        gateway().available_periods("<html><body>nothing here</body></html>")
