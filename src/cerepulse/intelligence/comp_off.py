"""Comp-off, one credit at a time: what earned it, what spent it, and what is left.

The leave register reports a single comp-off figure, and it is the wrong one to plan with:
it counts credits still awaiting approval, days already booked against credits, and credits
that lapsed unused. In November 2026 it said 8 while the portal would let 5 be spent. This
module rebuilds the balance the portal actually enforces, credit by credit, from what the
portal publishes:

* **Credits** are comp-off applications (the CO+ list), whole or half days, dated by the day
  worked. One still In Process is not yet a credit: the portal does not offer it.
* **Spends** are *leave* applications typed ``CO-`` (fact 8). One still In Process holds its
  credit already — the portal stops offering it the moment the leave is filed.
* **Which credit a spend drew on** is recorded by the portal on the application itself
  (:class:`~cerepulse.models.leave.CompOffLink`). Where it is known, that is the answer.
  Where it is not — a link not read yet, or one the portal left empty — the oldest credit
  still valid on the day is assumed, of the same size when there is one, because credits are
  spent whole: a full day takes a full credit and a half day a half. That rule reproduces
  every link the portal has recorded so far.
* **A credit lapses** 90 days after it was earned (the form's ``hdnCOAdjDaysSetting``, and
  "Utilize your CO+ within 90 days" on the Leave Rules page), and never outlives the
  financial year it was earned in: the portal's own lookups are scoped to the financial year,
  and the register opens each April at zero with the previous March's unused credit gone.
  Earned date rather than approval date, because no approval date is published (fact 14).

The register still matters, for what the applications cannot say: a credit or a spend with no
application behind it is taken from its dated rows.

Pure: no network, no database, no clock. ``today`` is passed in.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from enum import Enum

from cerepulse.intelligence.wording import clean_remark
from cerepulse.models.application import Application, ApplicationKind, RequestStatus
from cerepulse.models.leave import CompOffLink, CompOffSnapshot, LeaveCategory, LeaveTransaction

#: Days a credit stays usable after it is earned. Company policy, confirmed by the portal.
VALIDITY_DAYS = 90
#: Month and day the financial year ends; no credit outlives the year it was earned in.
FINANCIAL_YEAR_END = (3, 31)

#: Application states that count. A rejected or lapsed application never moved a balance.
_LIVE = (RequestStatus.APPROVED, RequestStatus.IN_PROCESS)


class CreditState(Enum):
    """What became of one credit."""

    AVAILABLE = "available"
    #: Spent by a leave that is still awaiting approval. Not available: the portal already
    #: holds it for that leave.
    BOOKED = "booked"
    USED = "used"
    LAPSED = "lapsed"
    #: The credit's own application is still awaiting approval.
    PENDING = "pending"

    @property
    def label(self) -> str:
        return {
            CreditState.AVAILABLE: "Available",
            CreditState.BOOKED: "Booked",
            CreditState.USED: "Used",
            CreditState.LAPSED: "Lapsed",
            CreditState.PENDING: "Awaiting approval",
        }[self]


@dataclass(frozen=True, slots=True)
class CompOffSpend:
    """One day (or half) of comp-off taken, and how sure we are which credit paid for it."""

    day: date
    days: float
    #: The leave application, or ``""`` for a register row no application explains.
    app_id: str = ""
    #: The leave is still awaiting approval.
    pending: bool = False
    #: The portal itself recorded which credit this drew on. False means it was inferred.
    from_portal: bool = False


@dataclass(frozen=True, slots=True)
class CompOffCredit:
    """One credit: when it was earned, until when it can be used, and what became of it."""

    earned_on: date
    days: float
    expires_on: date
    state: CreditState
    #: The comp-off application that earned it, or ``""`` for a register-only credit.
    app_id: str = ""
    reason: str = ""
    spent_by: tuple[CompOffSpend, ...] = ()

    @property
    def used(self) -> float:
        return sum(spend.days for spend in self.spent_by)

    @property
    def remaining(self) -> float:
        """What is still on it — available, lapsed or pending, depending on :attr:`state`."""
        return max(0.0, self.days - self.used)

    def days_left(self, today: date) -> int:
        return (self.expires_on - today).days


@dataclass(frozen=True, slots=True)
class CompOffLedger:
    """Every credit, oldest first, and the figures the screens ask for."""

    credits: tuple[CompOffCredit, ...]
    assessed_on: date
    #: Spends no credit here could pay for — taken against a credit older than the history
    #: the app holds, most likely. Reported, never charged to the wrong credit.
    unmatched: tuple[CompOffSpend, ...] = ()
    #: The portal's own balance, when it was read; a cross-check, never the source.
    portal: CompOffSnapshot | None = None
    #: Spends whose credit was inferred rather than read off the application.
    inferred: tuple[str, ...] = ()

    @property
    def available(self) -> tuple[CompOffCredit, ...]:
        """Credits that can still be spent, soonest to lapse first."""
        return tuple(
            sorted(
                (credit for credit in self.credits if credit.state is CreditState.AVAILABLE),
                key=lambda credit: (credit.expires_on, credit.earned_on),
            )
        )

    @property
    def usable(self) -> float:
        """The balance the portal will actually let be spent today."""
        return sum(credit.remaining for credit in self.available)

    @property
    def next_expiry(self) -> CompOffCredit | None:
        available = self.available
        return available[0] if available else None

    @property
    def pending_days(self) -> float:
        return sum(c.days for c in self.credits if c.state is CreditState.PENDING)

    @property
    def booked_days(self) -> float:
        return sum(c.used for c in self.credits if c.state is CreditState.BOOKED)

    def expiring_within(self, days: int) -> float:
        """How much of the usable balance lapses within ``days`` of the assessment."""
        return sum(
            credit.remaining
            for credit in self.available
            if 0 <= credit.days_left(self.assessed_on) <= days
        )

    def lapsed_since(self, start: date) -> tuple[CompOffCredit, ...]:
        """Credits that lapsed with something left on them, on or after ``start``."""
        return tuple(
            credit
            for credit in self.credits
            if credit.state is CreditState.LAPSED and credit.expires_on >= start
        )

    def credits_for(self, app_id: str) -> tuple[CompOffCredit, ...]:
        """The credits a leave application drew on."""
        if not app_id:
            return ()
        return tuple(
            credit
            for credit in self.credits
            if any(spend.app_id == app_id for spend in credit.spent_by)
        )

    def credit_earned(self, app_id: str) -> CompOffCredit | None:
        """The credit a comp-off application earned."""
        return next((c for c in self.credits if app_id and c.app_id == app_id), None)

    @property
    def portal_agrees(self) -> bool | None:
        """Whether today's portal balance matches ours. ``None`` with no reading for today."""
        if self.portal is None or self.portal.as_of != self.assessed_on:
            return None
        return abs(self.portal.balance - self.usable) < 1e-9


