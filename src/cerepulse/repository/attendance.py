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
from datetime import date, datetime, time

from loguru import logger

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

        ``detail_synced_at`` records *when* that answer was obtained, which is what lets
        :meth:`days_missing_detail` tell a genuinely empty day from one fetched while it was
        still being worked. It is deliberately separate from ``synced_at``, which every
        month refresh overwrites for every row and so says nothing about the punch log.

        The day's grid row must already be cached: punches hang off it by foreign key, and
        the month grid is always what identifies a day as worth fetching in the first place.
        """
        stamp = (synced_at or datetime.now()).isoformat()
        with self.database.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE attendance_day
                   SET detail_loaded = 1, synced_at = ?, detail_synced_at = ?
                 WHERE employee_code = ? AND day = ?
                """,
                (stamp, stamp, employee_code, day.isoformat()),
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

    # --- gaps the user has told us were work ----------------------------------------

    def flag_worked_gap(
        self, employee_code: str, day: date, gap_start: time, *, note: str = ""
    ) -> None:
        """Record that the gap beginning at ``gap_start`` was work, not a break.

        Keyed on the gap's own start because that is a real punch time and therefore stable
        across re-syncs — a re-fetch rewrites the punches but the Out that began the gap
        keeps its clock time, so the flag stays attached to the thing it was set on.
        """
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO worked_gap (employee_code, day, gap_start, note, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (employee_code, day, gap_start) DO UPDATE SET note = excluded.note
                """,
                (
                    employee_code,
                    day.isoformat(),
                    gap_start.isoformat(timespec="minutes"),
                    note,
                    datetime.now().isoformat(),
                ),
            )

    def clear_worked_gap(self, employee_code: str, day: date, gap_start: time) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM worked_gap WHERE employee_code = ? AND day = ? AND gap_start = ?",
                (employee_code, day.isoformat(), gap_start.isoformat(timespec="minutes")),
            )

    def find_worked_gaps(self, employee_code: str, day: date) -> dict[time, str]:
        """Flagged gaps for one day, each with whatever the user wrote about it."""
        rows = self.database.execute(
            "SELECT gap_start, note FROM worked_gap WHERE employee_code = ? AND day = ?",
            (employee_code, day.isoformat()),
        ).fetchall()
        return {time.fromisoformat(row["gap_start"]): row["note"] or "" for row in rows}

    def find_worked_gaps_between(
        self, employee_code: str, start: date, end: date
    ) -> dict[date, dict[time, str]]:
        """Every flag in a range, so a month can be analysed without a query per day."""
        rows = self.database.execute(
            """
            SELECT day, gap_start, note FROM worked_gap
             WHERE employee_code = ? AND day BETWEEN ? AND ?
            """,
            (employee_code, start.isoformat(), end.isoformat()),
        ).fetchall()
        flagged: dict[date, dict[time, str]] = {}
        for row in rows:
            flagged.setdefault(date.fromisoformat(row["day"]), {})[
                time.fromisoformat(row["gap_start"])
            ] = row["note"] or ""
        return flagged

    def find_comp_off_days(
        self, employee_code: str, start: date, end: date
    ) -> list[tuple[date, float]]:
        """Days on which a comp-off was spent, oldest first, with how much each one spent.

        Read from the muster's own codes: ``CO-`` on either user-type column, with the day's
        portion for a half. The leave register never records consumption, so this is the
        only place the question "when did I use it" can be answered from.
        """
        rows = self.database.execute(
            """
            SELECT day, portion FROM attendance_day
             WHERE employee_code = ? AND day BETWEEN ? AND ?
               AND (upper(trim(user_type_1)) IN ('CO-', 'CO')
                    OR upper(trim(user_type_2)) IN ('CO-', 'CO'))
             ORDER BY day
            """,
            (employee_code, start.isoformat(), end.isoformat()),
        ).fetchall()
        return [
            (
                date.fromisoformat(row["day"]),
                float(row["portion"]) if 0 < float(row["portion"] or 0) < 1 else 1.0,
            )
            for row in rows
        ]

    def find_days_between(self, employee_code: str, start: date, end: date) -> list[AttendanceDay]:
        """Every cached day in a date range, punches included, oldest first.

        Trends span months, so they cannot be assembled from :meth:`find_month` without
        one query per month and a gap wherever a month was never cached.
        """
        rows = self.database.execute(
            """
            SELECT * FROM attendance_day
             WHERE employee_code = ? AND day BETWEEN ? AND ?
             ORDER BY day
            """,
            (employee_code, start.isoformat(), end.isoformat()),
        ).fetchall()
        if not rows:
            return []

        punch_rows = self.database.execute(
            """
            SELECT * FROM punch
             WHERE employee_code = ? AND day BETWEEN ? AND ?
             ORDER BY day, sequence
            """,
            (employee_code, start.isoformat(), end.isoformat()),
        ).fetchall()

        grouped: dict[str, list[Punch]] = {}
        for row in punch_rows:
            grouped.setdefault(row["day"], []).append(row_to_punch(row))

        return [row_to_attendance_day(row, tuple(grouped.get(row["day"], ()))) for row in rows]

    def days_missing_detail(
        self, employee_code: str, year: int, month: int, *, today: date | None = None
    ) -> list[date]:
        """Worked days whose punch log is missing or out of date — the sync backlog.

        Three cases. The obvious one is a day never fetched. The second is a day the portal
        answered with nothing *at the time*, which is what it does for a day still in
        progress: the empty result was recorded as "fetched, nothing there", the backlog
        forgot about it, and the day was left permanently with no punches. An empty punch log
        is legitimate on its own; what makes it a contradiction is the grid reporting hours
        for the same day. A day with 9:27 on it has punches somewhere.

        The third is **today**, and it is why this method exists in its present form. A day
        still being lived is never finished being read: the 08:30 fetch stores the arrival
        and nothing else has happened yet. Under the old predicate that single punch row was
        enough to satisfy "has detail", so today dropped out of the backlog for good and
        lunch and the evening out were never fetched. Every automatic path then re-rendered
        the morning. The only way to see the rest of the day was to press "Sync this day" by
        hand, which is what the user ended up doing four clicks at a time.

        So today is always eligible while it is still today. That does not put ``drain_detail``
        back at risk of looping — the bound that migration 2 exists for still holds for every
        *other* day, and today stops being today. It costs one postback per refresh, for the
        one day whose answer is guaranteed to have changed.

        The second case used to be narrower than the problem. It rescued only a day whose log
        came back *empty*, because the test was "no punch rows" — and a day fetched at 11:34
        has one punch row, the morning In, which satisfied "has detail" forever. That day sat
        in the cache with half a morning on it while the grid beside it said 18:10, and every
        screen rendered the half. What actually marks a fetch as provisional is *when* it was
        made: on or before the day itself. A finished day whose detail was fetched that same
        day is eligible once, whatever it holds; the refetch stamps a later date and settles it.
        """
        now = today or date.today()
        rows = self.database.execute(
            """
            SELECT day FROM attendance_day
             WHERE employee_code = ?
               AND substr(day, 1, 7) = ?
               AND (status IN ('present', 'half_day') OR total_minutes > 0)
               AND (
                     detail_loaded = 0
                  OR day = ?
                  OR (
                        total_minutes > 0
                    AND day < ?
                    AND (detail_synced_at IS NULL OR substr(detail_synced_at, 1, 10) <= day)
                     )
                 )
             ORDER BY day
            """,
            (employee_code, f"{year:04d}-{month:02d}", now.isoformat(), now.isoformat()),
        ).fetchall()
        return [date.fromisoformat(row["day"]) for row in rows]

    def detail_fetched_at(self, employee_code: str, day: date) -> datetime | None:
        """When a day's punch log was last fetched, or None if never."""
        row = self.database.execute(
            "SELECT detail_synced_at FROM attendance_day WHERE employee_code = ? AND day = ?",
            (employee_code, day.isoformat()),
        ).fetchone()
        if row is None or row["detail_synced_at"] is None:
            return None
        return datetime.fromisoformat(str(row["detail_synced_at"]))

    def detail_is_settled(self, employee_code: str, day: date) -> bool:
        """Whether a day's punch log was fetched after the day was over.

        The same rule :meth:`days_missing_detail` applies, asked about one day, so the screen
        that opens a half-fetched day can repair it on the spot instead of waiting for the
        next sync to reach it. Today is never settled; a fetch made during the day is not.
        """
        row = self.database.execute(
            """
            SELECT detail_loaded, detail_synced_at FROM attendance_day
             WHERE employee_code = ? AND day = ?
            """,
            (employee_code, day.isoformat()),
        ).fetchone()
        if row is None or not row["detail_loaded"] or row["detail_synced_at"] is None:
            return False
        return str(row["detail_synced_at"])[:10] > day.isoformat()

    def prune_before(self, employee_code: str, cutoff: date) -> int:
        """Delete cached days before ``cutoff``. Returns how many rows went.

        Punches go with them by foreign key. Nothing here is irreplaceable — every row can
        be fetched again — but the cache otherwise grows for the life of the install, and
        the months at the far end are ones no screen can reach: the portal serves only the
        running year, so the picker cannot even offer them.
        """
        with self.database.transaction() as connection:
            deleted = connection.execute(
                "DELETE FROM attendance_day WHERE employee_code = ? AND day < ?",
                (employee_code, cutoff.isoformat()),
            ).rowcount
        if deleted:
            logger.info("Pruned {} cached day(s) before {}", deleted, cutoff)
        return int(deleted)

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
