"""What the portal says about pay, reduced to figures with labels.

Nothing personal beyond the figures: the pages also carry a PAN, a date of birth and a
photo path, and none of it is read. The parsers take the money and leave the rest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class PayLine:
    label: str
    amount: float


@dataclass(frozen=True, slots=True)
class Payslip:
    """One month's slip, as the portal renders it: three columns of lines and the totals."""

    period: date  # the first of the month
    earnings: tuple[PayLine, ...]
    deductions: tuple[PayLine, ...]
    variables: tuple[PayLine, ...]
    gross: float
    total_deductions: float
    net_pay: float
    in_words: str = ""

    @property
    def label(self) -> str:
        return self.period.strftime("%B %Y")


@dataclass(frozen=True, slots=True)
class CtcLine:
    label: str
    monthly: float | None
    yearly: float | None
    #: "Earnings" or "Outside Payroll Benefits", as the page groups them.
    section: str = ""
    #: A subtotal or the total, drawn as one on screen.
    is_total: bool = False


@dataclass(frozen=True, slots=True)
class CtcStatement:
    for_year: str
    lines: tuple[CtcLine, ...]

    def total(self, label_fragment: str) -> CtcLine | None:
        for line in self.lines:
            if line.is_total and label_fragment.lower() in line.label.lower():
                return line
        return None

    @property
    def cost_to_company(self) -> CtcLine | None:
        return self.total("cost to company")

    @property
    def gross(self) -> CtcLine | None:
        return self.total("total gross")


@dataclass(frozen=True, slots=True)
class MonthlyRow:
    label: str
    #: One per month column, None where the cell was empty.
    values: tuple[float | None, ...]


@dataclass(frozen=True, slots=True)
class MonthlyStatement:
    """The year at a glance: one row per component, one column per month."""

    period_label: str
    months: tuple[str, ...]
    rows: tuple[MonthlyRow, ...]

    def row(self, label_fragment: str) -> MonthlyRow | None:
        for row in self.rows:
            if label_fragment.lower() in row.label.lower():
                return row
        return None
