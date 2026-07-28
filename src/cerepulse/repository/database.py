"""Database connection management.

WAL mode is on because the UI reads while a background sync writes; without it the reader
would block. ``foreign_keys`` is enabled per connection — SQLite defaults it off, so cascade
deletes silently would not fire otherwise.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Self

from loguru import logger

from cerepulse.core.errors import RepositoryError
from cerepulse.repository.schema import migrate


class Database:
    """An open SQLite database, migrated to the current schema.

    Usable as a context manager. ``:memory:`` is accepted for tests.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = path
        self._connection: sqlite3.Connection | None = None

    # --- lifecycle ------------------------------------------------------------------

    def connect(self) -> Self:
        if self._connection is not None:
            return self

        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)

        try:
            connection = sqlite3.connect(
                str(self.path),
                # Sync runs on a worker thread while the GUI thread reads.
                check_same_thread=False,
                isolation_level=None,  # explicit transactions via `with connection`
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
        except sqlite3.Error as exc:
            raise RepositoryError(f"Could not open the local cache at {self.path}: {exc}") from exc

        self._connection = connection
        migrate(connection)
        return self

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> Self:
        return self.connect()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    # --- access ---------------------------------------------------------------------

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RepositoryError("Database is not open; call connect() first")
        return self._connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a unit of work atomically.

        Multi-table writes go through here so a failed sync cannot leave a month half
        written (Chapter 08 section 8).
        """
        connection = self.connection
        try:
            connection.execute("BEGIN")
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")

    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
        try:
            return self.connection.execute(sql, parameters)
        except sqlite3.Error as exc:
            raise RepositoryError(f"Query failed: {exc}") from exc

    # --- maintenance ----------------------------------------------------------------

    def clear_cache(self) -> None:
        """Drop all cached data, keeping the schema. Backs the Settings action."""
        tables = (
            "punch",
            "attendance_day",
            "leave_balance",
            "leave_transaction",
            "swipe_request",
            "holiday",
            "sync_metadata",
            "employee",
        )
        with self.transaction() as connection:
            for table in tables:
                connection.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table names
        logger.info("Local cache cleared")

    def vacuum(self) -> None:
        self.connection.execute("VACUUM")


def open_database(path: Path | str) -> Database:
    """Open and migrate a database in one call."""
    return Database(path).connect()
