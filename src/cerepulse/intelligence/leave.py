"""Leave intelligence: balances worth acting on, and expiry warnings.

The portal reports balances but never says "use this or lose it", so a balance sitting
untouched is a real and silent loss. Three rules apply here, and they are not the same rule:

* **PL** lapses at the end of the *financial* year, 31 March. The portal's own leave register
  corroborates this without stating it — it is scoped to the financial year, and credits from
  the previous February are simply absent from it.
* **CF** lapses at the end of the calendar year. This one is a one-off HR action rather than
  standing policy, which is exactly why it is configuration and not a constant.
* **CO+** expires 90 days after it is earned, *per credit*. Not per balance: two comp-offs
  earned four months apart do not lapse together, and treating the aggregate as one lot dates
  the whole balance by whichever credit happened to carry a date.

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

from cerepulse.intelligence.insights import Insight, InsightKind, Severity
from cerepulse.models.leave import LeaveBalance, LeaveCategory, LeaveTransaction

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
class LeaveLot:
    """One dated credit, and when that particular credit lapses.

    Comp-off is earned a half-day and a day at a time across a year, and each one carries its
    own clock. A single expiry date on the aggregate balance answers the wrong question: it
    says when *some* of it goes, without saying how much or which.
    """

    earned_on: date
    days: float
    expires_on: date
    note: str = ""
    #: The days this credit was spent on, from the muster, oldest first. Matched to the
    #: credit oldest-against-oldest — the only defensible reading, since nothing in the
    #: portal says which credit a given day off drew from.
    used_on: tuple[tuple[date, float], ...] = ()
    #: Spent according to the portal's balance, on a day the cached muster does not show —
    #: a comp-off taken before the history window, most likely. The portal's balance is the
    #: authority on how much is left; this is the part of the difference we cannot date.
    unattributed: float = 0.0

    @property
    def used_days(self) -> float:
        return sum(amount for _when, amount in self.used_on) + self.unattributed

    @property
    def remaining(self) -> float:
        return max(0.0, self.days - self.used_days)

    @property
    def is_spent(self) -> bool:
        return self.remaining <= 0

    def days_remaining(self, today: date) -> int:
        return (self.expires_on - today).days

    def has_lapsed(self, today: date) -> bool:
        return self.expires_on < today


@dataclass(frozen=True, slots=True)
class LeaveOutlook:
    """A balance with its expiry assessment."""

    balance: LeaveBalance
    expires_on: date | None
    basis: ExpiryBasis
    days_remaining: int | None
    #: Every dated credit, oldest first, spent ones included — the user wants to see each
    #: comp-off with when it expires and when it was used. Empty for leave types that expire
    #: as a block rather than a credit at a time. :attr:`open_lots` is the unspent subset.
    lots: tuple[LeaveLot, ...] = ()
    #: Days already past their window while still counted in the balance. Reported rather
    #: than deducted: the portal's balance is the authority on what exists, and this rule is
    #: company policy we were told rather than anything the portal confirms.
    expired_days: float = 0.0
    #: The day this was assessed against, carried so the lots can be re-read against the same
    #: date the headline figures used. Injected time, never `date.today()` — the rule the
    #: whole intelligence layer runs on.
    assessed_on: date | None = None

    @property
    def has_balance(self) -> bool:
        return self.balance.available_balance > 0

    @property
    def open_lots(self) -> tuple[LeaveLot, ...]:
        """The credits with anything left on them, soonest to lapse first."""
        return tuple(
            sorted((lot for lot in self.lots if not lot.is_spent), key=lambda lot: lot.expires_on)
        )

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
        if not self.lots or self.assessed_on is None:
            return self.balance.available_balance if self.is_at_risk else 0.0
        return sum(
            lot.remaining
            for lot in self.open_lots
            if 0 <= lot.days_remaining(self.assessed_on) <= WARNING_WINDOW_DAYS
        )


def analyze_leave(
    balances: list[LeaveBalance],
    *,
    today: date,
    policy: LeavePolicy | None = None,
    credits: list[LeaveTransaction] | None = None,
    taken: list[tuple[date, float]] | None = None,
) -> list[LeaveOutlook]:
    """Assess each balance for expiry risk.

    ``credits`` are the leave register's dated rows. Without them comp-off falls back to the
    one date on the balance — which the portal leaves empty in practice, so the expiry read
    "unknown" and the warning could never fire for the one leave type that actually expires
    on a rolling window.

    ``taken`` is the muster's record of comp-off days spent, ``(day, amount)``. It is what
    lets each credit say when it was used, which the ledger never records.
    """
    policy = policy or LeavePolicy()
    return [
        _outlook(balance, today=today, policy=policy, credits=credits or [], taken=taken or [])
        for balance in balances
    ]


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
                f"{_days(outlook.expired_days or outlook.balance.available_balance)} of "
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
            title=(
                f"{_days(outlook.at_risk_days)} of {_label(outlook)} "
                f"expire{'s' if outlook.at_risk_days == 1 else ''} on "
                f"{outlook.expires_on:%d %b}"
                if outlook.expires_on
                else f"{_days(outlook.at_risk_days)} of {_label(outlook)} expiring"
            ),
            detail=f"{outlook.days_remaining} days left.",
        )
        for outlook in at_risk
    ]
    return insights


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
    credits: list[LeaveTransaction],
    taken: list[tuple[date, float]],
) -> LeaveOutlook:
    if balance.category is LeaveCategory.COMP_OFF:
        return _comp_off_outlook(balance, today=today, policy=policy, credits=credits, taken=taken)

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
    credits: list[LeaveTransaction],
    taken: list[tuple[date, float]],
) -> LeaveOutlook:
    """Comp-off, one credit at a time.

    Consumption is spent oldest first. That is not a guess about this portal so much as the
    only defensible reading: the ledger records credits and leaves ``consumed_days`` at zero
    on every row, so which particular comp-off was taken is simply not recorded anywhere, and
    anything other than oldest-first would be inventing a worse answer. The balance itself
    always comes from the portal — only its attribution to dates is ours.
    """
    lots = _comp_off_lots(balance, credits=credits, taken=taken, policy=policy)
    if not lots:
        # No dated credits — the ledger was not loaded, or holds none for this type. Fall back
        # to whatever date the balance itself carries. The portal leaves that empty in
        # practice, which is why this used to be the only outcome comp-off ever reached and
        # the warning for the one type that really does expire could never fire.
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

    open_lots = [lot for lot in lots if not lot.is_spent]
    soonest = min(open_lots, key=lambda lot: lot.expires_on) if open_lots else None
    return LeaveOutlook(
        balance=balance,
        expires_on=soonest.expires_on if soonest else None,
        basis=ExpiryBasis.EARNED_PLUS_WINDOW,
        days_remaining=soonest.days_remaining(today) if soonest else None,
        lots=tuple(lots),
        expired_days=sum(lot.remaining for lot in open_lots if lot.has_lapsed(today)),
        assessed_on=today,
    )


def _comp_off_lots(
    balance: LeaveBalance,
    *,
    credits: list[LeaveTransaction],
    taken: list[tuple[date, float]],
    policy: LeavePolicy,
) -> list[LeaveLot]:
    """Every dated credit, oldest first, with the days it was spent on.

    Two sources of spending, applied in order. The muster's comp-off days are dated, and go
    against the oldest credit that was earned on or before them — a day off cannot have drawn
    on a credit that did not exist yet, and a day off older than every credit here drew on
    one the ledger no longer shows (it is scoped to the financial year), so it is dropped
    rather than charged to the wrong credit. Then the portal's own balance has the final
    word: if less is left than the muster accounts for, the difference is taken off the
    oldest credits as *unattributed* — spent, on a day the cached history does not reach.
    """
    dated = sorted(
        (
            txn
            for txn in credits
            if txn.transaction_date is not None
            and txn.credit_days > 0
            and LeaveCategory.classify(txn.leave_type) is LeaveCategory.COMP_OFF
        ),
        key=lambda txn: txn.transaction_date or date.min,
    )
    queue = sorted(taken)

    lots: list[LeaveLot] = []
    for txn in dated:
        assert txn.transaction_date is not None  # filtered above
        earned_on = txn.transaction_date
        left = txn.credit_days
        used: list[tuple[date, float]] = []
        # Days off that predate this credit cannot have come from it — or from any later one.
        while queue and queue[0][0] < earned_on:
            queue.pop(0)
        while left > 0 and queue:
            when, amount = queue[0]
            take = min(left, amount)
            used.append((when, take))
            left -= take
            if amount - take > 0:
                queue[0] = (when, amount - take)
            else:
                queue.pop(0)
        lots.append(
            LeaveLot(
                earned_on=earned_on,
                days=txn.credit_days,
                expires_on=earned_on + timedelta(days=policy.comp_off_validity_days),
                note=txn.remark,
                used_on=tuple(used),
            )
        )

    # The portal's balance is the authority on how much is left.
    excess = sum(lot.remaining for lot in lots) - balance.available_balance
    for index, lot in enumerate(lots):
        if excess <= 0:
            break
        if lot.is_spent:
            continue
        take = min(excess, lot.remaining)
        lots[index] = replace(lot, unattributed=lot.unattributed + take)
        excess -= take
    return lots