def expiry_of(
    earned_on: date,
    *,
    validity_days: int = VALIDITY_DAYS,
    financial_year_end: tuple[int, int] = FINANCIAL_YEAR_END,
) -> date:
    """The last day a credit can be used: 90 days on, or the financial year's end if sooner."""
    month, day = financial_year_end
    year_end = date(earned_on.year, month, day)
    if year_end < earned_on:
        year_end = date(earned_on.year + 1, month, day)
    return min(earned_on + timedelta(days=validity_days), year_end)


def is_comp_off_spend(application: Application) -> bool:
    """A leave application typed ``CO-`` — the only way comp-off is ever spent."""
    code = application.leave_type.replace(" ", "").upper()
    return application.kind is ApplicationKind.LEAVE and code.startswith("CO-")


def comp_off_ledger(
    *,
    today: date,
    applications: Sequence[Application] = (),
    links: Mapping[str, Sequence[CompOffLink]] | None = None,
    register: Sequence[LeaveTransaction] = (),
    snapshot: CompOffSnapshot | None = None,
    validity_days: int = VALIDITY_DAYS,
    financial_year_end: tuple[int, int] = FINANCIAL_YEAR_END,
) -> CompOffLedger:
    """Replay every credit and spend, and say what each credit is now."""
    credits = _credits(applications, register, validity_days, financial_year_end)
    spends = _spends(applications, register)
    links = links or {}

    unmatched: list[CompOffSpend] = []
    inferred: list[str] = []
    # The portal's own links first, so an inferred spend can never take a credit the portal
    # says another leave already holds.
    computed: list[CompOffSpend] = []
    for app_id, units in _by_application(spends):
        recorded = [link for link in links.get(app_id, ()) if link.credit_date is not None]
        if not recorded:
            computed.extend(units)
            continue
        for index, link in enumerate(recorded):
            unit = units[min(index, len(units) - 1)]
            target = next(
                (c for c in credits if c.earned_on == link.credit_date and c.remaining > 0),
                None,
            )
            amount = link.days or unit.days
            spend = CompOffSpend(unit.day, amount, app_id, unit.pending, from_portal=True)
            if target is None:
                unmatched.append(spend)
                continue
            paid = min(amount, target.remaining)
            target.take(spend, paid)
            if amount - paid > 0:
                unmatched.append(replace(spend, days=amount - paid))

    for spend in sorted(computed, key=lambda item: (item.day, item.app_id)):
        unpaid = _infer(credits, spend)
        if unpaid > 0:
            unmatched.append(replace(spend, days=unpaid))
        if unpaid < spend.days and spend.app_id and spend.app_id not in inferred:
            inferred.append(spend.app_id)

    return CompOffLedger(
        credits=tuple(credit.settle(today) for credit in credits),
        assessed_on=today,
        unmatched=tuple(unmatched),
        portal=snapshot,
        inferred=tuple(inferred),
    )


