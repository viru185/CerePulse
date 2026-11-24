"""Which credit each comp-off leave used, read off the application itself — read-only.

The leave register cannot say which credit a day off spent; the application form can, in
hidden fields the November 2026 survey found. These pin the parsing, the gateway's walk
(open each application from a fresh render of its list, never post from the form), the two
lookups' arguments — ``mTransNo`` above all, which the form fills with SQL and this client
sends empty — and the service's bound on how often any of it is asked.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import httpx
import pytest

from cerepulse.core.config import AppConfig
from cerepulse.core.errors import ParserError
from cerepulse.models.application import Application, ApplicationKind, RequestStatus
from cerepulse.models.leave import CompOffLink, CompOffSnapshot, LeaveTransaction
from cerepulse.parsers.comp_off import (
    comp_off_spend_rows,
    employee_key,
    parse_balance_answer,
    parse_comp_off_link,
    parse_dates_answer,
)
from cerepulse.repository.database import Database
from cerepulse.repository.leave import ApplicationRepository, LeaveRepository
from cerepulse.services.leave import LeaveService
from cerepulse.services.portal import (
    COMP_OFF_BALANCE_METHOD,
    COMP_OFF_DATES_METHOD,
    STATUS_SELECT,
    CompOffFetch,
)
from tests.services.conftest import EMPLOYEE, FakeGateway
from tests.services.test_portal_gateway import MENU, gateway

GRID = "ctl00$BodyContentPlaceHolder$GridView1"


def leave_list(view: str, rows: list[tuple[str, str, str]]) -> str:
    """The leave list on one status: (app id, date, leave type) per row."""
    options = "".join(
        f'<option {"selected" if value == view else ""} value="{value}">{value}</option>'
        for value in ("Recent Applications", "Approved Applications", "History")
    )
    body = "".join(
        f'<tr><td><a href="javascript:__doPostBack(&#39;{GRID}&#39;,&#39;Select${index}&#39;)">'
        f"Edit</a></td><td>{app_id}</td><td>{when} Fri</td><td>{when} Fri</td><td>1.00</td>"
        f"<td>Family visit</td><td>Approved</td><td>{code}</td><td></td></tr>"
        for index, (app_id, when, code) in enumerate(rows)
    )
    return (
        f"<html><body>{MENU}<form>"
        '<input type="hidden" name="__VIEWSTATE" value="STATE" />'
        '<input type="hidden" name="ctl00$BodyContentPlaceHolder$hdnEmpCode" value="412" />'
        f'<select name="{STATUS_SELECT}">{options}</select>'
        '<table id="ctl00_BodyContentPlaceHolder_GridView1"><tr><th></th><th>App. Id</th>'
        "<th>From Date</th><th>To Date</th><th>Apply Days</th><th>Remarks</th><th>Status</th>"
        f"<th>Leave Type</th><th>LeaveCategory</th></tr>{body}</table></form></body></html>"
    )


def form(app_id: str, credit: str, kind: str = "Full", half: str = "") -> str:
    """The leave form as an opened application renders it; only the fields that matter."""
    fields = {
        "HdnTransNo": app_id,
        "hdnCOAdjDate": credit,
        "hdnCOAdjustmenttype1": kind,
        "hdnCOAdjustmentHalfType1": half,
        "hdnCOAdjDaysSetting": "90",
    }
    inputs = "".join(
        f'<input type="hidden" name="ctl00$BodyContentPlaceHolder${name}" value="{value}" />'
        for name, value in fields.items()
    )
    return f"<html><body>{MENU}<form>{inputs}</form></body></html>"


# --- parsing ---------------------------------------------------------------------------------


def test_only_co_minus_rows_are_offered_for_opening() -> None:
    page = leave_list(
        "Recent Applications",
        [("8730", "13-Nov-26", "CO-"), ("8701", "10-Nov-26", "PL"), ("8728", "12-Nov-26", "CO-")],
    )
    assert comp_off_spend_rows(page) == [
        ("8730", GRID, "Select$0"),
        ("8728", GRID, "Select$2"),
    ]


def test_a_form_names_the_credit_it_drew_on() -> None:
    assert parse_comp_off_link(form("7732", "31-May-26", "Half", "Second"), app_id="7732") == [
        CompOffLink("7732", date(2026, 5, 31), 0.5, "Second")
    ]
    assert parse_comp_off_link(form("7622", "18-Jul-26"), app_id="7622") == [
        CompOffLink("7622", date(2026, 7, 18), 1.0)
    ]


def test_a_form_that_names_no_credit_is_recorded_as_none() -> None:
    """A CO- filed against the previous financial year's balance carries an empty field."""
    assert parse_comp_off_link(form("5742", ""), app_id="5742") == [CompOffLink("5742", None, 0.0)]


