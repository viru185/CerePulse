"""Starting up with a damaged cache.

Found the hard way: two processes wrote to one SQLite file and left a four-kilobyte stub
beside a four-megabyte write-ahead log. Every byte in that cache is re-fetchable, so
refusing to start over it is the worst possible response — the app becomes unusable to
protect data it can simply ask for again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cerepulse.core.errors import RepositoryError
from cerepulse.repository.database import Database, _is_corruption, open_database


def test_a_damaged_cache_is_replaced_rather_than_fatal(tmp_path: Path) -> None:
    target = tmp_path / "cerepulse.db"
    target.write_bytes(b"this is definitely not a database" * 200)

    database = open_database(target)

    assert database.connection.execute("SELECT 1").fetchone()[0] == 1
    database.close()


def test_the_damaged_file_is_kept_not_deleted(tmp_path: Path) -> None:
    """A corrupt database is evidence about how it got that way."""
    target = tmp_path / "cerepulse.db"
    target.write_bytes(b"garbage" * 500)

    open_database(target).close()

    kept = list(tmp_path.glob("cerepulse.db.corrupt-*"))
    assert len(kept) == 1
    assert kept[0].read_bytes().startswith(b"garbage")


def test_damage_that_only_a_migration_touches_is_still_survivable(tmp_path: Path) -> None:
    """The quarantine used to cover only the *open*, whose worst statement is a PRAGMA.

    Corruption living inside a table therefore opened cleanly and detonated during
    ``migrate`` instead — and a MigrationError out of ``connect`` means the application
    does not start at all, which inverts the whole point of the quarantine. It matters
    more from 0.14 on: migration 006 rebuilds a table rather than adding a column, so it
    reads and rewrites every row and touches pages a lighter migration walks past.

    Simulated by corrupting the page bytes of a real, already-migrated database — a file
    that opens and answers a PRAGMA, then fails once something reads the table.
    """
    target = tmp_path / "cerepulse.db"
    open_database(target).close()  # a genuine, fully migrated database

    raw = bytearray(target.read_bytes())
    # Leave the 100-byte header intact so the file still identifies as SQLite and opens.
    for offset in range(200, len(raw)):
        raw[offset] = 0x5A
    target.write_bytes(bytes(raw))

    database = open_database(target)

    assert database.connection.execute("SELECT 1").fetchone()[0] == 1
    assert list(tmp_path.glob("cerepulse.db.corrupt-*"))
    database.close()


def test_no_stale_write_ahead_log_survives_the_recovery(tmp_path: Path) -> None:
    """Leaving one beside a fresh database is how damage becomes unopenable.

    Asserted as an outcome rather than as a move, because SQLite discards the log itself
    when the failed connection closes — either route is fine, a survivor is not.
    """
    target = tmp_path / "cerepulse.db"
    target.write_bytes(b"garbage" * 500)
    Path(f"{target}-wal").write_bytes(b"stale log")

    database = open_database(target)

    assert Path(f"{target}-wal").read_bytes() != b"stale log"
    assert database.connection.execute("SELECT 1").fetchone()[0] == 1
    database.close()


def test_a_healthy_cache_is_left_alone(tmp_path: Path) -> None:
    target = tmp_path / "cerepulse.db"
    first = open_database(target)
    first.connection.execute("CREATE TABLE keepme (x INTEGER)")
    first.connection.execute("INSERT INTO keepme VALUES (42)")
    first.close()

    second = open_database(target)
    assert second.connection.execute("SELECT x FROM keepme").fetchone()[0] == 42
    second.close()
    assert not list(tmp_path.glob("*.corrupt-*"))


def test_a_directory_where_the_database_should_be_still_raises(tmp_path: Path) -> None:
    """Not corruption, so it must surface rather than be quietly moved aside."""
    target = tmp_path / "cerepulse.db"
    target.mkdir()

    with pytest.raises(RepositoryError):
        Database(target).connect()


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("database disk image is malformed", True),
        ("file is not a database", True),
        ("file is encrypted or is not a database", True),
        ("database is locked", False),
        ("unable to open database file", False),
        ("attempt to write a readonly database", False),
    ],
)
def test_only_damage_counts_as_damage(message: str, expected: bool) -> None:
    """A locked database is fine; moving it aside would destroy a working cache."""
    import sqlite3

    assert _is_corruption(sqlite3.DatabaseError(message)) is expected