# --- the working set ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Credit:
    earned_on: date
    days: float
    expires_on: date
    pending: bool
    app_id: str = ""
    reason: str = ""
    spent_by: list[CompOffSpend] = field(default_factory=list)

    @property
    def remaining(self) -> float:
        return max(0.0, self.days - sum(spend.days for spend in self.spent_by))

    def take(self, spend: CompOffSpend, amount: float) -> None:
        self.spent_by.append(
            CompOffSpend(spend.day, amount, spend.app_id, spend.pending, spend.from_portal)
        )

    def settle(self, today: date) -> CompOffCredit:
        if self.pending and not self.spent_by:
            state = CreditState.PENDING
        elif self.remaining <= 0:
            state = (
                CreditState.BOOKED
                if any(spend.pending for spend in self.spent_by)
                else CreditState.USED
            )
        elif self.expires_on < today:
            state = CreditState.LAPSED
        else:
            state = CreditState.AVAILABLE
        return CompOffCredit(
            earned_on=self.earned_on,
            days=self.days,
            expires_on=self.expires_on,
            state=state,
            app_id=self.app_id,
            reason=self.reason,
            spent_by=tuple(self.spent_by),
        )


def _units(start: date, end: date, days: float) -> list[tuple[date, float]]:
    """An application split into the days it covers: whole days, then any half."""
    if start >= end or days <= 1:
        return [(start, days or 1.0)]
    units: list[tuple[date, float]] = []
    left, when = days, start
    while left > 0 and when <= end:
        amount = 1.0 if left >= 1 else left
        units.append((when, amount))
        left -= amount
        when += timedelta(days=1)
    return units


