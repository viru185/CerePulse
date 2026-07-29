"""Service-layer fixtures.

The gateway is faked but the repositories are real (in-memory SQLite), so cache-first
behaviour is exercised against genuine persistence rather than a mock's memory of it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest

from cerepulse.core.config import AppConfig
from cerepulse.core.errors import SessionExpiredError, TransportError
from cerepulse.models.attendance import AttendanceMonth, Punch
from cerepulse.models.leave import Holiday, LeaveBalance, LeaveTransaction
from cerepulse.models.swipe import SwipeRequest
from cerepulse.parsers.attendance import ParsedDay
from cerepulse.repository.attendance import AttendanceRepository
from cerepulse.repository.database import Database, open_database
from cerepulse.repository.employee import EmployeeRepository
from cerepulse.repository.leave import (
    HolidayRepository,
    LeaveRepository,
    SwipeRequestRepository,
    SyncMetadataRepository,
)
from cerepulse.services.attendance import AttendanceService
from cerepulse.services.leave import LeaveService
from cerepulse.services.sync import SyncCoordinator

EMPLOYEE = "CIPL00364"


class FakeGateway:
    """Stands in for :class:`PortalGateway`, recording calls and replaying canned data."""

    def __init__(self) -> None:
        self.months: dict[tuple[int, int], AttendanceMonth] = {}
        self.punches: dict[date, list[Punch]] = {}
        self.balances: list[LeaveBalance] = []
        self.transactions: list[LeaveTransaction] = []
        self.swipe_requests: list[SwipeRequest] = []
        self.holidays: list[Holiday] = []

        #: Raise this on the next call, then clear it. Drives expiry/outage tests.
        self.fail_with: Exception | None = None
        #: Raise on every call until cleared.
        self.always_fail_with: Exception | None = None
        #: Raise on the next month fetch only, then clear. Lets a test fail one month
        #: of a history backfill without breaking the period lookup that precedes it.
        self.fail_month_with: Exception | None = None
        #: Raise on the next day-detail fetch only, then clear. Lets a test fail one day
        #: of a backfill batch without aborting the month fetch that precedes it.
        self.fail_detail_with: Exception | None = None

        #: Periods the fake portal claims to offer, newest first.
        self.periods: list[tuple[int, int]] = [(2026, m) for m in range(12, 0, -1)]

        self.month_fetches = 0
        self.detail_fetches: list[date] = []
        self.leave_fetches = 0
        self.menu_forgotten = 0

    # --- fault injection ------------------------------------------------------------

    def _maybe_fail(self) -> None:
        if self.always_fail_with is not None:
            raise self.always_fail_with
        if self.fail_with is not None:
            error, self.fail_with = self.fail_with, None
            raise error

    # --- gateway surface ------------------------------------------------------------

    def forget_menu(self) -> None:
        self.menu_forgotten += 1

    def fetch_month(self, year: int, month: int) -> tuple[AttendanceMonth, list[ParsedDay]]:
        self._maybe_fail()
        if self.fail_month_with is not None:
            error, self.fail_month_with = self.fail_month_with, None
            raise error
        self.month_fetches += 1
        found = self.months.get(
            (year, month), AttendanceMonth(employee_code=EMPLOYEE, year=year, month=month)
        )
        parsed = [
            ParsedDay(day=day, detail_ctl=f"ctl{index + 2:02d}")
            for index, day in enumerate(found.days)
        ]
        return found, parsed

    def available_periods(self, html: str | None = None) -> list[tuple[int, int]]:
        self._maybe_fail()
        return list(self.periods)

    def fetch_day_detail(self, day: ParsedDay) -> list[Punch]:
        self._maybe_fail()
        if self.fail_detail_with is not None:
            error, self.fail_detail_with = self.fail_detail_with, None
            raise error
        self.detail_fetches.append(day.day.day)
        return self.punches.get(day.day.day, [])

    def fetch_leave(self) -> tuple[list[LeaveBalance], list[LeaveTransaction]]:
        self._maybe_fail()
        self.leave_fetches += 1
        return list(self.balances), list(self.transactions)

    def fetch_swipe_requests(self) -> list[SwipeRequest]:
        self._maybe_fail()
        return list(self.swipe_requests)

    def fetch_holidays(self) -> list[Holiday]:
        self._maybe_fail()
        return list(self.holidays)


class FakeAuth:
    """Minimal stand-in for :class:`AuthManager`."""

    def __init__(self) -> None:
        self.state = "authenticated"
        self.reauth_count = 0
        self.reauth_fails = False

    def reauthenticate(self) -> None:
        self.reauth_count += 1
        if self.reauth_fails:
            raise SessionExpiredError("No credentials available")
        self.state = "reauthenticated"


@pytest.fixture
def database() -> Iterator[Database]:
    db = open_database(":memory:")
    yield db
    db.close()


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def config() -> AppConfig:
    return AppConfig()


@pytest.fixture
def repos(database: Database) -> dict[str, object]:
    return {
        "attendance": AttendanceRepository(database),
        "leave": LeaveRepository(database),
        "swipes": SwipeRequestRepository(database),
        "holidays": HolidayRepository(database),
        "sync_meta": SyncMetadataRepository(database),
        "employees": EmployeeRepository(database),
    }


@pytest.fixture
def attendance_service(
    gateway: FakeGateway, repos: dict[str, object], config: AppConfig
) -> AttendanceService:
    return AttendanceService(
        gateway=gateway,  # type: ignore[arg-type]
        attendance=repos["attendance"],  # type: ignore[arg-type]
        swipes=repos["swipes"],  # type: ignore[arg-type]
        holidays=repos["holidays"],  # type: ignore[arg-type]
        sync_meta=repos["sync_meta"],  # type: ignore[arg-type]
        employees=repos["employees"],  # type: ignore[arg-type]
        config=config,
    )


@pytest.fixture
def leave_service(
    gateway: FakeGateway, repos: dict[str, object], config: AppConfig
) -> LeaveService:
    return LeaveService(
        gateway=gateway,
        leave=repos["leave"],  # type: ignore[arg-type]
        swipes=repos["swipes"],  # type: ignore[arg-type]
        holidays=repos["holidays"],  # type: ignore[arg-type]
        sync_meta=repos["sync_meta"],  # type: ignore[arg-type]
        config=config,
    )


@pytest.fixture
def auth() -> FakeAuth:
    return FakeAuth()


@pytest.fixture
def coordinator(
    auth: FakeAuth,
    gateway: FakeGateway,
    attendance_service: AttendanceService,
    leave_service: LeaveService,
) -> SyncCoordinator:
    return SyncCoordinator(
        auth=auth,  # type: ignore[arg-type]
        gateway=gateway,  # type: ignore[arg-type]
        attendance=attendance_service,
        leave=leave_service,
    )


def offline() -> TransportError:
    return TransportError("Network unavailable")
