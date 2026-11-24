"""Leave intelligence: balances worth acting on, and expiry warnings.

The portal reports balances but never says "use this or lose it", so a balance sitting
untouched is a real and silent loss. Three rules apply here, and they are not the same rule:

* **PL** lapses at the end of the *financial* year, 31 March. The portal's own leave register
  corroborates this without stating it — it is scoped to the financial year, and credits from
  the previous February are simply absent from it.
* **CF** lapses at the end of the calendar year. This one is a one-off HR action rather than
  standing policy, which is exactly why it is configuration and not a constant.
* **CO+** expires 90 days after it is earned, *per credit*, and never outlives its financial
  year. Not per balance: two comp-offs earned four months apart do not lapse together. The
  register's comp-off figure is not the spendable one either — it counts credits awaiting
  approval, credits already booked and credits that lapsed — so comp-off is rebuilt credit
  by credit in :mod:`cerepulse.intelligence.comp_off` and its balance replaced with that.

None of that is protocol. It is company policy, so all of it is configurable, and where the
portal gives nothing to count from the answer is ``UNKNOWN`` rather than a guess.

One caveat is deliberately visible in the naming. The user's rule for comp-off counts 90 days
from the date the request was **approved**, and the portal does not publish an approval date
for comp-off anywhere — ``Approve Date`` exists only on the swipe-request grid. What it does
publish is the date each comp-off was *earned*, one dated row per credit in the leave
register. That is what is counted from, and :class:`ExpiryBasis` says so, so no screen can
imply a precision the data does not have.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import Enum

from cerepulse.intelligence.comp_off import CompOffLedger
from cerepulse.intelligence.insights import Insight, InsightKind, Severity
from cerepulse.models.leave import LeaveBalance, LeaveCategory

#: Days before expiry at which a balance starts being flagged.
WARNING_WINDOW_DAYS = 60
URGENT_WINDOW_DAYS = 21


class ExpiryBasis(Enum):
    """How an expiry date was arrived at, so the UI never implies false precision."""

    LEAVE_YEAR_END = "leave_year_end"
    FINANCIAL_YEAR_END = "financial_year_end"
    EARNED_PLUS_WINDOW = "earned_plus_window"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class LeavePolicy:
    """Company rules governing when leave lapses."""

    #: Month and day the leave year ends; carry-forward lapses here.
    leave_year_end: tuple[int, int] = (12, 31)
    #: Month and day the financial year ends; planned leave lapses here.
    financial_year_end: tuple[int, int] = (3, 31)
    #: Days a comp-off stays usable after it is earned.
    comp_off_validity_days: int = 90


@dataclass(frozen=True, slots=True)
class LeaveOutlook:
    """A balance with its expiry assessment."""

    balance: LeaveBalance
    expires_on: date | None
    basis: ExpiryBasis
    days_remaining: int | None
    #: Comp-off, credit by credit — earned, booked, used, lapsed or pending. ``None`` for
    #: leave types that expire as a block, and for comp-off before anything has synced.
    comp_off: CompOffLedger | None = None
    #: The day this was assessed against, carried so the lots can be re-read against the same
    #: date the headline figures used. Injected time, never `date.today()` — the rule the
    #: whole intelligence layer runs on.
    assessed_on: date | None = None

    @property
    def has_balance(self) -> bool:
        return self.balance.available_balance > 0

    @property
    def is_expired(self) -> bool:
        """The deadline has already passed — distinct from expiring soon."""
        return self.has_balance and self.days_remaining is not None and self.days_remaining < 0

    @property
    def is_at_risk(self) -> bool:
        """Still usable, but not for much longer."""
        return (
            self.has_balance
            and self.days_remaining is not None
            and 0 <= self.days_remaining <= WARNING_WINDOW_DAYS
        )

    @property
    def at_risk_days(self) -> float:
        """How much of the balance is inside the warning window, not the whole balance.

        For a leave type that expires as a block these are the same number. For comp-off they
        are not, and saying "2 days expire in 12 days" when only half a day is close is the
        kind of overstatement that gets an alert ignored.
        """
        if self.comp_off is None:
            return self.balance.available_balance if self.is_at_risk else 0.0
        return self.comp_off.expiring_within(WARNING_WINDOW_DAYS)


def analyze_leave(
    balances: list[LeaveBalance],
    *,
    today: date,
    policy: LeavePolicy | None = None,
    comp_off: CompOffLedger | None = None,
) -> list[LeaveOutlook]:
    """Assess each balance for expiry risk.

    ``comp_off`` is the credit-by-credit ledger. With it, the comp-off balance becomes what
    can actually be spent today and its deadline the next credit to lapse; without it —
    nothing synced yet — comp-off falls back to the one date on the balance, which the
    portal leaves empty in practice, so the expiry reads "unknown" rather than a guess.
    """
    policy = policy or LeavePolicy()
    return [_outlook(balance, today=today, policy=policy, ledger=comp_off) for balance in balances]


def leave_insights(outlooks: list[LeaveOutlook]) -> list[Insight]:
    """Turn expired and soon-to-expire balances into insights, most urgent first.

    Expired balances are reported as lapsed rather than as a negative countdown, and lead
    the list — a balance already lost is more important than one still saveable.
    """
    expired = sorted(
        (outlook for outlook in outlooks if outlook.is_expired),
        key=lambda outlook: outlook.days_remaining or 0,
    )
    at_risk = sorted(
        (outlook for outlook in outlooks if outlook.is_at_risk),
        key=lambda outlook: outlook.days_remaining or 0,
    )

    insights = [
        Insight(
            kind=InsightKind.LEAVE_EXPIRING,
            severity=Severity.CRITICAL,
            title=(
                f"{_days(outlook.balance.available_balance)} of "
                f"{outlook.balance.leave_type} have expired"
            ),
            detail=f"The deadline was {outlook.expires_on:%d %b %Y}.",
        )
        for outlook in expired
    ]

    # Kept short on purpose: this is a toast. The amount is the part inside the window, not
    # the whole balance — comp-off expires a credit at a time, so "2 days expire in 12 days"
    # when half a day is close overstates it, and an alert that overstates gets ignored. How
    # the date was arrived at is said on the Records card, where there is room to read it.
    insights += [
        Insight(
            kind=InsightKind.LEAVE_EXPIRING,
            severity=(
                Severity.WARNING
                if (outlook.days_remaining or 0) <= URGENT_WINDOW_DAYS
                else Severity.INFO
            ),
            title=_expiring_title(outlook),
            detail=_expiring_detail(outlook),
        )
        for outlook in at_risk
    ]
    return insights


def _first_to_go(outlook: LeaveOutlook) -> float:
    """How much lapses on the soonest date — not everything inside the window.

    Comp-off's five credits can all sit inside sixty days and lapse on five different dates;
    "5 days of comp-off expire on 31 Dec" was the result when the window's total was put
    beside the first date, and only one of them did.
    """
    if outlook.comp_off is None or outlook.expires_on is None:
        return outlook.at_risk_days
    return sum(
        credit.remaining
        for credit in outlook.comp_off.available
        if credit.expires_on == outlook.expires_on
    )


def _expiring_title(outlook: LeaveOutlook) -> str:
    amount = _first_to_go(outlook)
    if outlook.expires_on is None:
        return f"{_days(amount)} of {_label(outlook)} expiring"
    verb = "expires" if amount == 1 else "expire"
    return f"{_days(amount)} of {_label(outlook)} {verb} on {outlook.expires_on:%d %b}"


def _expiring_detail(outlook: LeaveOutlook) -> str:
    detail = f"{outlook.days_remaining} days left."
    more = outlook.at_risk_days - _first_to_go(outlook)
    if more > 0 and outlook.comp_off is not None:
        last = max(
            credit.expires_on
            for credit in outlook.comp_off.available
            if 0 <= credit.days_left(outlook.comp_off.assessed_on) <= WARNING_WINDOW_DAYS
        )
        detail += f" Another {_days(more)} by {last:%d %b}."
    return detail


def _label(outlook: LeaveOutlook) -> str:
    """``comp-off`` rather than the portal's ``CO- / CO+`` in a sentence."""
    if outlook.balance.category is LeaveCategory.COMP_OFF:
        return "comp-off"
    return outlook.balance.leave_type