def test_a_different_applications_form_is_refused() -> None:
    """Saved against the wrong id, a link would put a day off on a credit it never touched."""
    with pytest.raises(ParserError, match="opened 7622"):
        parse_comp_off_link(form("7622", "18-Jul-26"), app_id="7732")
    with pytest.raises(ParserError, match="did not open"):
        parse_comp_off_link(f"<html>{MENU}</html>", app_id="7732")


def test_the_lookups_answers_are_read() -> None:
    assert parse_balance_answer({"d": "CO Balance As On Date 24-Nov-2026 : 5"}) == 5.0
    assert parse_balance_answer({"d": "CO Balance As On Date 24-Nov-2026 : 2.5"}) == 2.5
    assert parse_dates_answer(
        {
            "d": [
                {"__type": "ListItem", "Text": "--Select--", "Value": "0"},
                {"__type": "ListItem", "Text": "03-Oct-26", "Value": "1"},
                {"__type": "ListItem", "Text": "02-Oct-26", "Value": "0.5"},
            ]
        }
    ) == [(date(2026, 10, 2), 0.5), (date(2026, 10, 3), 1.0)]
    with pytest.raises(ParserError):
        parse_balance_answer({"d": "Session expired"})
    with pytest.raises(ParserError):
        parse_dates_answer({"Message": "error"})


def test_the_employee_number_comes_from_the_leave_page() -> None:
    assert employee_key(leave_list("Recent Applications", [])) == "412"
    with pytest.raises(ParserError):
        employee_key(f"<html>{MENU}</html>")


# --- the gateway's walk ----------------------------------------------------------------------


def test_each_application_is_opened_from_a_fresh_render_and_the_lookups_are_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    portal = gateway()
    recent = [("8730", "13-Nov-26", "CO-"), ("8728", "12-Nov-26", "CO-")]
    approved = [("7732", "27-Aug-26", "CO-"), ("7622", "26-Aug-26", "CO-")]
    views = {
        "Recent Applications": leave_list("Recent Applications", recent),
        "Approved Applications": leave_list("Approved Applications", approved),
    }
    forms = {
        ("Recent Applications", "Select$0"): form("8730", "26-Sep-26"),
        ("Recent Applications", "Select$1"): form("8728", "19-Sep-26"),
        ("Approved Applications", "Select$0"): form("7732", "31-May-26", "Half", "Second"),
        ("Approved Applications", "Select$1"): form("7622", "18-Jul-26"),
    }
    sent: list[str] = []
    bodies: list[dict[str, str]] = []

    monkeypatch.setattr(portal, "_url", lambda *_a: "/Leave/LeaveList.aspx")
    monkeypatch.setattr(portal._auth, "check_response", lambda response: response)
    monkeypatch.setattr(
        portal._client,
        "get",
        lambda *_a, **_k: httpx.Response(200, text=views["Recent Applications"]),
    )

    def post(
        url: str, data: dict[str, str] | None = None, content: str | None = None, **_k: object
    ):  # type: ignore[no-untyped-def]
        if content is not None:
            sent.append(url)
            bodies.append(json.loads(content))
            if url == COMP_OFF_BALANCE_METHOD:
                return httpx.Response(200, json={"d": "CO Balance As On Date 24-Nov-2026 : 5"})
            return httpx.Response(200, json={"d": [{"Text": "02-Oct-26", "Value": "1"}]})
        assert data is not None
        if data.get("__EVENTTARGET") == STATUS_SELECT:
            sent.append(f"view {data[STATUS_SELECT]}")
            return httpx.Response(200, text=views[data[STATUS_SELECT]])
        assert data["__EVENTTARGET"] == GRID, "nothing but the list's own postbacks is sent"
        view = data[STATUS_SELECT]
        sent.append(f"open {view} {data['__EVENTARGUMENT']}")
        return httpx.Response(200, text=forms[(view, data["__EVENTARGUMENT"])])

    monkeypatch.setattr(portal._client, "post", post)
    fetched = portal.fetch_comp_off(["8730", "8728", "7732", "7622"], today=date(2026, 11, 24))

    assert fetched.links == {
        "8730": [CompOffLink("8730", date(2026, 9, 26), 1.0)],
        "8728": [CompOffLink("8728", date(2026, 9, 19), 1.0)],
        "7732": [CompOffLink("7732", date(2026, 5, 31), 0.5, "Second")],
        "7622": [CompOffLink("7622", date(2026, 7, 18), 1.0)],
    }
    assert sent == [
        "open Recent Applications Select$0",  # the page as served already shows Recent
        "view Recent Applications",
        "open Recent Applications Select$1",
        "view Approved Applications",
        "open Approved Applications Select$0",
        "view Approved Applications",
        "open Approved Applications Select$1",
        COMP_OFF_BALANCE_METHOD,
        COMP_OFF_DATES_METHOD,
    ]
    assert bodies[0] == {
        "mApplyDate": "24-Nov-26",
        "mTransNo": "",
        "mEmpCode": "412",
        "mPeriodFromDate": "202604",
        "mPeriodToDate": "202703",
    }
    assert fetched.snapshot == CompOffSnapshot(date(2026, 11, 24), 5.0, ((date(2026, 10, 2), 1.0),))


