"""Leave — balances with expiry countdowns, the breaks they can buy, and the ledger.

The break planner exists because everyone does this arithmetic by hand once a year, badly,
staring at a wall calendar. A holiday on a Thursday makes the Friday worth four days off;
the same holiday on a Wednesday is worth almost nothing, and telling those apart at a glance
is exactly what software is for.

CerePulse still books nothing. It says which days are worth asking for.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cerepulse.intelligence.leave import LeaveOutlook
from cerepulse.intelligence.optimizer import BreakPlan
from cerepulse.models.leave import LeaveCategory, LeaveTransaction
from cerepulse.services.leave import LeaveView as LeaveData
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import Palette
from cerepulse.ui.widgets import Banner, Card, InsightStrip, SectionTitle, data_table

LEDGER_COLUMNS = ("Type", "Date", "Credit", "Consumed", "Balance", "Remark")

#: Order the balance cards so the ones with a deadline lead.
CARD_ORDER = (
    LeaveCategory.PLANNED,
    LeaveCategory.COMP_OFF,
    LeaveCategory.CARRY_FORWARD,
    LeaveCategory.MEDICAL,
    LeaveCategory.CASUAL,
    LeaveCategory.OTHER,
)


BREAK_COLUMNS = ("When", "Days off", "Leave used", "Value", "Book these days")


class LeaveViewWidget(QWidget):
    """Balance cards, expiry insights, break suggestions, and the transaction ledger."""

    refresh_requested = Signal()
    export_requested = Signal()

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.addWidget(SectionTitle("Leave balances"))
        header.addStretch(1)
        export = QPushButton("Export to calendar…")
        export.clicked.connect(self.export_requested)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_requested)
        header.addWidget(export)
        header.addWidget(refresh)
        layout.addLayout(header)

        self.banner = Banner()
        layout.addWidget(self.banner)

        # The total leads, because "how much leave do I have" is the question the screen is
        # opened with, and adding six cards up in your head is not an answer to it.
        self.total = Card("Total available")
        self.total.setMinimumWidth(190)
        layout.addWidget(self.total)

        self._cards = QHBoxLayout()
        self._cards.setSpacing(12)
        cards_host = QWidget()
        cards_host.setLayout(self._cards)
        layout.addWidget(cards_host)

        self._insights = InsightStrip(palette)
        layout.addWidget(self._insights)

        layout.addWidget(SectionTitle("Best breaks you can book"))
        self.breaks = self._build_breaks()
        layout.addWidget(self.breaks)
        self._breaks_note = QLabel()
        self._breaks_note.setObjectName("CardCaption")
        self._breaks_note.setWordWrap(True)
        layout.addWidget(self._breaks_note)

        layout.addWidget(SectionTitle("Ledger"))
        self.table = self._build_table()
        layout.addWidget(self.table, 1)

    def _build_breaks(self) -> QTableWidget:
        # Short by design — six suggestions at most — so it shows every row rather than
        # scrolling inside a screen that already scrolls.
        return data_table(BREAK_COLUMNS, fit_rows=True)

    def _build_table(self) -> QTableWidget:
        return data_table(LEDGER_COLUMNS)

    # --- rendering ------------------------------------------------------------------

    def show_leave(self, data: LeaveData) -> None:
        self._render_total(data.outlooks)
        self._render_cards(data.outlooks)
        self._insights.set_insights(data.insights)
        self._render_breaks(data.breaks)

    def _render_breaks(self, plans: list[BreakPlan]) -> None:
        self.breaks.setRowCount(len(plans))
        self.breaks.setVisible(bool(plans))

        if not plans:
            # Distinguish the two reasons the table can be empty; "no suggestions" with no
            # explanation reads as the feature being broken.
            self._breaks_note.setText(
                "No bookable breaks in the next six months — either the balance is spent "
                "or the holiday calendar has not synced yet."
            )
            return

        for row, plan in enumerate(plans):
            values = (
                plan.label,
                str(plan.total_days),
                str(plan.cost),
                f"{plan.efficiency:.1f}×",
                _booking_text(plan),
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if 1 <= column <= 3:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                if column == 3:
                    item.setForeground(QColor(self._palette.good))
                item.setToolTip(_plan_tooltip(plan))
                self.breaks.setItem(row, column, item)

        best = max(plans, key=lambda plan: plan.efficiency)
        self._breaks_note.setText(
            f"Value is days off per day of leave spent. The best here turns "
            f"{_days(best.cost)} into {best.total_days}. Nothing is booked — "
            f"apply in SpineHR."
        )

    def _render_total(self, outlooks: list[LeaveOutlook]) -> None:
        """Everything available, and how much of it is on a deadline.

        A single number would be the wrong answer on its own: some of a balance can be
        comp-off that lapses in a fortnight, and a total that hides that reads as more
        freedom than the user actually has. Expired days are excluded outright — they are
        not available, whatever the portal's column still says.
        """
        live = [outlook for outlook in outlooks if not outlook.is_expired]
        total = sum(outlook.balance.available_balance for outlook in live)
        at_risk = sum(outlook.balance.available_balance for outlook in live if outlook.is_at_risk)

        self.total.set_value(
            _days(total), accent=self._palette.good if total > 0 else self._palette.text_muted
        )
        parts = [f"across {len(live)} leave type(s)"]
        if at_risk:
            parts.append(f"{_days(at_risk)} expiring soon")
        expired = [outlook for outlook in outlooks if outlook.is_expired]
        if expired:
            parts.append(f"{len(expired)} type(s) already lapsed and not counted")
        self.total.set_caption("  ·  ".join(parts))

    def _render_cards(self, outlooks: list[LeaveOutlook]) -> None:
        while self._cards.count():
            item = self._cards.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

        ordered = sorted(outlooks, key=lambda outlook: CARD_ORDER.index(outlook.balance.category))
        for outlook in ordered:
            self._cards.addWidget(self._card_for(outlook))
        self._cards.addStretch(1)

    def _card_for(self, outlook: LeaveOutlook) -> Card:
        balance = outlook.balance
        accent = None
        if outlook.is_expired:
            accent = self._palette.bad
        elif outlook.is_at_risk:
            accent = self._palette.rest
        elif balance.available_balance > 0:
            accent = self._palette.good

        card = Card(balance.leave_type, value=f"{balance.available_balance:g}", accent=accent)
        card.set_caption(_expiry_caption(outlook))
        # A fixed floor keeps the row even when the captions differ in length.
        card.setMinimumWidth(190)
        return card

    def show_ledger(self, transactions: list[LeaveTransaction]) -> None:
        self.table.setRowCount(len(transactions))
        for row, txn in enumerate(transactions):
            values = (
                txn.leave_type,
                fmt.day_label(txn.transaction_date),
                f"{txn.credit_days:g}" if txn.credit_days else "",
                f"{txn.consumed_days:g}" if txn.consumed_days else "",
                f"{txn.available_balance:g}",
                txn.remark,
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(str(text))
                if 2 <= column <= 4:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(row, column, item)


#: Beyond this, the dates stop being a list to act on and become a wall of text that
#: doubles the width of the table. The full list stays available on hover.
INLINE_DATES = 4


def _days(count: float) -> str:
    """``1 day``, ``4 days``, ``4.5 days`` — balances come in halves, break plans do not."""
    shown = f"{count:g}"
    return f"{shown} day" if count == 1 else f"{shown} days"


def _booking_text(plan: BreakPlan) -> str:
    labels = [day.strftime("%a %d %b").replace(" 0", " ") for day in plan.leave_days]
    if len(labels) <= INLINE_DATES:
        return ", ".join(labels)
    return f"{labels[0]} – {labels[-1]}  ({_days(len(labels))})"


def _plan_tooltip(plan: BreakPlan) -> str:
    lines = [
        "Book: " + ", ".join(day.strftime("%a %d %b").replace(" 0", " ") for day in plan.leave_days)
    ]
    if plan.holidays:
        lines.append(
            "Covers " + ", ".join(day.strftime("%d %b").lstrip("0") for day in plan.holidays)
        )
    return "\n".join(lines)


def _expiry_caption(outlook: LeaveOutlook) -> str:
    """What the card says under the number.

    Comp-off often has no earned date in the portal's summary row, so its expiry is
    genuinely unknown. Saying so is better than implying it never expires.
    """
    if outlook.expires_on is None:
        return "days available"
    if outlook.is_expired:
        return f"expired {outlook.expires_on:%d %b %Y}"
    days = outlook.days_remaining or 0
    if days <= 60:
        return f"expires in {days} days"
    return f"expires {outlook.expires_on:%d %b %Y}"
