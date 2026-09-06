"""The Pay screen: the latest slip, the CTC statement and the year at a glance.

Masked by default. This is the app that sits open on an office desk, so every figure reads
as dots until the one control at the top is switched on, and the switch forgets itself
when the screen is left. The slip can be saved as a PDF — rendered here from the figures,
because the portal's own export answers 500 to anything that is not a browser.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPageSize, QPdfWriter, QTextDocument
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cerepulse.models.pay import CtcStatement, MonthlyStatement, Payslip
from cerepulse.services.pay import PaySnapshot
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import Palette
from cerepulse.ui.widgets import (
    Banner,
    Card,
    EmptyState,
    SectionTitle,
    card_row,
    data_table,
)

MASK = "••••••"


def format_inr(amount: float | None, *, masked: bool = False) -> str:
    """``123456.5`` → ``₹1,23,456.50`` — Indian grouping, two decimals, dots when masked."""
    if masked:
        return MASK
    if amount is None:
        return "—"
    sign = "-" if amount < 0 else ""
    whole, fraction = f"{abs(amount):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{sign}₹{whole}.{fraction}"


class PayView(QWidget):
    refresh_requested = Signal()
    settings_requested = Signal()

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._snapshot: PaySnapshot | None = None
        self._enabled = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)

        layout.addLayout(self._build_header())

        self.banner = Banner()
        layout.addWidget(self.banner)

        self._off = EmptyState(
            "The Pay screen is off.",
            "Turn it on under Settings › Pay. Figures are fetched only when you ask, kept "
            "encrypted to your Windows account, and masked on screen until you reveal them.",
        )
        layout.addWidget(self._off)
        self._settings_link = QPushButton("Open Settings")
        self._settings_link.clicked.connect(self.settings_requested.emit)
        layout.addWidget(self._settings_link, 0, Qt.AlignmentFlag.AlignLeft)

        self._empty = EmptyState(
            "Nothing fetched yet.",
            "Refresh reads the CTC statement, the monthly report and every payslip the "
            "portal offers. It is not part of the automatic sync.",
        )
        layout.addWidget(self._empty)

        self._content = QWidget()
        body = QVBoxLayout(self._content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        layout.addWidget(self._content)

        self.net_pay = Card("Net pay")
        self.gross = Card("Gross")
        self.ctc_yearly = Card("Cost to company")
        self.ctc_monthly = Card("CTC per month")
        body.addWidget(card_row(self.net_pay, self.gross, self.ctc_yearly, self.ctc_monthly))

        body.addWidget(SectionTitle("Payslip"))
        picker = QHBoxLayout()
        self._period = QComboBox()
        self._period.setMinimumWidth(180)
        self._period.currentIndexChanged.connect(lambda _index: self._render_slip())
        picker.addWidget(self._period)
        self._save_pdf = QPushButton("Save as PDF…")
        self._save_pdf.clicked.connect(self._on_save_pdf)
        picker.addWidget(self._save_pdf)
        picker.addStretch(1)
        body.addLayout(picker)

        columns = QHBoxLayout()
        columns.setSpacing(12)
        self._earnings = data_table(("Earnings", "Amount"), fit_rows=True)
        self._deductions = data_table(("Deductions", "Amount"), fit_rows=True)
        self._variables = data_table(("Variables", "Amount"), fit_rows=True)
        for table in (self._earnings, self._deductions, self._variables):
            columns.addWidget(table, 1)
        body.addLayout(columns)
        self._slip_totals = QLabel()
        self._slip_totals.setObjectName("CardCaption")
        self._slip_totals.setWordWrap(True)
        body.addWidget(self._slip_totals)

        body.addWidget(SectionTitle("CTC statement"))
        self._ctc_note = QLabel()
        self._ctc_note.setObjectName("CardCaption")
        body.addWidget(self._ctc_note)
        self._ctc = data_table(("Item", "Monthly", "Yearly"), fit_rows=True)
        body.addWidget(self._ctc)

        body.addWidget(SectionTitle("Monthly report"))
        self._monthly_note = QLabel()
        self._monthly_note.setObjectName("CardCaption")
        body.addWidget(self._monthly_note)
        self._monthly = data_table(("Particulars",), stretch_last=False, fit_rows=True)
        self._monthly.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body.addWidget(self._monthly)

        self._synced = QLabel()
        self._synced.setObjectName("CardCaption")
        body.addWidget(self._synced)
        layout.addStretch(1)

        self.set_enabled_state(False)

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        title = QLabel("Pay")
        title.setObjectName("HeroLabel")
        header.addWidget(title)
        header.addStretch(1)
        self._reveal = QPushButton("Show figures")
        self._reveal.setCheckable(True)
        self._reveal.setToolTip(
            "Figures stay masked until this is on, and mask again when you leave the screen."
        )
        self._reveal.toggled.connect(self._on_reveal)
        header.addWidget(self._reveal)
        self._refresh = QPushButton("Refresh")
        self._refresh.setToolTip("Fetch the CTC, the monthly report and any new payslips.")
        self._refresh.clicked.connect(self.refresh_requested.emit)
        header.addWidget(self._refresh)
        return header

    # --- state --------------------------------------------------------------------------

    @property
    def revealed(self) -> bool:
        return self._reveal.isChecked()

    def set_enabled_state(self, enabled: bool) -> None:
        self._enabled = enabled
        self._off.setVisible(not enabled)
        self._settings_link.setVisible(not enabled)
        self._reveal.setVisible(enabled)
        self._refresh.setVisible(enabled)
        if not enabled:
            self._snapshot = None
            self._empty.setVisible(False)
            self._content.setVisible(False)
            return
        self._render()

    def show(self, snapshot: PaySnapshot) -> None:  # type: ignore[override]
        self._snapshot = snapshot
        self._render()

    def hideEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        # Leaving the screen re-masks it: the figures were shown for a look, not for the day.
        self._reveal.setChecked(False)
        super().hideEvent(event)  # type: ignore[arg-type]

    def _on_reveal(self, _checked: bool) -> None:
        self._reveal.setText("Hide figures" if self.revealed else "Show figures")
        self._render()

    # --- rendering ----------------------------------------------------------------------

    def _money(self, amount: float | None) -> str:
        return format_inr(amount, masked=not self.revealed)

    def _render(self) -> None:
        if not self._enabled:
            return
        snapshot = self._snapshot
        empty = snapshot is None or snapshot.is_empty
        self._empty.setVisible(empty)
        self._content.setVisible(not empty)
        if snapshot is None or empty:
            return

        latest = snapshot.latest
        self.net_pay.set_value(self._money(latest.net_pay) if latest else "—")
        self.net_pay.set_caption(latest.label if latest else "No payslip yet")
        self.gross.set_value(self._money(latest.gross) if latest else "—")
        self.gross.set_caption(f"{self._money(latest.total_deductions)} deducted" if latest else "")
        ctc = snapshot.ctc.cost_to_company if snapshot.ctc else None
        self.ctc_yearly.set_value(self._money(ctc.yearly) if ctc else "—")
        self.ctc_yearly.set_caption(
            f"For {snapshot.ctc.for_year}" if snapshot.ctc and snapshot.ctc.for_year else ""
        )
        self.ctc_monthly.set_value(self._money(ctc.monthly) if ctc else "—")
        self.ctc_monthly.set_caption("Before the yearly items" if ctc else "")

        self._fill_periods(snapshot.payslips)
        self._render_slip()
        self._render_ctc(snapshot.ctc)
        self._render_monthly(snapshot.monthly)
        self._synced.setText(
            f"Last fetched {fmt.relative_time(snapshot.last_synced, now=datetime.now())}."
            if snapshot.last_synced
            else ""
        )

    def _fill_periods(self, slips: tuple[Payslip, ...]) -> None:
        chosen = self._period.currentData()
        self._period.blockSignals(True)
        self._period.clear()
        for slip in slips:
            self._period.addItem(slip.label, slip.period.isoformat())
        index = next(
            (i for i in range(self._period.count()) if self._period.itemData(i) == chosen), 0
        )
        self._period.setCurrentIndex(index)
        self._period.blockSignals(False)
        self._period.setVisible(bool(slips))
        self._save_pdf.setVisible(bool(slips))

    def _current_slip(self) -> Payslip | None:
        if self._snapshot is None:
            return None
        chosen = self._period.currentData()
        for slip in self._snapshot.payslips:
            if slip.period.isoformat() == chosen:
                return slip
        return self._snapshot.latest

    def _render_slip(self) -> None:
        slip = self._current_slip()
        for table, lines in (
            (self._earnings, slip.earnings if slip else ()),
            (self._deductions, slip.deductions if slip else ()),
            (self._variables, slip.variables if slip else ()),
        ):
            _fill(table, [(line.label, self._money(line.amount)) for line in lines])
        if slip is None:
            self._slip_totals.setText("No payslip has been fetched.")
            return
        words = f" — {slip.in_words}" if slip.in_words and self.revealed else ""
        self._slip_totals.setText(
            f"Gross {self._money(slip.gross)} · deductions {self._money(slip.total_deductions)}"
            f" · net pay {self._money(slip.net_pay)}{words}"
        )

    def _render_ctc(self, ctc: CtcStatement | None) -> None:
        if ctc is None:
            self._ctc_note.setText("The CTC statement has not been fetched.")
            _fill(self._ctc, [])
            return
        self._ctc_note.setText(f"For {ctc.for_year}." if ctc.for_year else "")
        rows: list[tuple[str, ...]] = [
            (line.label, self._money(line.monthly), self._money(line.yearly)) for line in ctc.lines
        ]
        _fill(self._ctc, rows, bold=[line.is_total for line in ctc.lines])

    def _render_monthly(self, monthly: MonthlyStatement | None) -> None:
        if monthly is None:
            self._monthly_note.setText("The monthly report has not been fetched.")
            self._monthly.setRowCount(0)
            return
        self._monthly_note.setText(monthly.period_label)
        self._monthly.setColumnCount(1 + len(monthly.months))
        self._monthly.setHorizontalHeaderLabels(["Particulars", *monthly.months])
        rows = [(row.label, *[self._money(value) for value in row.values]) for row in monthly.rows]
        _fill(
            self._monthly,
            rows,
            bold=["total" in r.label.lower() or "net" in r.label.lower() for r in monthly.rows],
        )

    # --- PDF ----------------------------------------------------------------------------

    def _on_save_pdf(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        slip = self._current_slip()
        if slip is None:
            return
        suggested = f"Payslip {slip.period.strftime('%Y-%m')}.pdf"
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save payslip as PDF", suggested, "PDF (*.pdf)"
        )
        if not path:
            return
        try:
            write_payslip_pdf(slip, path)
        except OSError as exc:
            self.banner.show_message(f"Could not save the PDF: {exc}", key="pdf", offer_logs=True)
            return
        self.banner.show_message(f"Saved {path}.", key="pdf")


def _fill(
    table: QTableWidget, rows: Sequence[tuple[str, ...]], *, bold: list[bool] | None = None
) -> None:
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            item = QTableWidgetItem(text)
            if c > 0:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if bold and bold[r]:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            table.setItem(r, c, item)
    table.resizeColumnsToContents()
    table.resizeRowsToContents()


def payslip_html(slip: Payslip) -> str:
    """The slip as a printable page. Figures only — no name, code, PAN or bank detail."""

    def rows(lines: tuple[object, ...]) -> str:
        return "".join(
            f"<tr><td>{line.label}</td><td align=right>{format_inr(line.amount)}</td></tr>"  # type: ignore[attr-defined]
            for line in lines
        )

    def block(title: str, lines: tuple[object, ...], total: str, total_value: float) -> str:
        return (
            f"<h3>{title}</h3><table width=100% cellpadding=4>{rows(lines)}"
            f"<tr><td><b>{total}</b></td><td align=right><b>{format_inr(total_value)}</b></td></tr>"
            "</table>"
        )

    words = f"<p><i>{slip.in_words}</i></p>" if slip.in_words else ""
    return (
        f"<h2>Payslip — {slip.label}</h2>"
        + block("Earnings", slip.earnings, "Gross", slip.gross)
        + block("Deductions", slip.deductions, "Total deductions", slip.total_deductions)
        + block("Variables", slip.variables, "Net pay", slip.net_pay)
        + words
        + "<p><small>Figures as shown on the SpineHR portal, rendered by CerePulse.</small></p>"
    )


def write_payslip_pdf(slip: Payslip, path: str) -> None:
    # QPdfWriter rather than QPrinter: the latter probes the print spooler over COM, which
    # is slow, needs no part of a printer, and throws non-fatal exceptions in the offscreen
    # test platform.
    writer = QPdfWriter(path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setTitle(f"Payslip {slip.label}")
    document = QTextDocument()
    document.setHtml(payslip_html(slip))
    document.print_(writer)


__all__ = ["MASK", "PayView", "format_inr", "payslip_html", "write_payslip_pdf"]
