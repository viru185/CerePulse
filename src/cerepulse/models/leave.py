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
