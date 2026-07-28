"""Schema migration, and the leave/swipe/holiday/sync stores."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time
from pathlib import Path

import pytest

from cerepulse.core.errors import MigrationError, RepositoryError
from cerepulse.models.leave import Holiday, LeaveBalance, LeaveTransaction
from cerepulse.models.swipe import SwipeRequest, SwipeStatus
from cerepulse.repository.database import Database, open_database
from cerepulse.repository.employee import Employee, EmployeeRepository
from cerepulse.repository.leave import (
    HolidayRepository,
    LeaveRepository,
    SwipeRequestRepository,
    SyncMetadataRepository,
    attendance_scope,
)
from cerepulse.repository.schema import SCHEMA_VERSION, current_version, migrate
from tests.repository.conftest import EMPLOYEE, make_day, make_month, make_punches

# --- schema ---------------------------------------------------------------------------


def test_a_fresh_database_migrates_to_the_current_version(database: Database) -> None:
    assert current_version(database.connection) == SCHEMA_VERSION


def test_migration_is_idempotent(database: Database) -> None:
    assert migrate(database.connection) == SCHEMA_VERSION
    assert migrate(database.connection) == SCHEMA_VERSION


def test_a_newer_database_is_refused_rather_than_corrupted() -> None:
    """An older build meeting a newer cache must stop, not write to a schema it misreads."""
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL PRIMARY KEY)")
    connection.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION + 5,))

    with pytest.raises(MigrationError, match="Upgrade CerePulse"):
        migrate(connection)


def test_a_database_file_is_created_with_its_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "cache" / "cerepulse.db"
    with open_database(target) as db:
        assert current_version(db.connection) == SCHEMA_VERSION
    assert target.exists()


def test_foreign_keys_cascade_punches(database: Database) -> None:
    """Enabled explicitly — SQLite defaults them off, so cascades would silently not fire."""
    from cerepulse.repository.attendance import AttendanceRepository

    repo = AttendanceRepository(database)
    day = date(2026, 7, 1)
    repo.save_month(make_month(make_day(day)))
    repo.save_day_detail(EMPLOYEE, day, make_punches())

    with database.transaction() as connection:
        connection.execute("DELETE FROM attendance_day WHERE day = ?", (day.isoformat(),))

    assert repo.find_punches(EMPLOYEE, day) == []


def test_using_a_closed_database_raises(tmp_path: Path) -> None:
    db = Database(tmp_path / "x.db")
    with pytest.raises(RepositoryError, match="not open"):
        _ = db.connection


def test_a_failed_transaction_rolls_back(database: Database) -> None:
    with pytest.raises(sqlite3.Error), database.transaction() as connection:
        connection.execute(
            "INSERT INTO holiday (day, weekday, name) VALUES ('2026-01-01','Thu','X')"
        )
        connection.execute("INSERT INTO nonexistent_table VALUES (1)")

    assert database.execute("SELECT COUNT(*) AS n FROM holiday").fetchone()["n"] == 0


def test_clear_cache_empties_data_but_keeps_the_schema(database: Database) -> None:
    from cerepulse.repository.attendance import AttendanceRepository

    AttendanceRepository(database).save_month(make_month(make_day(date(2026, 7, 1))))
    database.clear_cache()

    assert database.execute("SELECT COUNT(*) AS n FROM attendance_day").fetchone()["n"] == 0
    assert current_version(database.connection) == SCHEMA_VERSION


# --- leave ----------------------------------------------------------------------------


def test_leave_balances_round_trip(leave: LeaveRepository) -> None:
    leave.save_balances(
        EMPLOYEE,
        [
            LeaveBalance(leave_type="PL", available_balance=6.0),
            LeaveBalance(leave_type="CO- / CO+", available_balance=2.0, as_of=date(2026, 7, 1)),
        ],
    )
    loaded = {b.leave_type: b for b in leave.find_balances(EMPLOYEE)}

    assert loaded["PL"].available_balance == 6.0
    assert loaded["CO- / CO+"].as_of == date(2026, 7, 1)
    assert loaded["CO- / CO+"].is_comp_off


def test_saving_balances_replaces_the_previous_set(leave: LeaveRepository) -> None:
    """A leave type can vanish from the portal; a stale balance must not linger."""
    leave.save_balances(EMPLOYEE, [LeaveBalance(leave_type="PL", available_balance=6.0)])
    leave.save_balances(EMPLOYEE, [LeaveBalance(leave_type="CF", available_balance=3.0)])

    assert [b.leave_type for b in leave.find_balances(EMPLOYEE)] == ["CF"]


def test_resyncing_the_ledger_does_not_duplicate_rows(leave: LeaveRepository) -> None:
    """Transactions have no portal id, so identity is the row's content."""
    transactions = [
        LeaveTransaction(
            leave_type="PL",
            opening_balance=0.0,
            consumed_days=0.0,
            credit_days=1.5,
            available_balance=1.5,
            transaction_date=date(2026, 4, 30),
            remark="Monthly Incr Added",
        )
    ]
    leave.save_transactions(EMPLOYEE, transactions)
    leave.save_transactions(EMPLOYEE, transactions)

    assert len(leave.find_transactions(EMPLOYEE)) == 1


