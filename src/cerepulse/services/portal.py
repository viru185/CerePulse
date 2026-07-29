"""Authenticated page fetching, one level above transport.

Everything the portal exposes is reached through the navigation menu rather than by URL:
a direct request returns "You do not have sufficient privileges (ROLE) to view this page"
even with a valid session, because the real links carry a privilege token. The menu is
therefore fetched once per session and cached — see :mod:`cerepulse.parsers.menu`.

Selecting a month is a postback, not a query parameter. The attendance page renders the
current month and only re-renders after ``btnRefresh`` is submitted with the period
dropdowns set, so :meth:`PortalGateway.fetch_month` skips that round trip when the month
already on screen is the one asked for.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import TypeVar

from loguru import logger
from lxml import html as lxml_html

from cerepulse.auth.manager import AuthManager
from cerepulse.core.errors import ParserError, PrivilegeError
from cerepulse.models.attendance import AttendanceMonth, Punch
from cerepulse.models.leave import Holiday, LeaveBalance, LeaveTransaction
from cerepulse.models.swipe import SwipeRequest
from cerepulse.parsers.attendance import ParsedDay, parse_month, parse_punches
from cerepulse.parsers.leave import current_balances, parse_holidays, parse_leave_register
from cerepulse.parsers.menu import MenuIndex, parse_menu
from cerepulse.parsers.swipe import parse_swipe_requests
from cerepulse.repository.employee import Employee
from cerepulse.transport import pages
from cerepulse.transport.client import HttpClient
from cerepulse.transport.webforms import (
    WebFormsState,
    async_postback_headers,
    async_postback_payload,
    find_script_manager,
    find_update_panels,
)

# Menu entries, as (label, section).
MENU_ATTENDANCE = ("My Attendance", "Time > Attendance")
MENU_SWIPE = ("Apply", "Time > Swipe")
MENU_LEAVE_REGISTER = ("My Leave Register", "Leave > My Info")
MENU_HOLIDAYS = ("Holiday List", "Self Service > Quick Info")

# Controls on the attendance page.
MONTH_SELECT = "ctl00$BodyContentPlaceHolder$drpFromMonth"
YEAR_SELECT = "ctl00$BodyContentPlaceHolder$drpFromYear"
REFRESH_BUTTON = "ctl00$BodyContentPlaceHolder$btnRefresh"

# Control on the leave register that populates its grid.
LEAVE_VIEW_BUTTON = "ctl00$BodyContentPlaceHolder$btnView2"

_EMPLOYEE_BANNER_ID = "ctl00_BodyContentPlaceHolder_lblEmpCode"

T = TypeVar("T")


class PortalGateway:
    """Fetches and parses portal pages. Holds no cache and makes no policy decisions."""

    def __init__(self, client: HttpClient, auth: AuthManager) -> None:
        self._client = client
        self._auth = auth
        self._menu: MenuIndex | None = None

    # --- navigation -----------------------------------------------------------------

    def menu(self, *, refresh: bool = False) -> MenuIndex:
        """The navigation menu, fetched once per session."""
        if self._menu is None or refresh:
            response = self._auth.check_response(self._client.get(pages.HOME))
            self._menu = parse_menu(response.text)
            logger.debug("Indexed {} menu entries", len(self._menu))
        return self._menu

    def forget_menu(self) -> None:
        """Drop the cached menu. Called after re-authentication invalidates its tokens."""
        self._menu = None

    def _fetch(self, label: str, section: str) -> str:
        def get() -> str:
            entry = self.menu().require(label, section=section)
            return self._auth.check_response(
                self._client.get(entry.url, follow_redirects=True)
            ).text

        return self._retrying_stale_menu(get)

    def _url(self, label: str, section: str) -> str:
        return self.menu().require(label, section=section).url

    def _retrying_stale_menu(self, operation: Callable[[], T]) -> T:
        """Run an operation, reloading the menu once if its token turned out to be stale.

        Menu tokens expire independently of the session, so this is recoverable without
        touching credentials. Exactly one retry: if a freshly loaded menu is also refused,
        the problem is not staleness and looping would only hammer the portal.
        """
        try:
            return operation()
        except PrivilegeError:
            logger.info("Menu token rejected; reloading the menu and retrying once")
            self.menu(refresh=True)
            return operation()

    # --- attendance -----------------------------------------------------------------

    def fetch_month(self, year: int, month: int) -> tuple[AttendanceMonth, list[ParsedDay]]:
        """Fetch one month's attendance grid."""

        def get() -> tuple[AttendanceMonth, list[ParsedDay]]:
            url = self._url(*MENU_ATTENDANCE)
            html = self._auth.check_response(self._client.get(url, follow_redirects=True)).text

            if not self._shows_period(html, year, month):
                html = self._select_period(url, html, year, month)

            return parse_month(html, year=year, month=month)

        return self._retrying_stale_menu(get)

    def fetch_day_detail(self, day: ParsedDay) -> list[Punch]:
        """Fetch one day's punch log via the async postback its date link triggers."""
        target = day.detail_target()
        if target is None:
            raise ParserError(f"{day.day.day:%d %b} has no clickable date link")

        url = self._url(*MENU_ATTENDANCE)
        page = self._auth.check_response(self._client.get(url, follow_redirects=True)).text

        script_manager = find_script_manager(page)
        if script_manager is None:
            raise ParserError("Attendance page has no ScriptManager; cannot request detail")
        panels = find_update_panels(page)

        payload = async_postback_payload(
            WebFormsState.from_html(page),
            script_manager=script_manager,
            update_panel=panels[0] if panels else "",
            target=target,
        )
        response = self._auth.check_response(
            self._client.post(
                url, data=payload, headers=async_postback_headers(self._client.url_for(url))
            )
        )
        return parse_punches(response.text)

    def _shows_period(self, html: str, year: int, month: int) -> bool:
        """True when the page already displays the requested month."""
        try:
            state = WebFormsState.from_html(html)
        except ParserError:
            return False
        return state.fields.get(MONTH_SELECT) == f"{month:02d}" and state.fields.get(
            YEAR_SELECT
        ) == str(year)

    def _select_period(self, url: str, html: str, year: int, month: int) -> str:
        """Submit the period filter and return the re-rendered page.

        Refresh is an ``<input type="submit">``, so it must be posted as its own name/value
        pair. Sending ``__EVENTTARGET`` instead — as a LinkButton would need — leaves the
        server with no event to raise: it re-renders the page without running the handler,
        the grid never appears, and the parser fails with a confusing "table not found".
        That was the bug behind every non-default month failing to load.
        """
        state = WebFormsState.from_html(html)
        payload = state.submit(
            REFRESH_BUTTON,
            **{MONTH_SELECT: f"{month:02d}", YEAR_SELECT: str(year)},
        )
        logger.debug("Selecting attendance period {:04d}-{:02d}", year, month)
        return self._auth.check_response(
            self._client.post(url, data=payload, follow_redirects=True)
        ).text

    def available_periods(self, html: str | None = None) -> list[tuple[int, int]]:
        """Every year/month the portal will actually serve, newest first.

        The year dropdown is the real limit: it currently offers only the running portal
        year, so history cannot reach further back than that January however much the user
        asks for.
        """
        page = html if html is not None else self._attendance_page()
        document = lxml_html.fromstring(page)

        def options(name: str) -> list[str]:
            nodes = document.xpath(f"//select[@name={name!r}]/option/@value")
            return [str(value) for value in nodes if str(value).strip()]

        years = sorted({int(value) for value in options(YEAR_SELECT) if value.isdigit()})
        months = sorted({int(value) for value in options(MONTH_SELECT) if value.isdigit()})
        if not years or not months:
            raise ParserError("Attendance page has no period dropdowns")

        return sorted(((year, month) for year in years for month in months), reverse=True)

    def _attendance_page(self) -> str:
        url = self._url(*MENU_ATTENDANCE)
        return self._auth.check_response(self._client.get(url, follow_redirects=True)).text

    # --- leave, swipe, holidays -----------------------------------------------------

    def fetch_leave(self) -> tuple[list[LeaveBalance], list[LeaveTransaction]]:
        """Fetch the leave ledger and reduce it to current balances.

        The register renders an empty grid until its View button is submitted, so fetching
        the page alone would report that the employee has no leave at all.
        """
        url = self._url(*MENU_LEAVE_REGISTER)
        page = self._auth.check_response(self._client.get(url, follow_redirects=True)).text

        state = WebFormsState.from_html(page)
        html = self._auth.check_response(
            self._client.post(url, data=state.submit(LEAVE_VIEW_BUTTON), follow_redirects=True)
        ).text

        transactions = parse_leave_register(html)
        return current_balances(transactions), transactions

    def fetch_swipe_requests(self) -> list[SwipeRequest]:
        return parse_swipe_requests(self._fetch(*MENU_SWIPE))

    def fetch_holidays(self) -> list[Holiday]:
        return parse_holidays(self._fetch(*MENU_HOLIDAYS))

    # --- identity -------------------------------------------------------------------

    def fetch_employee(self) -> Employee:
        """Read the signed-in employee from the banner the portal renders on every page."""
        html = self._fetch(*MENU_ATTENDANCE)
        return Employee(
            code=_employee_code(html) or self._auth.username,
            company_code=self._client.cookies.get("CompCode", "") or "",
        )


def _employee_code(html: str) -> str:
    from lxml import html as lxml_html

    try:
        document = lxml_html.fromstring(html)
    except Exception:  # noqa: BLE001 — identity is best-effort; the username still works
        return ""
    nodes = document.xpath(f"//*[@id='{_EMPLOYEE_BANNER_ID}']")
    return str(nodes[0].text_content()).strip() if nodes else ""


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """First and last day of a month, for range queries."""
    from calendar import monthrange

    return date(year, month, 1), date(year, month, monthrange(year, month)[1])
