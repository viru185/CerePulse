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

from collections.abc import Callable, Iterator
from datetime import date
from typing import TypeVar

from loguru import logger
from lxml import html as lxml_html

from cerepulse.auth.manager import AuthManager
from cerepulse.core.errors import ParserError, PrivilegeError
from cerepulse.models.application import Application, ApplicationKind
from cerepulse.models.attendance import AttendanceMonth, Punch
from cerepulse.models.leave import Holiday, LeaveBalance, LeaveTransaction
from cerepulse.models.swipe import SwipeRequest
from cerepulse.parsers.applications import parse_applications
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

# Every request list — swipes, leave, outdoor duty, comp-off — shows one status at a time
# through this control. The first entry in each tuple is what the page renders on its own,
# so it costs no postback. The option *labels* differ between the swipe list and the other
# three, which is the only reason there are two tuples rather than one.
STATUS_SELECT = "ctl00$BodyContentPlaceHolder$cboReports"
SWIPE_STATUS_SELECT = STATUS_SELECT  # kept as the name the swipe path reads
SWIPE_STATUS_VIEWS = ("In Process", "Approved", "Rejected", "Lapsed")
#: "History" would do in one request what these four do in four, but it is the portal's own
#: undocumented rollup and there is no way to know what it drops. Asking for each state by
#: name is the version whose gaps are visible.
APPLICATION_STATUS_VIEWS = (
    "Recent Applications",
    "Approved Applications",
    "Rejected Applications",
    "Lapsed Applications",
)