def test_only_the_applications_asked_for_are_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    portal = gateway()
    page = leave_list("Recent Applications", [("8730", "13-Nov-26", "CO-")])
    monkeypatch.setattr(portal, "_url", lambda *_a: "/Leave/LeaveList.aspx")
    monkeypatch.setattr(portal._auth, "check_response", lambda response: response)
    monkeypatch.setattr(portal._client, "get", lambda *_a, **_k: httpx.Response(200, text=page))

    def post(*_a: object, **_k: object) -> httpx.Response:
        raise AssertionError("nothing should be posted")

    monkeypatch.setattr(portal._client, "post", post)
    assert portal.fetch_comp_off([], today=date(2026, 11, 24), snapshot=False) == CompOffFetch()


def test_a_lookup_that_fails_costs_the_cross_check_not_the_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    portal = gateway()
    page = leave_list("Recent Applications", [("8730", "13-Nov-26", "CO-")])
    monkeypatch.setattr(portal, "_url", lambda *_a: "/Leave/LeaveList.aspx")
    monkeypatch.setattr(portal._auth, "check_response", lambda response: response)
    monkeypatch.setattr(portal._client, "get", lambda *_a, **_k: httpx.Response(200, text=page))

    def post(
        url: str, data: dict[str, str] | None = None, content: str | None = None, **_k: object
    ):  # type: ignore[no-untyped-def]
        if content is not None:
            return httpx.Response(500, text="<title>Runtime Error</title>")
        return httpx.Response(200, text=form("8730", "26-Sep-26"))

    monkeypatch.setattr(portal._client, "post", post)
    fetched = portal.fetch_comp_off(["8730"], today=date(2026, 11, 24))

    assert fetched.links == {"8730": [CompOffLink("8730", date(2026, 9, 26), 1.0)]}
    assert fetched.snapshot is None


# --- the service's bound ---------------------------------------------------------------------


class CompOffGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.applications: list[Application] = []
        self.links: dict[str, list[CompOffLink]] = {}
        self.asked: list[tuple[list[str], bool]] = []

    def fetch_applications(self) -> list[Application]:
        return list(self.applications)

    def fetch_comp_off(self, app_ids, *, today: date, snapshot: bool = True) -> CompOffFetch:  # type: ignore[no-untyped-def]
        self.asked.append((sorted(app_ids), snapshot))
        return CompOffFetch(
            links={app_id: self.links[app_id] for app_id in app_ids if app_id in self.links},
            snapshot=CompOffSnapshot(today, 1.0) if snapshot else None,
        )


@pytest.fixture
def service(database: Database, repos: dict[str, object]) -> tuple[LeaveService, CompOffGateway]:
    portal = CompOffGateway()
    leave = LeaveService(
        gateway=portal,  # type: ignore[arg-type]
        leave=repos["leave"],  # type: ignore[arg-type]
        swipes=repos["swipes"],  # type: ignore[arg-type]
        holidays=repos["holidays"],  # type: ignore[arg-type]
        sync_meta=repos["sync_meta"],  # type: ignore[arg-type]
        config=AppConfig(),
        applications=ApplicationRepository(database),
    )
    return leave, portal


def spend(app_id: str, when: date, status: RequestStatus) -> Application:
    return Application(app_id, ApplicationKind.LEAVE, when, when, 1.0, "", status, "CO-")


