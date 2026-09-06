"""Pay statements: fetched when asked, held encrypted, shown from the cache.

Unlike attendance nothing here refreshes on the tick. A payslip changes once a month and
the pages behind it are the heaviest on the portal, so the screen reads the cache and the
user's Refresh is what fetches — the CTC, the monthly grid, and every slip period the cache
does not yet hold. Nothing is read from the pages beyond the figures.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime

from loguru import logger

from cerepulse.core.config import AppConfig
from cerepulse.core.errors import CerePulseError
from cerepulse.models.pay import (
    CtcLine,
    CtcStatement,
    MonthlyRow,
    MonthlyStatement,
    PayLine,
    Payslip,
)
from cerepulse.repository.pay import PayRepository
from cerepulse.services.portal import PortalGateway

KIND_CTC = "ctc"
KIND_MONTHLY = "monthly"
KIND_PAYSLIP = "payslip"


@dataclass(frozen=True, slots=True)
class PaySnapshot:
    ctc: CtcStatement | None
    monthly: MonthlyStatement | None
    #: Newest first.
    payslips: tuple[Payslip, ...]
    last_synced: datetime | None

    @property
    def latest(self) -> Payslip | None:
        return self.payslips[0] if self.payslips else None

    @property
    def is_empty(self) -> bool:
        return self.ctc is None and self.monthly is None and not self.payslips


class PayService:
    def __init__(
        self, *, gateway: PortalGateway, repository: PayRepository, config: AppConfig
    ) -> None:
        self._gateway = gateway
        self._repo = repository
        self._config = config

    def use_config(self, config: AppConfig) -> None:
        self._config = config

    @property
    def enabled(self) -> bool:
        return self._config.pay.enabled

    # --- reading --------------------------------------------------------------------------

    def load(self, employee_code: str) -> PaySnapshot:
        """What the cache holds. Never touches the network."""
        ctc_raw = self._repo.find(employee_code, KIND_CTC, "")
        monthly_raw = self._repo.find(employee_code, KIND_MONTHLY, "current")
        slips = [
            _payslip_from(payload)
            for _period, payload in sorted(
                self._repo.find_all(employee_code, KIND_PAYSLIP).items(), reverse=True
            )
        ]
        return PaySnapshot(
            ctc=_ctc_from(ctc_raw) if ctc_raw else None,
            monthly=_monthly_from(monthly_raw) if monthly_raw else None,
            payslips=tuple(slip for slip in slips if slip is not None),
            last_synced=self._repo.last_synced(employee_code),
        )

    # --- fetching -------------------------------------------------------------------------

    def refresh(self, employee_code: str, *, now: datetime | None = None) -> PaySnapshot:
        """Fetch the CTC, the monthly grid, and every slip period not yet cached.

        The newest period is fetched again each time: the current month's slip appears
        partway through the month and the figure on it can change before it settles.
        Each document is saved as it lands, so a failure halfway leaves what did arrive.
        """
        stamp = now or datetime.now()
        failures: list[str] = []

        try:
            self._repo.save(
                employee_code, KIND_CTC, "", asdict(self._gateway.fetch_ctc()), synced_at=stamp
            )
        except CerePulseError as exc:
            failures.append(f"CTC: {exc}")
        try:
            self._repo.save(
                employee_code,
                KIND_MONTHLY,
                "current",
                asdict(self._gateway.fetch_monthly_pay()),
                synced_at=stamp,
            )
        except CerePulseError as exc:
            failures.append(f"monthly report: {exc}")

        try:
            periods = self._gateway.fetch_payslip_periods()
        except CerePulseError as exc:
            periods = []
            failures.append(f"payslips: {exc}")
        held = set(self._repo.find_all(employee_code, KIND_PAYSLIP))
        wanted = [
            value
            for index, (value, _label) in enumerate(periods)
            if index == 0 or value not in held
        ]
        for period in wanted:
            try:
                slip = self._gateway.fetch_payslip(period)
            except CerePulseError as exc:
                failures.append(f"payslip {period}: {exc}")
                continue
            self._repo.save(employee_code, KIND_PAYSLIP, period, _payslip_to(slip), synced_at=stamp)

        for failure in failures:
            logger.warning("Pay refresh: {}", failure)
        return self.load(employee_code)

    def forget(self, employee_code: str) -> None:
        self._repo.clear(employee_code)


# --- (de)serialisation: JSON is what the blob holds --------------------------------------------


def _payslip_to(slip: Payslip) -> dict[str, object]:
    payload = asdict(slip)
    payload["period"] = slip.period.isoformat()
    return payload


def _payslip_from(payload: dict[str, object]) -> Payslip | None:
    try:
        return Payslip(
            period=date.fromisoformat(str(payload["period"])),
            earnings=_lines(payload.get("earnings")),
            deductions=_lines(payload.get("deductions")),
            variables=_lines(payload.get("variables")),
            gross=_number(payload.get("gross")),
            total_deductions=_number(payload.get("total_deductions")),
            net_pay=_number(payload.get("net_pay")),
            in_words=str(payload.get("in_words") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _number(raw: object) -> float:
    return float(raw) if isinstance(raw, (int, float, str)) else 0.0


def _lines(raw: object) -> tuple[PayLine, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(
        PayLine(str(item["label"]), float(item["amount"]))
        for item in raw
        if isinstance(item, dict) and "label" in item and "amount" in item
    )


def _ctc_from(payload: dict[str, object]) -> CtcStatement | None:
    raw = payload.get("lines")
    if not isinstance(raw, list):
        return None
    lines = tuple(
        CtcLine(
            label=str(item.get("label", "")),
            monthly=None if item.get("monthly") is None else float(item["monthly"]),
            yearly=None if item.get("yearly") is None else float(item["yearly"]),
            section=str(item.get("section", "")),
            is_total=bool(item.get("is_total")),
        )
        for item in raw
        if isinstance(item, dict)
    )
    return CtcStatement(for_year=str(payload.get("for_year", "")), lines=lines)


def _monthly_from(payload: dict[str, object]) -> MonthlyStatement | None:
    raw = payload.get("rows")
    months = payload.get("months")
    if not isinstance(raw, list) or not isinstance(months, list):
        return None
    rows = tuple(
        MonthlyRow(
            label=str(item.get("label", "")),
            values=tuple(None if v is None else float(v) for v in item.get("values", [])),
        )
        for item in raw
        if isinstance(item, dict)
    )
    return MonthlyStatement(
        period_label=str(payload.get("period_label", "")),
        months=tuple(str(m) for m in months),
        rows=rows,
    )


__all__ = ["KIND_CTC", "KIND_MONTHLY", "KIND_PAYSLIP", "PayService", "PaySnapshot"]