def _caveat(outlook: LeaveOutlook) -> str:
    """Say where a comp-off deadline was counted from, wherever there is room to.

    The rule is 90 days from approval and the portal publishes no approval date for comp-off,
    so this counts from the earned date instead. Stating that beside the figure is the whole
    reason :class:`ExpiryBasis` exists: a date the user might book leave around has to carry
    how it was arrived at. On the timeline row, not in the toast.
    """
    if outlook.basis is not ExpiryBasis.EARNED_PLUS_WINDOW:
        return ""
    return " Counted from the date it was earned; the portal does not publish an approval date."


def _days(amount: float) -> str:
    return f"{amount:g} day{'s' if amount != 1 else ''}"


def _outlook(
    balance: LeaveBalance,
    *,
    today: date,
    policy: LeavePolicy,
    ledger: CompOffLedger | None,
) -> LeaveOutlook:
    if balance.category is LeaveCategory.COMP_OFF:
        return _comp_off_outlook(balance, today=today, policy=policy, ledger=ledger)

    expires_on, basis = _block_expiry(balance, today=today, policy=policy)
    remaining = (expires_on - today).days if expires_on else None
    return LeaveOutlook(
        balance=balance,
        expires_on=expires_on,
        basis=basis,
        days_remaining=remaining,
        assessed_on=today,
    )


