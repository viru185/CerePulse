"""The pay service fetches on request only, and only what the cache does not hold."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from cerepulse.core import dpapi
from cerepulse.core.config import AppConfig, PayConfig
from cerepulse.core.errors import TransportError
from cerepulse.models.pay import (
    CtcLine,
    CtcStatement,
    MonthlyRow,
    MonthlyStatement,
    PayLine,
    Payslip,
)
from cerepulse.repository.database import Database, open_database
from cerepulse.repository.pay import PayRepository
from cerepulse.services.pay import PayService

pytestmark = pytest.mark.skipif(not dpapi.available(), reason="DPAPI is Windows-only")
EMPLOYEE = "CIPL00364"


def _slip(period: str, net: float) -> Payslip:
    return Payslip(
        period=date(int(period[:4]), int(period[4:]), 1),
        earnings=(PayLine("E Basic", net),),
        deductions=(),
        variables=(),
        gross=net,
        total_deductions=0.0,
        net_pay=net,
        in_words="some words",
    )


class FakePayGateway:
    def __init__(self) -> None:
        self.periods = [("202607", "July , 2026"), ("202606", "June , 2026")]
        self.slips = {"202607": _slip("202607", 100.0), "202606": _slip("202606", 90.0)}
        self.fetched: list[str] = []
        self.fail_ctc = False

    def fetch_ctc(self) -> CtcStatement:
        self.fetched.append("ctc")
        if self.fail_ctc:
            raise TransportError("portal down")
        return CtcStatement(
            "2026-2027",
            (CtcLine("(A+C) Cost To Company", 10.0, 120.0, "Outside Payroll Benefits", True),),
        )

    def fetch_monthly_pay(self) -> MonthlyStatement:
        self.fetched.append("monthly")
        return MonthlyStatement(
            "April , 2026 - March , 2027", ("Apr 2026",), (MonthlyRow("Net Salary", (1.0,)),)
        )

    def fetch_payslip_periods(self) -> list[tuple[str, str]]:
        self.fetched.append("periods")
        return list(self.periods)

    def fetch_payslip(self, period: str) -> Payslip:
        self.fetched.append(period)
        return self.slips[period]


@pytest.fixture
def database():  # type: ignore[no-untyped-def]
    db = open_database(":memory:")
    yield db
    db.close()


@pytest.fixture
def service(database: Database) -> tuple[PayService, FakePayGateway]:
    gateway = FakePayGateway()
    config = AppConfig(pay=PayConfig(enabled=True))
    return (
        PayService(gateway=gateway, repository=PayRepository(database), config=config),  # type: ignore[arg-type]
        gateway,
    )


def test_load_never_touches_the_gateway(service: tuple[PayService, FakePayGateway]) -> None:
    pay, gateway = service
    snapshot = pay.load(EMPLOYEE)
    assert snapshot.is_empty
    assert gateway.fetched == []


def test_refresh_fetches_everything_then_only_the_newest(
    service: tuple[PayService, FakePayGateway],
) -> None:
    pay, gateway = service
    snapshot = pay.refresh(EMPLOYEE, now=datetime(2026, 9, 6, 10))
    assert gateway.fetched == ["ctc", "monthly", "periods", "202607", "202606"]
    assert [slip.label for slip in snapshot.payslips] == ["July 2026", "June 2026"]
    assert snapshot.latest is not None and snapshot.latest.net_pay == 100.0
    assert snapshot.ctc is not None and snapshot.ctc.cost_to_company is not None
    assert snapshot.monthly is not None and snapshot.monthly.months == ("Apr 2026",)
    assert snapshot.last_synced == datetime(2026, 9, 6, 10)

    gateway.fetched.clear()
    gateway.slips["202607"] = _slip("202607", 101.0)
    again = pay.refresh(EMPLOYEE)
    # The newest slip can still change; the settled one is not asked for twice.
    assert gateway.fetched == ["ctc", "monthly", "periods", "202607"]
    assert again.latest is not None and again.latest.net_pay == 101.0
    assert again.latest.in_words == "some words"


def test_one_page_failing_keeps_the_rest(service: tuple[PayService, FakePayGateway]) -> None:
    pay, gateway = service
    gateway.fail_ctc = True
    snapshot = pay.refresh(EMPLOYEE)
    assert snapshot.ctc is None
    assert snapshot.monthly is not None
    assert len(snapshot.payslips) == 2


def test_forget_empties_the_cache(service: tuple[PayService, FakePayGateway]) -> None:
    pay, _gateway = service
    pay.refresh(EMPLOYEE)
    pay.forget(EMPLOYEE)
    assert pay.load(EMPLOYEE).is_empty
