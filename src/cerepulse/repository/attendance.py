"""Attendance persistence and the offline history it backs.

Two behaviours matter here and both exist to protect data the app cannot cheaply re-fetch:

* **Saving a month never discards punch detail.** The monthly grid carries no punches, so a
  routine month refresh writes rows whose punch list is empty. If that overwrote stored
  punches, every sync would silently erase the day detail it cost a postback each to build.
  :meth:`AttendanceRepository.save_month` therefore preserves existing punches unless the
  incoming day actually carries some.
* **Upserts, not delete-and-insert.** A month is re-synced constantly; replacing rows
  wholesale would churn the table and lose ``detail_loaded``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import date, datetime

from cerepulse.core.errors import RepositoryError
from cerepulse.models.attendance import AttendanceDay, AttendanceMonth, Punch
from cerepulse.repository.database import Database
from cerepulse.repository.mappers import (
    attendance_day_to_row,
    punch_to_row,
    row_to_attendance_day,
    row_to_punch,
)

_DAY_COLUMNS = """
    employee_code, day, weekday, status, shift_code, shift_in, shift_out,
    first_in, last_out, user_type_1, user_type_2, portion,
    total_minutes, late_minutes, ot_minutes, remarks, detail_loaded, synced_at
"""

_UPSERT_DAY = f"""
    INSERT INTO attendance_day ({_DAY_COLUMNS})
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (employee_code, day) DO UPDATE SET
        weekday       = excluded.weekday,
        status        = excluded.status,
        shift_code    = excluded.shift_code,
        shift_in      = excluded.shift_in,
        shift_out     = excluded.shift_out,
        first_in      = excluded.first_in,
        last_out      = excluded.last_out,
        user_type_1   = excluded.user_type_1,
        user_type_2   = excluded.user_type_2,
        portion       = excluded.portion,
        total_minutes = excluded.total_minutes,
        late_minutes  = excluded.late_minutes,
        ot_minutes    = excluded.ot_minutes,
        remarks       = excluded.remarks,
        -- Never downgrade a day that already has detail.
        detail_loaded = MAX(attendance_day.detail_loaded, excluded.detail_loaded),
        synced_at     = excluded.synced_at
"""


class AttendanceRepository:
    """Reads and writes attendance days and their punch logs."""

    def __init__(self, database: Database) -> None:
        self.database = database

    # --- writing --------------------------------------------------------------------

    def save_month(self, month: AttendanceMonth, *, synced_at: datetime | None = None) -> None:
        """Persist a month's grid. Existing punch detail is preserved."""
        stamp = (synced_at or datetime.now()).isoformat()
        code = month.employee_code

        with self.database.transaction() as connection:
            for day in month.days:
                connection.execute(
                    _UPSERT_DAY, attendance_day_to_row(day, employee_code=code, synced_at=stamp)
                )
                # Only touch punches when this day actually brought some; a grid-only
                # refresh must not wipe detail fetched earlier.
                if day.punches or day.detail_loaded:
                    self._replace_punches(connection, code, day.day, day.punches)

    def save_day_detail(
        self,
        employee_code: str,
        day: date,
        punches: Iterable[Punch],
        *,
        synced_at: datetime | None = None,
    ) -> None:
        """Persist a day's punch log and mark its detail as loaded.

        An empty list is a legitimate result — it records "fetched, nothing there" rather
        than leaving the day looking unfetched.

        The day's grid row must already be cached: punches hang off it by foreign key, and
        the month grid is always what identifies a day as worth fetching in the first place.
        """
        stamp = (synced_at or datetime.now()).isoformat()
        with self.database.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE attendance_day
                   SET detail_loaded = 1, synced_at = ?
                 WHERE employee_code = ? AND day = ?
                """,
                (stamp, employee_code, day.isoformat()),
            ).rowcount
            if not updated:
                raise RepositoryError(
                    f"No cached attendance row for {employee_code} on {day.isoformat()}; "
                    f"save the month before its day detail."
                )
            self._replace_punches(connection, employee_code, day, tuple(punches))

    @staticmethod
    def _replace_punches(
        connection: sqlite3.Connection,
        employee_code: str,
        day: date,
        punches: tuple[Punch, ...],
    ) -> None:
        connection.execute(
            "DELETE FROM punch WHERE employee_code = ? AND day = ?",
            (employee_code, day.isoformat()),
        )
        connection.executemany(
            """
            INSERT INTO punch (
                employee_code, day, sequence, at, direction,
                ip_address, machine, approver_remark
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                punch_to_row(punch, employee_code=employee_code, day=day, sequence=index)
                for index, punch in enumerate(punches)
            ],
        )

    # --- reading --------------------------------------------------------------------

    def find_month(self, employee_code: str, year: int, month: int) -> AttendanceMonth | None:
        """Load a cached month, punches included. None when nothing is cached."""
        prefix = f"{year:04d}-{month:02d}"
        rows = self.database.execute(
            """
            SELECT * FROM attendance_day
             WHERE employee_code = ? AND substr(day, 1, 7) = ?
             ORDER BY day
            """,
            (employee_code, prefix),
        ).fetchall()

        if not rows:
            return None

        punches = self._punches_for_month(employee_code, prefix)
        days = tuple(row_to_attendance_day(row, punches.get(row["day"], ())) for row in rows)
        return AttendanceMonth(employee_code=employee_code, year=year, month=month, days=days)

    def find_day(self, employee_code: str, day: date) -> AttendanceDay | None:
        row = self.database.execute(
            "SELECT * FROM attendance_day WHERE employee_code = ? AND day = ?",
            (employee_code, day.isoformat()),
        ).fetchone()
        if row is None:
            return None
        return row_to_attendance_day(row, tuple(self.find_punches(employee_code, day)))

    def find_punches(self, employee_code: str, day: date) -> list[Punch]:
        rows = self.database.execute(
            """
            SELECT * FROM punch
             WHERE employee_code = ? AND day = ?
             ORDER BY sequence
            """,
            (employee_code, day.isoformat()),
        ).fetchall()
        return [row_to_punch(row) for row in rows]

    def days_missing_detail(self, employee_code: str, year: int, month: int) -> list[date]:
        """Worked days whose punch log has not been fetched — the sync backlog."""
        rows = self.database.execute(
            """
            SELECT day FROM attendance_day
             WHERE employee_code = ?
               AND substr(day, 1, 7) = ?
               AND detail_loaded = 0
               AND status IN ('present', 'half_day')
             ORDER BY day
            """,
            (employee_code, f"{year:04d}-{month:02d}"),
        ).fetchall()
        return [date.fromisoformat(row["day"]) for row in rows]

    def cached_months(self, employee_code: str) -> list[tuple[int, int]]:
        """Every month with cached data, newest first — what offline history can show."""
        rows = self.database.execute(
            """
            SELECT DISTINCT substr(day, 1, 7) AS period
              FROM attendance_day
             WHERE employee_code = ?
             ORDER BY period DESC
            """,
            (employee_code,),
        ).fetchall()
        return [(int(row["period"][:4]), int(row["period"][5:7])) for row in rows]

    def _punches_for_month(self, employee_code: str, prefix: str) -> dict[str, tuple[Punch, ...]]:
        rows = self.database.execute(
            """
            SELECT * FROM punch
             WHERE employee_code = ? AND substr(day, 1, 7) = ?
             ORDER BY day, sequence
            """,
            (employee_code, prefix),
        ).fetchall()

        grouped: dict[str, list[Punch]] = {}
        for row in rows:
            grouped.setdefault(row["day"], []).append(row_to_punch(row))
        return {day: tuple(punches) for day, punches in grouped.items()}