def test_distinct_movements_on_one_date_are_both_kept(leave: LeaveRepository) -> None:
    common = {"leave_type": "PL", "opening_balance": 0.0, "consumed_days": 0.0}
    leave.save_transactions(
        EMPLOYEE,
        [
            LeaveTransaction(
                **common,
                credit_days=1.5,
                available_balance=1.5,
                transaction_date=date(2026, 4, 30),
                remark="Increment",
            ),
            LeaveTransaction(
                **common,
                credit_days=0.5,
                available_balance=2.0,
                transaction_date=date(2026, 4, 30),
                remark="Adjustment",
            ),
        ],
    )
    assert len(leave.find_transactions(EMPLOYEE)) == 2


def test_transactions_filter_by_type(leave: LeaveRepository) -> None:
    leave.save_transactions(
        EMPLOYEE,
        [
            LeaveTransaction("PL", 0.0, 0.0, 1.0, 1.0),
            LeaveTransaction("CF", 0.0, 0.0, 3.0, 3.0),
        ],
    )
    assert [t.leave_type for t in leave.find_transactions(EMPLOYEE, leave_type="CF")] == ["CF"]


# --- swipe requests -------------------------------------------------------------------


def swipe(day: date, status: SwipeStatus = SwipeStatus.IN_PROCESS) -> SwipeRequest:
    return SwipeRequest(
        for_date=day,
        direction="In",
        in_time=time(9, 0),
        out_time=None,
        remark="Extra night work",
        status=status,
    )


def test_swipe_requests_round_trip(swipes: SwipeRequestRepository) -> None:
    swipes.save_all(EMPLOYEE, [swipe(date(2026, 7, 24))])
    loaded = swipes.find_all(EMPLOYEE)

    assert len(loaded) == 1
    assert loaded[0].for_date == date(2026, 7, 24)
    assert loaded[0].in_time == time(9, 0)
    assert loaded[0].is_open


def test_a_swipe_request_status_change_updates_in_place(
    swipes: SwipeRequestRepository,
) -> None:
    day = date(2026, 7, 24)
    swipes.save_all(EMPLOYEE, [swipe(day)])
    swipes.save_all(EMPLOYEE, [swipe(day, SwipeStatus.APPROVED)])

    loaded = swipes.find_all(EMPLOYEE)
    assert len(loaded) == 1
    assert loaded[0].status is SwipeStatus.APPROVED


def test_swipe_requests_filter_by_month(swipes: SwipeRequestRepository) -> None:
    swipes.save_all(EMPLOYEE, [swipe(date(2026, 6, 30)), swipe(date(2026, 7, 24))])
    assert [r.for_date for r in swipes.find_for_month(EMPLOYEE, 2026, 7)] == [date(2026, 7, 24)]


