"""Database connection management.

WAL mode is on because the UI reads while a background sync writes; without it the reader
would block. ``foreign_keys`` is enabled per connection — SQLite defaults it off, so cascade
deletes silently would not fire otherwise.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
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
            connection = self._open()
        except sqlite3.DatabaseError as exc:
            if not _is_corruption(exc) or not isinstance(self.path, Path):
                raise RepositoryError(
                    f"Could not open the local cache at {self.path}: {exc}"
                ) from exc
            # Every byte in here can be fetched again, and refusing to start is a far worse
            # outcome than re-syncing. The damaged file is kept rather than deleted, since a
            # corrupt database is evidence about how it got that way.
            self._quarantine(exc)
            try:
                connection = self._open()
            except sqlite3.Error as retry_exc:
                raise RepositoryError(
                    f"Could not open the local cache at {self.path}: {retry_exc}"
                ) from retry_exc
        except sqlite3.Error as exc:
            raise RepositoryError(f"Could not open the local cache at {self.path}: {exc}") from exc

        self._connection = connection
        migrate(connection)
        return self

    def _open(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.path),
            # Sync runs on a worker thread while the GUI thread reads.
            check_same_thread=False,
            isolation_level=None,  # explicit transactions via `with connection`
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
        except sqlite3.Error:
            # sqlite3.connect() succeeds against a damaged file — it is the first statement
            # that fails — and on Windows the open handle then blocks any attempt to move
            # the file aside. Closing here is what makes recovery possible at all.
            connection.close()
            raise
        return connection

    def _quarantine(self, exc: sqlite3.DatabaseError) -> None:
        """Move a damaged cache aside, sidecars and all, so a fresh one can be created.

        The ``-wal`` and ``-shm`` files have to go with it. Leaving a write-ahead log next
        to a newly created database is how a merely damaged cache becomes an unopenable one.
        """
        assert isinstance(self.path, Path)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        logger.error("Local cache is damaged ({}); starting a fresh one", exc)

        for suffix in ("", "-wal", "-shm"):
            source = Path(f"{self.path}{suffix}")
            if not source.exists():
                continue
            target = Path(f"{self.path}.corrupt-{stamp}{suffix}")
            try:
                source.replace(target)
                logger.info("Kept the damaged file as {}", target.name)
            except OSError as move_error:  # pragma: no cover — the retry will report it
                logger.warning("Could not move {}: {}", source.name, move_error)

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
            # Omitted until 0.13, so "clear the cache" left every filed leave, outdoor-duty
            # and comp-off application behind — the one table a stuck sync most needs
            # cleared.
            "application",
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


#: SQLite's wording for a file it cannot make sense of. Matched on text because the codes
#: that distinguish them (SQLITE_CORRUPT, SQLITE_NOTADB) are not exposed on the exception.
_CORRUPTION_SIGNS = (
    "malformed",
    "not a database",
    "file is encrypted",
    "database disk image",
)


def _is_corruption(exc: sqlite3.DatabaseError) -> bool:
    """Whether the file is damaged, as opposed to merely busy or unreadable.

    A locked or permission-denied database must not be quarantined — it is fine, and moving
    it aside would destroy a working cache over a transient problem.
    """
    message = str(exc).lower()
    return any(sign in message for sign in _CORRUPTION_SIGNS)


def open_database(path: Path | str) -> Database:
    """Open and migrate a database in one call."""
    return Database(path).connect()