def _credits(
    applications: Sequence[Application],
    register: Sequence[LeaveTransaction],
    validity_days: int,
    financial_year_end: tuple[int, int],
) -> list[_Credit]:
    def expiry(when: date) -> date:
        return expiry_of(when, validity_days=validity_days, financial_year_end=financial_year_end)

    credits: list[_Credit] = []
    # Every day a comp-off application covers, whatever became of it: the application is the
    # authority on that day, so a rejected one cannot be resurrected from a register row.
    explained: set[date] = set()
    for filed in applications:
        if filed.kind is not ApplicationKind.COMP_OFF:
            continue
        units = _units(filed.start, filed.end, filed.days)
        explained.update(when for when, _amount in units)
        if filed.status not in _LIVE:
            continue
        for when, amount in units:
            credits.append(
                _Credit(
                    earned_on=when,
                    days=amount,
                    expires_on=expiry(when),
                    pending=filed.is_open,
                    app_id=filed.app_id,
                    reason=clean_remark(filed.remark),
                )
            )

    # Register credits nothing explains. The register repeats a credit when it is re-listed,
    # so one per day and amount.
    seen: set[tuple[date, float]] = set()
    for row in register:
        earned_on = row.transaction_date
        if (
            earned_on is None
            or row.credit_days <= 0
            or LeaveCategory.classify(row.leave_type) is not LeaveCategory.COMP_OFF
            or earned_on in explained
            or (earned_on, row.credit_days) in seen
        ):
            continue
        seen.add((earned_on, row.credit_days))
        credits.append(
            _Credit(
                earned_on=earned_on,
                days=row.credit_days,
                expires_on=expiry(earned_on),
                pending=False,
                reason=clean_remark(row.remark.rsplit("|", 1)[-1]),
            )
        )
    return sorted(credits, key=lambda credit: (credit.earned_on, credit.app_id))


def _spends(
    applications: Sequence[Application], register: Sequence[LeaveTransaction]
) -> list[CompOffSpend]:
    spends: list[CompOffSpend] = []
    explained: set[date] = set()
    for filed in applications:
        if not is_comp_off_spend(filed):
            continue
        units = _units(filed.start, filed.end, filed.days)
        explained.update(when for when, _amount in units)
        if filed.status not in _LIVE:
            continue
        spends += [
            CompOffSpend(when, amount, filed.app_id, pending=filed.is_open)
            for when, amount in units
        ]

    seen: set[tuple[date, float]] = set()
    for row in register:
        when = row.transaction_date
        if (
            when is None
            or row.consumed_days <= 0
            or LeaveCategory.classify(row.leave_type) is not LeaveCategory.COMP_OFF
            or when in explained
            or (when, row.consumed_days) in seen
        ):
            continue
        seen.add((when, row.consumed_days))
        spends.append(CompOffSpend(when, row.consumed_days))
    return spends


def _by_application(spends: list[CompOffSpend]) -> list[tuple[str, list[CompOffSpend]]]:
    grouped: dict[str, list[CompOffSpend]] = {}
    loose: list[tuple[str, list[CompOffSpend]]] = []
    for spend in spends:
        if spend.app_id:
            grouped.setdefault(spend.app_id, []).append(spend)
        else:
            loose.append(("", [spend]))
    return [*grouped.items(), *loose]


def _infer(credits: list[_Credit], spend: CompOffSpend) -> float:
    """Charge a spend to the oldest credit valid on its day, same size first.

    Returns what could not be paid for, which is reported rather than charged to a credit
    that was not valid on the day.
    """
    valid = [
        credit
        for credit in credits
        if not credit.pending
        and credit.remaining > 0
        and credit.earned_on <= spend.day <= credit.expires_on
    ]
    exact = next((credit for credit in valid if credit.remaining == spend.days), None)
    if exact is not None:
        exact.take(spend, spend.days)
        return 0.0
    left = spend.days
    for credit in valid:
        if left <= 0:
            break
        amount = min(left, credit.remaining)
        credit.take(spend, amount)
        left -= amount
    return max(0.0, left)