# --- holidays -------------------------------------------------------------------------


def test_holidays_round_trip_and_upsert(holidays: HolidayRepository) -> None:
    holidays.save_all([Holiday(day=date(2026, 1, 14), weekday="Wednesday", name="Uttrayan")])
    holidays.save_all([Holiday(day=date(2026, 1, 14), weekday="Wednesday", name="Uttarayan")])

    loaded = holidays.find_all()
    assert len(loaded) == 1
    assert loaded[0].name == "Uttarayan"


def test_holidays_filter_by_range(holidays: HolidayRepository) -> None:
    holidays.save_all(
        [
            Holiday(day=date(2026, 1, 14), weekday="Wed", name="Uttrayan"),
            Holiday(day=date(2026, 8, 15), weekday="Sat", name="Independence Day"),
        ]
    )
    found = holidays.find_between(date(2026, 6, 1), date(2026, 12, 31))
    assert [h.day for h in found] == [date(2026, 8, 15)]


# --- sync metadata --------------------------------------------------------------------


def test_sync_metadata_records_and_reads_back(sync_meta: SyncMetadataRepository) -> None:
    stamp = datetime(2026, 7, 29, 22, 0)
    sync_meta.mark_synced("leave", at=stamp)
    assert sync_meta.last_synced("leave") == stamp


def test_an_unsynced_scope_is_stale(sync_meta: SyncMetadataRepository) -> None:
    assert sync_meta.is_stale("leave", max_age_minutes=30)
    assert sync_meta.last_synced("leave") is None


def test_staleness_respects_the_ttl(sync_meta: SyncMetadataRepository) -> None:
    synced = datetime(2026, 7, 29, 22, 0)
    sync_meta.mark_synced("leave", at=synced)

    assert not sync_meta.is_stale("leave", max_age_minutes=30, now=datetime(2026, 7, 29, 22, 20))
    assert sync_meta.is_stale("leave", max_age_minutes=30, now=datetime(2026, 7, 29, 22, 45))


def test_marking_again_moves_the_timestamp(sync_meta: SyncMetadataRepository) -> None:
    sync_meta.mark_synced("leave", at=datetime(2026, 7, 29, 22, 0))
    sync_meta.mark_synced("leave", at=datetime(2026, 7, 29, 23, 0))

    assert sync_meta.last_synced("leave") == datetime(2026, 7, 29, 23, 0)
    assert len(sync_meta.all_scopes()) == 1


def test_attendance_scope_key_is_stable() -> None:
    assert attendance_scope(2026, 7) == "attendance:2026-07"
    assert attendance_scope(2026, 12) == "attendance:2026-12"


# --- employee -------------------------------------------------------------------------


def test_employee_round_trips(employees: EmployeeRepository) -> None:
    employees.save(Employee(code=EMPLOYEE, name="Viren Hirpara", company_code="CEREBU"))
    loaded = employees.find(EMPLOYEE)

    assert loaded is not None
    assert loaded.name == "Viren Hirpara"
    assert loaded.company_code == "CEREBU"


def test_a_later_sync_without_a_name_keeps_the_known_one(
    employees: EmployeeRepository,
) -> None:
    """Some pages expose only the code; that must not blank an already-known name."""
    employees.save(Employee(code=EMPLOYEE, name="Viren Hirpara", company_code="CEREBU"))
    employees.save(Employee(code=EMPLOYEE))

    loaded = employees.find(EMPLOYEE)
    assert loaded is not None
    assert loaded.name == "Viren Hirpara"
    assert loaded.company_code == "CEREBU"


def test_find_any_restores_session_context(employees: EmployeeRepository) -> None:
    assert employees.find_any() is None
    employees.save(Employee(code=EMPLOYEE, name="Viren Hirpara"))
    found = employees.find_any()
    assert found is not None
    assert found.code == EMPLOYEE
