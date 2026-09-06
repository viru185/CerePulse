"""Pay documents: JSON in, DPAPI blob at rest, JSON out — and only for the user who wrote it."""

from __future__ import annotations

import json
from datetime import datetime

from loguru import logger

from cerepulse.core import dpapi
from cerepulse.core.errors import RepositoryError
from cerepulse.repository.database import Database


class PayRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save(
        self,
        employee_code: str,
        kind: str,
        period: str,
        payload: dict[str, object],
        *,
        synced_at: datetime | None = None,
    ) -> None:
        try:
            blob = dpapi.protect(json.dumps(payload).encode("utf-8"))
        except OSError as exc:
            raise RepositoryError(f"Could not protect the pay document: {exc}") from exc
        stamp = (synced_at or datetime.now()).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO pay_document (employee_code, kind, period, blob, synced_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (employee_code, kind, period) DO UPDATE SET
                    blob = excluded.blob, synced_at = excluded.synced_at
                """,
                (employee_code, kind, period, blob, stamp),
            )

    def find(self, employee_code: str, kind: str, period: str) -> dict[str, object] | None:
        row = self.database.execute(
            "SELECT blob FROM pay_document WHERE employee_code = ? AND kind = ? AND period = ?",
            (employee_code, kind, period),
        ).fetchone()
        return self._open(row["blob"]) if row is not None else None

    def find_all(self, employee_code: str, kind: str) -> dict[str, dict[str, object]]:
        """Every document of a kind, keyed by period. Unreadable blobs are skipped, not fatal."""
        rows = self.database.execute(
            "SELECT period, blob FROM pay_document "
            "WHERE employee_code = ? AND kind = ? ORDER BY period",
            (employee_code, kind),
        ).fetchall()
        found: dict[str, dict[str, object]] = {}
        for row in rows:
            payload = self._open(row["blob"])
            if payload is not None:
                found[str(row["period"])] = payload
        return found

    def last_synced(self, employee_code: str) -> datetime | None:
        row = self.database.execute(
            "SELECT MAX(synced_at) AS at FROM pay_document WHERE employee_code = ?",
            (employee_code,),
        ).fetchone()
        return datetime.fromisoformat(row["at"]) if row is not None and row["at"] else None

    def clear(self, employee_code: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM pay_document WHERE employee_code = ?", (employee_code,))

    @staticmethod
    def _open(blob: bytes) -> dict[str, object] | None:
        # A blob written under another account, or on another machine, is not ours to read.
        # It is re-fetched, not reported: the figures are one sync away.
        try:
            payload = json.loads(dpapi.unprotect(bytes(blob)).decode("utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("A pay document could not be opened and will be re-fetched: {}", exc)
            return None
        return payload if isinstance(payload, dict) else None


__all__ = ["PayRepository"]