def _block_expiry(
    balance: LeaveBalance, *, today: date, policy: LeavePolicy
) -> tuple[date | None, ExpiryBasis]:
    """Leave types that lapse all at once, on a fixed date in the year."""
    if balance.category is LeaveCategory.CARRY_FORWARD:
        return _next_occurrence(policy.leave_year_end, today), ExpiryBasis.LEAVE_YEAR_END
    if balance.category is LeaveCategory.PLANNED:
        # PL runs on the financial year, not the calendar one. It had no rule at all and fell
        # through to UNKNOWN, so the largest balance most people hold was the one type the
        # app never warned about.
        return _next_occurrence(policy.financial_year_end, today), ExpiryBasis.FINANCIAL_YEAR_END
    return None, ExpiryBasis.UNKNOWN


def _next_occurrence(month_day: tuple[int, int], today: date) -> date:
    month, day = month_day
    deadline = date(today.year, month, day)
    return deadline if deadline >= today else date(today.year + 1, month, day)


def _comp_off_outlook(
    balance: LeaveBalance,
    *,
    today: date,
    policy: LeavePolicy,
    ledger: CompOffLedger | None,
) -> LeaveOutlook:
    """Comp-off, one credit at a time.

    The balance shown is the ledger's usable figure, not the register's: the register counts
    credits awaiting approval, credits already booked against a leave and credits that lapsed
    unused, and the user planned a day off around an 8 that was really a 5.
    """
    if ledger is None or not ledger.credits:
        # Nothing synced to rebuild it from. Fall back to whatever date the balance carries,
        # which the portal leaves empty in practice.
        if balance.as_of is None:
            return LeaveOutlook(balance, None, ExpiryBasis.UNKNOWN, None, assessed_on=today)
        expires_on = balance.as_of + timedelta(days=policy.comp_off_validity_days)
        return LeaveOutlook(
            balance=balance,
            expires_on=expires_on,
            basis=ExpiryBasis.EARNED_PLUS_WINDOW,
            days_remaining=(expires_on - today).days,
            assessed_on=today,
        )

    soonest = ledger.next_expiry
    return LeaveOutlook(
        balance=replace(balance, available_balance=ledger.usable),
        expires_on=soonest.expires_on if soonest else None,
        basis=ExpiryBasis.EARNED_PLUS_WINDOW,
        days_remaining=soonest.days_left(today) if soonest else None,
        comp_off=ledger,
        assessed_on=today,
    )
