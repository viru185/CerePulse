"""Leave and holiday domain models.

Sourced from the leave register (``GridView2`` on LeaveBalanceDetail, populated by the
View postback) and the holiday list. The register is a running ledger: each row is a
transaction with an opening balance, consumed/credit days, and the available balance after
it. The latest row per leave type is therefore the current balance, which
:class:`LeaveBalance` captures as a summary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum


class LeaveCategory(Enum):
    """Normalized grouping of the portal's leave-type codes.

    The spec asks for planned, comp-off and carry-forward balances by name, but the portal
    labels them tersely and inconsistently — comp off arrives as ``CO- / CO+``, so matching
    has to be on the leading code rather than the whole string.
    """

    PLANNED = "planned"
    COMP_OFF = "comp_off"
    CARRY_FORWARD = "carry_forward"
    MEDICAL = "medical"
    CASUAL = "casual"
    OTHER = "other"

    @classmethod
    def classify(cls, leave_type: str) -> LeaveCategory:
        # Take the leading alphabetic code: "CO- / CO+" -> "CO", "PL" -> "PL".
        match = re.match(r"\s*([A-Za-z]+)", leave_type)
        code = (match.group(1) if match else "").upper()
        return {
            "PL": cls.PLANNED,
            "EL": cls.PLANNED,
            "CO": cls.COMP_OFF,
            "COMP": cls.COMP_OFF,
            "CF": cls.CARRY_FORWARD,
            "ML": cls.MEDICAL,
            "SL": cls.MEDICAL,
            "CL": cls.CASUAL,
        }.get(code, cls.OTHER)


@dataclass(frozen=True, slots=True)
class LeaveTransaction:
    """One row of the leave register ledger."""

    leave_type: str
    opening_balance: float
    consumed_days: float
    credit_days: float
    available_balance: float
    transaction_date: date | None = None
    remark: str = ""
    is_credit: bool = False


@dataclass(frozen=True, slots=True)
class LeaveBalance:
    """Current balance for one leave type, i.e. the latest ledger row for it."""

    leave_type: str
    available_balance: float
    consumed_days: float = 0.0
    credit_days: float = 0.0
    as_of: date | None = None

    @property
    def category(self) -> LeaveCategory:
        return LeaveCategory.classify(self.leave_type)

    @property
    def is_comp_off(self) -> bool:
        return self.category is LeaveCategory.COMP_OFF


@dataclass(frozen=True, slots=True)
class Holiday:
    """A single company holiday."""

    day: date
    weekday: str
    name: str


@dataclass(frozen=True, slots=True)
class CompOffLink:
    """Which comp-off credit a ``CO-`` leave application drew on, as the portal recorded it.

    Read off the application itself: opening it (the list's own Select postback, view-only)
    renders the form with ``hdnCOAdjDate`` — the earned date of the credit chosen when it was
    filed — beside ``hdnCOAdjustmenttype1`` (``Full``/``Half``) and, for a half,
    ``hdnCOAdjustmentHalfType1`` (``First``/``Second``). Nothing else in the portal says which
    credit a day off spent, and without it the answer is inferred.
    """

    app_id: str
    #: ``None`` when the portal recorded no credit at all — a CO- filed against the previous
    #: financial year's balance carries an empty field.
    credit_date: date | None
    days: float = 1.0
    #: ``First``/``Second`` for a half credit, empty for a whole one.
    half: str = ""


@dataclass(frozen=True, slots=True)
class CompOffSnapshot:
    """The portal's own comp-off balance and the credits it would offer, on one day.

    Two read-only page methods on the leave form answer these — the ones its date picker
    calls — so this is the portal's figure rather than ours. Kept as a cross-check on the
    ledger the app computes, never as a replacement for it: it says *how many* and *which*,
    and nothing about what became of the rest.
    """

    as_of: date
    balance: float
    #: Earned date and amount of every credit the portal would offer for a leave on ``as_of``.
    dates: tuple[tuple[date, float], ...] = ()
    #: The comp-off applications' ids and states when this was taken, so a decision made
    #: since can make it stale before the day is out.
    signature: str = ""