def test_an_approved_leave_is_opened_once_and_a_pending_one_once_a_day(
    service: tuple[LeaveService, CompOffGateway], repos: dict[str, object]
) -> None:
    leave, portal = service
    today = date(2026, 11, 24)
    portal.applications = [
        Application(
            "1",
            ApplicationKind.COMP_OFF,
            date(2026, 9, 19),
            date(2026, 9, 19),
            1.0,
            "",
            RequestStatus.APPROVED,
        ),
        spend("2", date(2026, 10, 1), RequestStatus.APPROVED),
        spend("3", date(2026, 11, 12), RequestStatus.IN_PROCESS),
        spend("4", date(2026, 11, 13), RequestStatus.REJECTED),
    ]
    portal.links = {
        "2": [CompOffLink("2", date(2026, 9, 19), 1.0)],
        "3": [CompOffLink("3", None, 0.0)],
    }

    leave.refresh_comp_off(EMPLOYEE, portal.applications, today=today)
    leave.refresh_comp_off(EMPLOYEE, portal.applications, today=today)

    # First: both live spends and the balance. Second: nothing — same day, nothing decided.
    assert portal.asked == [(["2", "3"], True)]

    store: LeaveRepository = repos["leave"]  # type: ignore[assignment]
    stored = store.find_comp_off_links(EMPLOYEE)
    assert stored["2"][0] == [CompOffLink("2", date(2026, 9, 19), 1.0)]
    assert stored["3"][0] == [], "named no credit: stored, so it is not opened again"

    # A day on, only the pending one is asked again, and the balance with it.
    store.save_comp_off_links(
        EMPLOYEE, {"3": []}, fetched_at=datetime.combine(today, datetime.min.time())
    )
    leave.refresh_comp_off(EMPLOYEE, portal.applications, today=today + timedelta(days=1))
    assert portal.asked[-1] == (["3"], True)


def test_a_decision_makes_the_portals_balance_stale_the_same_day(
    service: tuple[LeaveService, CompOffGateway],
) -> None:
    leave, portal = service
    today = date(2026, 11, 24)
    earned = Application(
        "1",
        ApplicationKind.COMP_OFF,
        date(2026, 11, 19),
        date(2026, 11, 19),
        1.0,
        "",
        RequestStatus.IN_PROCESS,
    )
    leave.refresh_comp_off(EMPLOYEE, [earned], today=today)
    leave.refresh_comp_off(EMPLOYEE, [earned], today=today)
    assert portal.asked == [([], True)]

    approved = Application(
        "1",
        ApplicationKind.COMP_OFF,
        date(2026, 11, 19),
        date(2026, 11, 19),
        1.0,
        "",
        RequestStatus.APPROVED,
    )
    leave.refresh_comp_off(EMPLOYEE, [approved], today=today)
    assert portal.asked == [([], True), ([], True)]


def test_someone_with_no_comp_off_is_never_asked(
    service: tuple[LeaveService, CompOffGateway],
) -> None:
    leave, portal = service
    planned = Application(
        "1",
        ApplicationKind.LEAVE,
        date(2026, 11, 2),
        date(2026, 11, 2),
        1.0,
        "",
        RequestStatus.APPROVED,
        "PL",
    )
    leave.refresh_comp_off(EMPLOYEE, [planned], today=date(2026, 11, 24))
    assert portal.asked == []


def test_an_empty_register_is_refused_rather_than_saved_over_the_old_one(
    service: tuple[LeaveService, CompOffGateway], repos: dict[str, object]
) -> None:
    leave, portal = service
    store: LeaveRepository = repos["leave"]  # type: ignore[assignment]
    row = LeaveTransaction("CO- / CO+", 0, 0, 1.0, 1.0, date(2026, 10, 2), "Deploy")
    store.save_transactions(EMPLOYEE, [row])

    portal.transactions = []
    with pytest.raises(ParserError, match="no rows"):
        leave.refresh_leave(EMPLOYEE)
    assert store.find_transactions(EMPLOYEE) == [row]


def test_the_register_is_replaced_not_merged(repos: dict[str, object]) -> None:
    """Its running totals change as rows are re-listed, so merging on content stored a new
    copy of every credit on every sync — three of each October credit in the field."""
    store: LeaveRepository = repos["leave"]  # type: ignore[assignment]
    first = LeaveTransaction("CO- / CO+", 3.0, 0, 1.0, 4.0, date(2026, 10, 2), "Deploy")
    relisted = LeaveTransaction("CO- / CO+", 6.0, 0, 1.0, 7.0, date(2026, 10, 2), "Deploy")

    store.save_transactions(EMPLOYEE, [first])
    store.save_transactions(EMPLOYEE, [relisted])

    assert store.find_transactions(EMPLOYEE) == [relisted]


def test_the_portals_balance_round_trips(repos: dict[str, object]) -> None:
    store: LeaveRepository = repos["leave"]  # type: ignore[assignment]
    reading = CompOffSnapshot(
        date(2026, 11, 24), 5.0, ((date(2026, 10, 2), 1.0), (date(2026, 10, 3), 0.5)), "1:a"
    )
    store.save_comp_off_snapshot(EMPLOYEE, reading)
    assert store.find_comp_off_snapshot(EMPLOYEE) == reading
    assert store.find_comp_off_snapshot("someone else") is None