# The three application lists, as (kind, menu label, menu section).
MENU_APPLICATIONS: tuple[tuple[ApplicationKind, str, str], ...] = (
    (ApplicationKind.LEAVE, "Apply", "Leave > Leave"),
    (ApplicationKind.OUTDOOR_DUTY, "Apply", "Leave > Outdoor Duty"),
    (ApplicationKind.COMP_OFF, "Apply", "Leave > Comp. Off"),
)

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
        """The navigation menu, fetched once per session.

        A landing page with no menu on it is not a vendor UI change, whatever the parser
        thinks: every live session gets a menu, and the page only comes back bare when the
        session behind it is gone. The portal has at least three ways of saying that — a
        302 to login, a 200 re-rendering the login form, and this — and only the first two
        are shapes ``check_response`` can recognise. Reporting the third as a parse failure
        sent it down the "the portal changed" path instead of the session-recovery one,
        where an eviction is what it actually was.
        """
        if self._menu is None or refresh:
            response = self._auth.check_response(self._client.get(pages.HOME))
            try:
                self._menu = parse_menu(response.text)
            except ParserError as exc:
                raise self._auth.session_lost("The landing page came back with no menu") from exc
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

    def page_url(self, label: str, section: str) -> str:
        """A page's full URL, privilege token and all, for handing to a browser.

        Public because the deep link the app opens in a browser has to be a real menu URL:
        `/Atten/SwipeRequestList.aspx` on its own is answered with the privileges page, and
        the `?mnusr=` token cannot be invented — it is only ever read off the live menu.
        """
        return self._retrying_stale_menu(lambda: self._url(label, section))

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
        month_data, days, _page = self.fetch_month_page(year, month)
        return month_data, days

    def fetch_month_page(
        self, year: int, month: int
    ) -> tuple[AttendanceMonth, list[ParsedDay], str]:
        """As :meth:`fetch_month`, and hand back the HTML it was parsed from.

        The page is worth more than the rows parsed out of it: it carries the ``__VIEWSTATE``
        and the ScriptManager that a day-detail postback needs, and it is already showing the
        right month. Callers that want both used to fetch it twice — once here and once
        inside :meth:`fetch_day_detail` — which is two GETs and two full-ViewState period
        postbacks for one day's punches.
        """

        def get() -> tuple[AttendanceMonth, list[ParsedDay], str]:
            url = self._url(*MENU_ATTENDANCE)
            html = self._auth.check_response(self._client.get(url, follow_redirects=True)).text

            if not self._shows_period(html, year, month):
                html = self._select_period(url, html, year, month)

            month_data, days = parse_month(html, year=year, month=month)
            return month_data, days, html

        return self._retrying_stale_menu(get)

    def fetch_day_detail(self, day: ParsedDay, *, page: str | None = None) -> list[Punch]:
        """Fetch one day's punch log via the async postback its date link triggers.

        The postback target is a **row control in the grid currently on screen** —
        ``GridView1$ctl17$LnkDate`` — not a date. The attendance page opens on the current
        month, so asking for a July day during August posted July's row index against
        August's two-row grid and got nothing back. Silently, because an empty punch log is
        a legitimate answer for a day someone genuinely did not work.

        Selecting the day's own month first is therefore not an optimisation, it is what
        makes the target mean the day it was parsed from.

        ``page`` is that already-selected grid, when the caller has one. ``day`` was parsed
        out of some page, and if that is the page still on screen then re-fetching it is pure
        duplication — which it was for every caller, since both of them had just called
        :meth:`fetch_month`. Passing it turns a single day's sync from four requests into
        two, and a twenty-day drain from about sixty into twenty-two.

        Wrapped in :meth:`_retrying_stale_menu` like every other fetch. It was the one that
        was not, so a stale privilege token here raised ``PrivilegeError`` — which is a
        ``ProtocolError``, not a session error, so nothing above could recover from it and
        "sync this day" simply failed after the portal had been opened in a browser.
        """
        target = day.detail_target()
        if target is None:
            raise ParserError(f"{day.day.day:%d %b} has no clickable date link")
        when = day.day.day

        def get() -> list[Punch]:
            url = self._url(*MENU_ATTENDANCE)
            html = page
            if html is None or not self._shows_period(html, when.year, when.month):
                html = self._auth.check_response(self._client.get(url, follow_redirects=True)).text
                if not self._shows_period(html, when.year, when.month):
                    html = self._select_period(url, html, when.year, when.month)

            script_manager = find_script_manager(html)
            if script_manager is None:
                raise ParserError("Attendance page has no ScriptManager; cannot request detail")
            panels = find_update_panels(html)

            payload = async_postback_payload(
                WebFormsState.from_html(html),
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

        return self._retrying_stale_menu(get)

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
        """Every filed request, across all four of the portal's status views.

        The grid shows **one status at a time**. Its selector defaults to In Process, and
        fetching the page alone therefore returns pending requests and nothing else — which
        is what the app did until now. Two things followed from it, both invisible: the
        Records screen could not show an approval because it had never seen one, and the
        "your request was decided" notification could never fire, because a decided request
        *leaves* the In Process grid rather than changing status inside it, so diffing two
        fetches of that one view shows a disappearance and not a decision.

        Each extra view is one postback. Four are cheap next to a month of day detail, and
        skipping any of them would put a hole in the timeline the user cannot see the shape
        of. ``History`` is deliberately not among them: it re-lists what the other four
        already carry.
        """

        def get() -> list[SwipeRequest]:
            collected: dict[tuple[date, str, str], SwipeRequest] = {}
            for page in self._status_views(MENU_SWIPE, SWIPE_STATUS_VIEWS):
                _absorb(collected, parse_swipe_requests(page))
            return sorted(collected.values(), key=lambda request: request.for_date, reverse=True)

        return self._retrying_stale_menu(get)

    def fetch_applications(self) -> list[Application]:
        """Every filed leave, outdoor-duty and comp-off application, across all statuses.

        Nothing read these before, so the Records timeline could say a June week was outdoor
        duty — the muster records that much — but not that the application behind it was
        approved, or that anything was ever applied for at all.

        Unlike a swipe request these carry the portal's own ``App. Id``, so they are keyed on
        it rather than on a synthetic identity rebuilt from the fields.
        """

        def get() -> list[Application]:
            collected: dict[str, Application] = {}
            for kind, label, section in MENU_APPLICATIONS:
                for page in self._status_views((label, section), APPLICATION_STATUS_VIEWS):
                    for application in parse_applications(page, kind):
                        collected[application.app_id] = application
            return sorted(collected.values(), key=lambda item: item.start, reverse=True)

        return self._retrying_stale_menu(get)

    def _status_views(self, menu: tuple[str, str], views: tuple[str, ...]) -> Iterator[str]:
        """Yield one request list's HTML once per status its filter offers.

        These grids show a single status and open on one that is often empty, so a plain GET
        describes a fraction of the data while looking like all of it. The first view costs
        no postback because it is the one the page already rendered.
        """
        url = self._url(*menu)
        page = self._auth.check_response(self._client.get(url, follow_redirects=True)).text
        yield page

        for status in views[1:]:
            state = WebFormsState.from_html(page)
            payload = state.postback(STATUS_SELECT, **{STATUS_SELECT: status})
            logger.debug("Fetching {!r} from {}", status, menu[1])
            yield self._auth.check_response(
                self._client.post(url, data=payload, follow_redirects=True)
            ).text

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


def _absorb(
    collected: dict[tuple[date, str, str], SwipeRequest], requests: list[SwipeRequest]
) -> None:
    """Merge one status view's rows into the accumulator, decided rows winning.

    The portal gives requests no id, so identity has to be built from the fields a user
    cannot file twice with: the day, which punch, and what they wrote. A request should
    appear under exactly one status filter, but if the portal ever lists one under two the
    decided reading is the true one — a request cannot un-approve itself back to pending.
    """
    for request in requests:
        key = (request.for_date, request.direction, request.remark)
        existing = collected.get(key)
        if existing is None or (request.status.is_decided and not existing.status.is_decided):
            collected[key] = request


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
