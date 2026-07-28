"""Shared repository fixtures. Every test runs against a real in-memory SQLite database."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, time

import pytest

from cerepulse.models.attendance import (
    AttendanceDay,
    AttendanceMonth,
    DayStatus,
    Punch,
    PunchDirection,
)
from cerepulse.models.values import Duration
from cerepulse.repository.attendance import AttendanceRepository
from cerepulse.repository.database import Database, open_database
from cerepulse.repository.employee import EmployeeRepository
from cerepulse.repository.leave import (
    HolidayRepository,
    LeaveRepository,
    SwipeRequestRepository,
    SyncMetadataRepository,
)

EMPLOYEE = "CIPL00364"


@pytest.fixture
def database() -> Iterator[Database]:
    db = open_database(":memory:")
    yield db
    db.close()


@pytest.fixture
def attendance(database: Database) -> AttendanceRepository:
    return AttendanceRepository(database)


@pytest.fixture
def leave(database: Database) -> LeaveRepository:
    return LeaveRepository(database)


@pytest.fixture
def swipes(database: Database) -> SwipeRequestRepository:
    return SwipeRequestRepository(database)


@pytest.fixture
def holidays(database: Database) -> HolidayRepository:
    return HolidayRepository(database)


@pytest.fixture
def sync_meta(database: Database) -> SyncMetadataRepository:
    return SyncMetadataRepository(database)


@pytest.fixture
def employees(database: Database) -> EmployeeRepository:
    return EmployeeRepository(database)


def make_day(
    when: date,
    *,
    status: DayStatus = DayStatus.PRESENT,
    punches: tuple[Punch, ...] = (),
    detail_loaded: bool = False,
    total: str = "9.00",
) -> AttendanceDay:
    return AttendanceDay(
        day=when,
        weekday=when.strftime("%a"),
        status=status,
        shift_code="GS",
        shift_in=time(8, 0),
        shift_out=time(19, 0),
        first_in=time(9, 21),
        last_out=time(18, 31),
        user_type_1="DP",
        user_type_2="---",
        portion=1.0,
        total_hours=Duration.from_hhmm(total),
        late_mark=Duration(0),
        ot_hours=Duration(0),
        remarks="Attendance Muster",
        punches=punches,
        detail_loaded=detail_loaded,
    )


def make_month(*days: AttendanceDay, year: int = 2026, month: int = 7) -> AttendanceMonth:
    return AttendanceMonth(employee_code=EMPLOYEE, year=year, month=month, days=tuple(days))


def make_punches() -> tuple[Punch, ...]:
    return (
        Punch(at=time(9, 21), direction=PunchDirection.IN, ip_address="10.0.0.1", machine="IN"),
        Punch(at=time(13, 0), direction=PunchDirection.OUT, ip_address="10.0.0.1", machine="OUT"),
        Punch(at=time(14, 0), direction=PunchDirection.IN, ip_address="10.0.0.1", machine="IN"),
        Punch(at=time(18, 31), direction=PunchDirection.OUT, ip_address="10.0.0.1", machine="OUT"),
    )
