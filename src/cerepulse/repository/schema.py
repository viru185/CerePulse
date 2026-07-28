"""SQLite schema and forward migrations.

Two rules shape this (Chapter 08 sections 8 and 10):

* **Versioned, forward-only.** ``schema_version`` records what has been applied; each
  migration is a numbered step that runs once, inside a transaction. There is no downgrade
  path — an older build meeting a newer database refuses to run rather than corrupting it.
* **Parsed models only, never raw HTML.** Storing the fetched pages would mean a parser fix
  needs a data migration. Storing only what was parsed means a fix is reapplied by simply
  re-syncing.

Attendance rows carry a ``detail_loaded`` flag because "no punches recorded" and "punches
not fetched yet" are different states, and the intelligence layer treats them differently.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from loguru import logger

from cerepulse.core.errors import MigrationError

#: Bumped whenever a migration is added. Checked against the database on open.
SCHEMA_VERSION = 1


def _migration_001(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE employee (
            code          TEXT PRIMARY KEY,
            name          TEXT NOT NULL DEFAULT '',
            company_code  TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE attendance_day (
            employee_code TEXT NOT NULL,
            day           TEXT NOT NULL,           -- ISO date
            weekday       TEXT NOT NULL DEFAULT '',
            status        TEXT NOT NULL,
            shift_code    TEXT NOT NULL DEFAULT '',
            shift_in      TEXT,                    -- ISO time or NULL
            shift_out     TEXT,
            first_in      TEXT,
            last_out      TEXT,
            user_type_1   TEXT NOT NULL DEFAULT '',
            user_type_2   TEXT NOT NULL DEFAULT '',
            portion       REAL NOT NULL DEFAULT 0,
            total_minutes INTEGER NOT NULL DEFAULT 0,
            late_minutes  INTEGER NOT NULL DEFAULT 0,
            ot_minutes    INTEGER NOT NULL DEFAULT 0,
            remarks       TEXT NOT NULL DEFAULT '',
            -- Distinguishes "fetched, genuinely no punches" from "detail not fetched yet".
            detail_loaded INTEGER NOT NULL DEFAULT 0,
            synced_at     TEXT NOT NULL,
            PRIMARY KEY (employee_code, day)
        );

        CREATE TABLE punch (
            employee_code   TEXT NOT NULL,
            day             TEXT NOT NULL,
            sequence        INTEGER NOT NULL,      -- position within the day, 0-based
            at              TEXT NOT NULL,         -- ISO time
            direction       TEXT NOT NULL,         -- 'In' | 'Out'
            ip_address      TEXT NOT NULL DEFAULT '',
            machine         TEXT NOT NULL DEFAULT '',
            approver_remark TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (employee_code, day, sequence),
            FOREIGN KEY (employee_code, day)
                REFERENCES attendance_day (employee_code, day) ON DELETE CASCADE
        );

        CREATE TABLE leave_balance (
            employee_code     TEXT NOT NULL,
            leave_type        TEXT NOT NULL,
            available_balance REAL NOT NULL DEFAULT 0,
            consumed_days     REAL NOT NULL DEFAULT 0,
            credit_days       REAL NOT NULL DEFAULT 0,
            as_of             TEXT,
            synced_at         TEXT NOT NULL,
            PRIMARY KEY (employee_code, leave_type)
        );

        CREATE TABLE leave_transaction (
            employee_code     TEXT NOT NULL,
            leave_type        TEXT NOT NULL,
            transaction_date  TEXT,
            opening_balance   REAL NOT NULL DEFAULT 0,
            consumed_days     REAL NOT NULL DEFAULT 0,
            credit_days       REAL NOT NULL DEFAULT 0,
            available_balance REAL NOT NULL DEFAULT 0,
            remark            TEXT NOT NULL DEFAULT '',
            is_credit         INTEGER NOT NULL DEFAULT 0,
            row_hash          TEXT NOT NULL,
            PRIMARY KEY (employee_code, row_hash)
        );

        CREATE TABLE swipe_request (
            employee_code TEXT NOT NULL,
            for_date      TEXT NOT NULL,
            direction     TEXT NOT NULL DEFAULT '',
            in_time       TEXT,
            out_time      TEXT,
            remark        TEXT NOT NULL DEFAULT '',
            status        TEXT NOT NULL,
            approve_date  TEXT,
            category      TEXT NOT NULL DEFAULT '',
            synced_at     TEXT NOT NULL,
            PRIMARY KEY (employee_code, for_date, direction)
        );

        CREATE TABLE holiday (
            day     TEXT PRIMARY KEY,
            weekday TEXT NOT NULL DEFAULT '',
            name    TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE sync_metadata (
            scope          TEXT PRIMARY KEY,       -- e.g. 'attendance:2026-07', 'leave'
            last_synced_at TEXT NOT NULL
        );

        CREATE INDEX idx_attendance_day_day     ON attendance_day (day);
        CREATE INDEX idx_attendance_day_synced  ON attendance_day (synced_at);
        CREATE INDEX idx_attendance_day_month
            ON attendance_day (employee_code, substr(day, 1, 7));
        CREATE INDEX idx_punch_day               ON punch (employee_code, day);
        CREATE INDEX idx_swipe_request_for_date  ON swipe_request (for_date);
        CREATE INDEX idx_leave_txn_type          ON leave_transaction (employee_code, leave_type);
    """)


#: Ordered migrations. Append only; never edit one that has shipped.
MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (_migration_001,)


def current_version(connection: sqlite3.Connection) -> int:
    """Version recorded in the database. Zero for a fresh file."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL PRIMARY KEY)"
    )
    row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def migrate(connection: sqlite3.Connection) -> int:
    """Apply any outstanding migrations. Returns the resulting version."""
    version = current_version(connection)

    if version > SCHEMA_VERSION:
        raise MigrationError(
            f"Database schema is version {version}, but this build only understands "
            f"{SCHEMA_VERSION}. Upgrade CerePulse, or delete the cache to start fresh."
        )
    if version == SCHEMA_VERSION:
        return version

    for step in range(version, len(MIGRATIONS)):
        number = step + 1
        logger.info("Applying database migration {}", number)
        try:
            with connection:  # commits on success, rolls back on error
                MIGRATIONS[step](connection)
                connection.execute("INSERT INTO schema_version (version) VALUES (?)", (number,))
        except sqlite3.Error as exc:
            raise MigrationError(f"Migration {number} failed: {exc}") from exc

    logger.info("Database schema is at version {}", SCHEMA_VERSION)
    return SCHEMA_VERSION
