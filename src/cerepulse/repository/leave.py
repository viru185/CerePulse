"""Leave, swipe-request, holiday, and sync-metadata persistence."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

from cerepulse.models.application import Application
from cerepulse.models.leave import Holiday, LeaveBalance, LeaveTransaction
from cerepulse.models.swipe import SwipeRequest
from cerepulse.repository.database import Database
from cerepulse.repository.mappers import (
    application_to_row,
    holiday_to_row,
    leave_balance_to_row,
    leave_transaction_to_row,
    row_to_application,
    row_to_holiday,
    row_to_leave_balance,
    row_to_leave_transaction,
    row_to_swipe_request,
    swipe_request_to_row,
)


class LeaveRepository:
    """Leave balances and the ledger behind them."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save_balances(
        self,
        employee_code: str,
        balances: Iterable[LeaveBalance],
        *,
        synced_at: datetime | None = None,
    ) -> None:
        stamp = (synced_at or datetime.now()).isoformat()
        rows = [
            leave_balance_to_row(balance, employee_code=employee_code, synced_at=stamp)
            for balance in balances
        ]
        with self.database.transaction() as connection:
            # A leave type can disappear from the portal entirely; replacing the set keeps
            # a stale balance from lingering forever.
            connection.execute(
                "DELETE FROM leave_balance WHERE employee_code = ?", (employee_code,)
            )
            connection.executemany(
                """
                INSERT INTO leave_balance (
                    employee_code, leave_type, available_balance,
                    consumed_days, credit_days, as_of, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def save_transactions(
        self, employee_code: str, transactions: Iterable[LeaveTransaction]
    ) -> None:
        """Merge ledger rows, keyed on content so a re-sync cannot duplicate them."""
        rows = [
            leave_transaction_to_row(transaction, employee_code=employee_code)
            for transaction in transactions
        ]
        with self.database.transaction() as connection:
            connection.executemany(
                """
                INSERT INTO leave_transaction (
                    employee_code, leave_type, transaction_date, opening_balance,
                    consumed_days, credit_days, available_balance, remark,
                    is_credit, row_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (employee_code, row_hash) DO NOTHING
                """,
                rows,
            )

    def find_balances(self, employee_code: str) -> list[LeaveBalance]:
        rows = self.database.execute(
            "SELECT * FROM leave_balance WHERE employee_code = ? ORDER BY leave_type",
            (employee_code,),
        ).fetchall()
        return [row_to_leave_balance(row) for row in rows]

    def find_transactions(
        self, employee_code: str, *, leave_type: str | None = None
    ) -> list[LeaveTransaction]:
        if leave_type is None:
            rows = self.database.execute(
                """
                SELECT * FROM leave_transaction
                 WHERE employee_code = ?
                 ORDER BY transaction_date IS NULL DESC, transaction_date
                """,
                (employee_code,),
            ).fetchall()
        else:
            rows = self.database.execute(
                """
                SELECT * FROM leave_transaction
                 WHERE employee_code = ? AND leave_type = ?
                 ORDER BY transaction_date IS NULL DESC, transaction_date
                """,
                (employee_code, leave_type),
            ).fetchall()
        return [row_to_leave_transaction(row) for row in rows]


class SwipeRequestRepository:
    """Filed swipe requests, used to cross-reference short days."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save_all(
        self,
        employee_code: str,
        requests: Iterable[SwipeRequest],
        *,
        synced_at: datetime | None = None,
    ) -> None:
        """Replace every stored request with the fetched set, atomically.

        A replace rather than a merge because the fetch covers *all* of the portal's status
        views, so the window it describes is "everything" — and a merge could only ever add.
        A request the user withdrew would otherwise sit on the timeline forever, since the
        portal stops mentioning it rather than marking it gone.

        Safe to replace only because a partial fetch never reaches here: ``_status_views``
        yields lazily and any view that fails propagates out of ``fetch_swipe_requests``
        before a save is attempted. Nothing in this method may be called with "what we
        managed to get" — that is how a failed fetch becomes an empty screen the TTL then
        protects for the rest of the day.
        """
        stamp = (synced_at or datetime.now()).isoformat()
        rows = [
            swipe_request_to_row(request, employee_code=employee_code, synced_at=stamp)
            for request in requests
        ]
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM swipe_request WHERE employee_code = ?", (employee_code,)
            )
            connection.executemany(
                """
                INSERT INTO swipe_request (
                    employee_code, for_date, direction, in_time, out_time,
                    remark, status, approve_date, category, kind, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (employee_code, for_date, direction, remark) DO UPDATE SET
                    in_time    = excluded.in_time,
                    out_time   = excluded.out_time,
                    status     = excluded.status,
                    approve_date  = excluded.approve_date,
                    category   = excluded.category,
                    kind       = excluded.kind,
                    synced_at  = excluded.synced_at
                """,
                rows,
            )

    def find_all(self, employee_code: str) -> list[SwipeRequest]:
        rows = self.database.execute(
            "SELECT * FROM swipe_request WHERE employee_code = ? ORDER BY for_date DESC",
            (employee_code,),
        ).fetchall()
        return [row_to_swipe_request(row) for row in rows]

    def find_for_month(self, employee_code: str, year: int, month: int) -> list[SwipeRequest]:
        rows = self.database.execute(
            """
            SELECT * FROM swipe_request
             WHERE employee_code = ? AND substr(for_date, 1, 7) = ?
             ORDER BY for_date
            """,
            (employee_code, f"{year:04d}-{month:02d}"),
        ).fetchall()
        return [row_to_swipe_request(row) for row in rows]


