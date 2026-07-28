"""Leave — balances with expiry countdowns, and the ledger behind them."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cerepulse.intelligence.leave import LeaveOutlook
from cerepulse.models.leave import LeaveCategory, LeaveTransaction
from cerepulse.services.leave import LeaveView as LeaveData
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import Palette
from cerepulse.ui.widgets import Banner, Card, InsightStrip, SectionTitle

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


class LeaveViewWidget(QWidget):
    """Balance cards, expiry insights, and the transaction ledger."""

    refresh_requested = Signal()

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.addWidget(SectionTitle("Leave balances"))
        header.addStretch(1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_requested)
        header.addWidget(refresh)
        layout.addLayout(header)

        self.banner = Banner()
        layout.addWidget(self.banner)

        self._cards = QHBoxLayout()
        self._cards.setSpacing(12)
        cards_host = QWidget()
        cards_host.setLayout(self._cards)
        layout.addWidget(cards_host)

        self._insights = InsightStrip(palette)
        layout.addWidget(self._insights)

        layout.addWidget(SectionTitle("Ledger"))
        self.table = self._build_table()
        layout.addWidget(self.table, 1)

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(0, len(LEDGER_COLUMNS))
        table.setHorizontalHeaderLabels(LEDGER_COLUMNS)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        return table

    # --- rendering ------------------------------------------------------------------

    def show_leave(self, data: LeaveData) -> None:
        self._render_cards(data.outlooks)
        self._insights.set_insights(data.insights)

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
