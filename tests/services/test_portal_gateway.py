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