class ApplicationRepository:
    """Filed leave, outdoor-duty and comp-off applications, and where each one stands."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save_all(
        self,
        employee_code: str,
        applications: Iterable[Application],
        *,
        synced_at: datetime | None = None,
    ) -> None:
        """Replace every stored application with the fetched set, atomically.

        Same reasoning as the swipe requests: the fetch walks every status view of all three
        lists, so it describes the whole entity, and a cancelled application has to be able
        to disappear. And the same precondition — a view that fails aborts the fetch, so a
        partial set never arrives here.
        """
        stamp = (synced_at or datetime.now()).isoformat()
        rows = [
            application_to_row(application, employee_code=employee_code, synced_at=stamp)
            for application in applications
        ]
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM application WHERE employee_code = ?", (employee_code,))
            connection.executemany(
                """
                INSERT INTO application (
                    employee_code, app_id, kind, start_date, end_date,
                    days, remark, status, leave_type, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (employee_code, app_id) DO UPDATE SET
                    kind       = excluded.kind,
                    start_date = excluded.start_date,
                    end_date   = excluded.end_date,
                    days       = excluded.days,
                    remark     = excluded.remark,
                    status     = excluded.status,
                    leave_type = excluded.leave_type,
                    synced_at  = excluded.synced_at
                """,
                rows,
            )

    def find_all(self, employee_code: str) -> list[Application]:
        rows = self.database.execute(
            "SELECT * FROM application WHERE employee_code = ? ORDER BY start_date DESC",
            (employee_code,),
        ).fetchall()
        return [row_to_application(row) for row in rows]


class HolidayRepository:
    """The company holiday calendar.

    Load-bearing rather than decorative: working-day counts and the leave optimizer both
    depend on it, and without it every holiday reads as a zero-hours anomaly.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def save_all(self, holidays: Iterable[Holiday]) -> None:
        """Replace the calendar with the fetched one, atomically.

        The portal publishes the year in one page, so a fetch is the whole calendar. Merging
        meant a holiday the company later moved stayed on both dates forever — and since the
        optimizer and every working-day count read this table, a phantom holiday is not a
        cosmetic problem.

        An empty fetch never reaches here: ``parse_holidays`` raises rather than returning
        nothing when the page is not the holiday list, so this cannot wipe the calendar over
        a session that expired mid-sync.
        """
        rows = [holiday_to_row(holiday) for holiday in holidays]
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM holiday")
            connection.executemany(
                """
                INSERT INTO holiday (day, weekday, name) VALUES (?, ?, ?)
                ON CONFLICT (day) DO UPDATE SET
                    weekday = excluded.weekday,
                    name    = excluded.name
                """,
                rows,
            )

    def find_all(self) -> list[Holiday]:
        rows = self.database.execute("SELECT * FROM holiday ORDER BY day").fetchall()
        return [row_to_holiday(row) for row in rows]

    def find_between(self, start: date, end: date) -> list[Holiday]:
        rows = self.database.execute(
            "SELECT * FROM holiday WHERE day BETWEEN ? AND ? ORDER BY day",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [row_to_holiday(row) for row in rows]


class SyncMetadataRepository:
    """When each scope was last synced — what drives cache-first TTL decisions."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def mark_synced(
        self, scope: str, *, at: datetime | None = None, content_hash: str | None = None
    ) -> None:
        """Record that a scope was just fetched, and optionally what it contained.

        Omitting ``content_hash`` leaves any stored digest alone rather than clearing it, so
        a caller that does not compute one cannot silently make every future sync look like
        a change.
        """
        stamp = (at or datetime.now()).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sync_metadata (scope, last_synced_at, content_hash)
                VALUES (?, ?, ?)
                ON CONFLICT (scope) DO UPDATE SET
                    last_synced_at = excluded.last_synced_at,
                    content_hash = COALESCE(excluded.content_hash, sync_metadata.content_hash)
                """,
                (scope, stamp, content_hash),
            )

    def content_hash(self, scope: str) -> str | None:
        """The digest stored the last time this scope's content was written."""
        row = self.database.execute(
            "SELECT content_hash FROM sync_metadata WHERE scope = ?", (scope,)
        ).fetchone()
        return row["content_hash"] if row else None

    def last_synced(self, scope: str) -> datetime | None:
        row = self.database.execute(
            "SELECT last_synced_at FROM sync_metadata WHERE scope = ?", (scope,)
        ).fetchone()
        return datetime.fromisoformat(row["last_synced_at"]) if row else None

    def is_stale(self, scope: str, *, max_age_minutes: int, now: datetime | None = None) -> bool:
        """True when a scope has never been synced or has aged past its TTL."""
        last = self.last_synced(scope)
        if last is None:
            return True
        elapsed = (now or datetime.now()) - last
        return elapsed.total_seconds() > max_age_minutes * 60

    def all_scopes(self) -> dict[str, datetime]:
        rows = self.database.execute("SELECT * FROM sync_metadata ORDER BY scope").fetchall()
        return {row["scope"]: datetime.fromisoformat(row["last_synced_at"]) for row in rows}


def attendance_scope(year: int, month: int) -> str:
    """Canonical sync-metadata key for one month of attendance."""
    return f"attendance:{year:04d}-{month:02d}"
