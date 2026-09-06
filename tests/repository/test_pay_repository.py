"""Pay documents are unreadable at rest and re-fetched rather than reported when they are."""

from __future__ import annotations

from datetime import datetime

import pytest

from cerepulse.core import dpapi
from cerepulse.repository.database import Database
from cerepulse.repository.pay import PayRepository

from .conftest import EMPLOYEE

pytestmark = pytest.mark.skipif(not dpapi.available(), reason="DPAPI is Windows-only")


def test_round_trip_and_overwrite(database: Database) -> None:
    repo = PayRepository(database)
    repo.save(EMPLOYEE, "payslip", "202607", {"net_pay": 14295.0}, synced_at=datetime(2026, 9, 6))
    repo.save(EMPLOYEE, "payslip", "202607", {"net_pay": 14300.0}, synced_at=datetime(2026, 9, 7))
    assert repo.find(EMPLOYEE, "payslip", "202607") == {"net_pay": 14300.0}
    assert repo.find(EMPLOYEE, "payslip", "202606") is None
    assert repo.last_synced(EMPLOYEE) == datetime(2026, 9, 7)


def test_the_blob_on_disk_is_not_the_document(database: Database) -> None:
    repo = PayRepository(database)
    repo.save(EMPLOYEE, "ctc", "", {"label": "Cost To Company", "yearly": 178000.0})
    raw = database.execute("SELECT blob FROM pay_document").fetchone()["blob"]
    assert b"Cost To Company" not in bytes(raw)
    assert b"178000" not in bytes(raw)


def test_an_unreadable_blob_is_skipped_not_fatal(database: Database) -> None:
    repo = PayRepository(database)
    repo.save(EMPLOYEE, "payslip", "202606", {"net_pay": 1.0})
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO pay_document VALUES (?, 'payslip', '202607', X'00', '2026-09-06')",
            (EMPLOYEE,),
        )
    assert list(repo.find_all(EMPLOYEE, "payslip")) == ["202606"]
    assert repo.find(EMPLOYEE, "payslip", "202607") is None


def test_clear_cache_drops_pay_documents(database: Database) -> None:
    repo = PayRepository(database)
    repo.save(EMPLOYEE, "ctc", "", {"x": 1})
    database.clear_cache()
    assert repo.find(EMPLOYEE, "ctc", "") is None
