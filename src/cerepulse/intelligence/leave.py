"""Leave intelligence: balances worth acting on, and expiry warnings.

The portal reports balances but never says "use this or lose it". Carry-forward leave
typically lapses at the end of the leave year, and comp-off usually expires a fixed window
after it is earned — so a balance sitting untouched is a real, silent loss.

Those windows are company policy, not protocol facts, so they are configurable and default
to conservative values. When a balance carries no usable date, the expiry is reported as
unknown rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from cerepulse.intelligence.insights import Insight, InsightKind, Severity
from cerepulse.models.leave import LeaveBalance, LeaveCategory

#: Days before expiry at which a balance starts being flagged.
WARNING_WINDOW_DAYS = 60
URGENT_WINDOW_DAYS = 21


class ExpiryBasis(Enum):
    """How an expiry date was arrived at, so the UI never implies false precision."""

    LEAVE_YEAR_END = "leave_year_end"
    EARNED_PLUS_WINDOW = "earned_plus_window"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class LeavePolicy:
    """Company rules governing when leave lapses."""

    #: Month and day the leave year ends; carry-forward lapses here.
    leave_year_end: tuple[int, int] = (12, 31)
    #: Days a comp-off stays usable after it is earned.
    comp_off_validity_days: int = 90


@dataclass(frozen=True, slots=True)
class LeaveOutlook:
    """A balance with its expiry assessment."""

    balance: LeaveBalance
    expires_on: date | None
    basis: ExpiryBasis
    days_remaining: int | None

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


def analyze_leave(
    balances: list[LeaveBalance],
    *,
    today: date,
    policy: LeavePolicy | None = None,
) -> list[LeaveOutlook]:
    """Assess each balance for expiry risk."""
    policy = policy or LeavePolicy()
    return [_outlook(balance, today=today, policy=policy) for balance in balances]


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
            title=f"{_days(outlook)} of {outlook.balance.leave_type} have expired",
            detail=f"The deadline was {outlook.expires_on:%d %b %Y}.",
        )
        for outlook in expired
    ]

    insights += [
        Insight(
            kind=InsightKind.LEAVE_EXPIRING,
            severity=(
                Severity.WARNING
                if (outlook.days_remaining or 0) <= URGENT_WINDOW_DAYS
                else Severity.INFO
            ),
            title=(
                f"{_days(outlook)} of {outlook.balance.leave_type} "
                f"expire in {outlook.days_remaining} days"
            ),
            detail=(
                f"Expires on {outlook.expires_on:%d %b %Y}. Use it or lose it."
                if outlook.expires_on
                else "Expiry date unknown."
            ),
        )
        for outlook in at_risk
    ]
    return insights


def _days(outlook: LeaveOutlook) -> str:
    amount = outlook.balance.available_balance
    return f"{amount:g} day{'s' if amount != 1 else ''}"


def _outlook(balance: LeaveBalance, *, today: date, policy: LeavePolicy) -> LeaveOutlook:
    expires_on, basis = _expiry(balance, today=today, policy=policy)
    remaining = (expires_on - today).days if expires_on else None
    return LeaveOutlook(
        balance=balance,
        expires_on=expires_on,
        basis=basis,
        days_remaining=remaining,
    )


def _expiry(
    balance: LeaveBalance, *, today: date, policy: LeavePolicy
) -> tuple[date | None, ExpiryBasis]:
    month, day = policy.leave_year_end

    if balance.category is LeaveCategory.CARRY_FORWARD:
        year_end = date(today.year, month, day)
        if year_end < today:
            year_end = date(today.year + 1, month, day)
        return year_end, ExpiryBasis.LEAVE_YEAR_END

    if balance.category is LeaveCategory.COMP_OFF:
        if balance.as_of is None:
            # Without an earned date there is nothing to count from; say so rather than
            # inventing a deadline the user might act on.
            return None, ExpiryBasis.UNKNOWN
        return (
            balance.as_of + timedelta(days=policy.comp_off_validity_days),
            ExpiryBasis.EARNED_PLUS_WINDOW,
        )

    return None, ExpiryBasis.UNKNOWN
