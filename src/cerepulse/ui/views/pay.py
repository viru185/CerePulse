"""The Pay screen: a payslip, the year by month and the CTC statement, behind three tabs.

Masked by default. This is the app that sits open on an office desk, so every figure reads
as dots until the one switch at the top is on, and the switch forgets itself when the screen
is left. The slip can be saved as a PDF — rendered here from the figures, because the
portal's own export answers 500 to anything that is not a browser.

The slip and the CTC are ledgers — label, amount, subtotal — not grids, and a table widget
sized to its contents left two-thirds of every frame empty. :class:`_Ledger` fills its
column. The monthly report is the one genuine grid here and stays a table.

The three documents answer three different questions, so they are three views rather than
one long page: the redesign's mockup put the slip first, alone, with the net pay at the size
of the figure the screen is opened for.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPageSize, QPdfWriter, QTextDocument
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cerepulse.models.pay import CtcStatement, MonthlyStatement, Payslip
from cerepulse.services.pay import PaySnapshot
from cerepulse.ui import formatting as fmt
from cerepulse.ui import kit
from cerepulse.ui.theme import Palette, Space
from cerepulse.ui.widgets import Banner, EmptyState, card_row

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


class LineStyle(Enum):
    NORMAL = "normal"
    #: A subtotal or the total: bold, ruled off above.
    TOTAL = "total"
    #: A group heading with no amounts, like the CTC's "Outside payroll benefits".
    HEADING = "heading"


Line = tuple[str, tuple[str, ...], LineStyle]


#: The three views behind the tabs, in order.
TABS = ("Payslip", "Year by month", "CTC")
SLIP, YEAR, CTC = range(3)


class _Ledger(kit.Surface):
    """Label on the left, amounts on the right, totals ruled off — filling its column.

    Takes strings, never numbers: masking is the view's decision, and a ledger that never
    sees a figure cannot leak one. Built from kit text, so the rows carry no stylesheet of
    their own — the shaded rows that used to alternate here drew as solid black bars in the
    light theme.
    """

    def __init__(
        self,
        title: str,
        headings: Sequence[str],
        palette: Palette,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, variant="card")
        self._palette = palette
        self._columns = 1 + len(headings)
        self._cells: list[list[kit.Text]] = []
        self._drawn: list[QWidget] = []

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(Space.GAP, 14, Space.GAP, 14)
        self._grid.setHorizontalSpacing(Space.GAP)
        self._grid.setVerticalSpacing(6)
        self._grid.setColumnStretch(0, 1)

        self._grid.addWidget(kit.Text(title, self, role="caption", tone="muted"), 0, 0)
        for column, heading in enumerate(headings, start=1):
            label = kit.Text(heading, self, role="caption", tone="muted")
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._grid.addWidget(label, 0, column)

    def set_lines(self, lines: Sequence[Line]) -> None:
        # Reparent before deleting, or the old rows keep painting until the loop drains.
        for widget in self._drawn:
            self._grid.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._drawn = []
        self._cells = []

        row = 1
        for label, amounts, style in lines:
            if style is LineStyle.HEADING:
                heading = kit.Text(label, self, role="caption", tone="muted")
                heading.setContentsMargins(0, Space.SNUG, 0, 0)
                self._grid.addWidget(heading, row, 0, 1, self._columns)
                self._drawn.append(heading)
                self._cells.append([heading])
                row += 1
                continue

            if style is LineStyle.TOTAL:
                rule = kit.Divider(self)
                self._grid.addWidget(rule, row, 0, 1, self._columns)
                self._drawn.append(rule)
                row += 1

            role = "body-strong" if style is LineStyle.TOTAL else "body"
            cells = [kit.Text(label, self, role=role, wrap=True)]
            for column in range(1, self._columns):
                amount = kit.Text(
                    amounts[column - 1] if column - 1 < len(amounts) else "", self, role=role
                )
                amount.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                cells.append(amount)
            for column, cell in enumerate(cells):
                self._grid.addWidget(cell, row, column)
            self._drawn.extend(cells)
            self._cells.append(cells)
            row += 1

    def text_at(self, row: int, column: int) -> str:
        return self._cells[row][column].text()

    @property
    def line_count(self) -> int:
        return len(self._cells)


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
        page = kit.ScrollArea(spacing=Space.GAP)
        outer.addWidget(page)
        layout = page.body

        layout.addLayout(self._build_header())
        self._fetched = kit.Text(role="caption", tone="muted", wrap=True)
        layout.addWidget(self._fetched)

        self.banner = Banner()
        layout.addWidget(self.banner)

        self._off = EmptyState(
            "The Pay screen is off.",
            "Turn it on under Settings › Pay. Figures are fetched only when you ask, kept "
            "encrypted to your Windows account, and masked on screen until you reveal them.",
        )
        layout.addWidget(self._off)
        self._settings_link = kit.Button(
            "Open Settings", variant="primary", on_click=self.settings_requested.emit
        )
        layout.addWidget(self._settings_link, 0, Qt.AlignmentFlag.AlignLeft)

        self._empty = EmptyState(
            "Nothing fetched yet.",
            "Refresh reads the CTC statement, the monthly report and every payslip the "
            "portal offers. It is not part of the automatic sync.",
        )
        layout.addWidget(self._empty)

        self._content = kit.Box()
        body = QVBoxLayout(self._content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(Space.GAP)
        layout.addWidget(self._content)
        self._views = [self._build_slip(), self._build_year(), self._build_ctc()]
        for view in self._views:
            body.addWidget(view)
        self._show_view(SLIP)

        layout.addStretch(1)
        self.set_enabled_state(False)

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(Space.SNUG)
        header.addWidget(kit.Text("Pay", role="title"))
        header.addSpacing(Space.SNUG)
        self._tabs = kit.Tabs(list(TABS))
        self._tabs.changed.connect(self._show_view)
        header.addWidget(self._tabs)
        header.addStretch(1)
        self._reveal = kit.CheckBox("Show figures")
        self._reveal.setToolTip(
            "Figures stay masked until this is on, and mask again when you leave the screen."
        )
        self._reveal.toggled.connect(self._on_reveal)
        header.addWidget(self._reveal)
        self._refresh = kit.Button("Refresh", on_click=self.refresh_requested.emit)
        self._refresh.setToolTip("Fetch the CTC, the monthly report and any new payslips.")
        header.addWidget(self._refresh)
        return header

    def _build_slip(self) -> QWidget:
        view = kit.Box()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.GAP)

        bar = QHBoxLayout()
        bar.setSpacing(Space.SNUG)
        self._period = kit.ComboBox()
        self._period.setMinimumWidth(170)
        self._period.currentIndexChanged.connect(lambda _index: self._render_slip())
        bar.addWidget(self._period)
        bar.addStretch(1)
        self._save_pdf = kit.Button("Save as PDF…", variant="quiet", on_click=self._on_save_pdf)
        bar.addWidget(self._save_pdf)
        layout.addLayout(bar)

        # The figure the screen is opened for, at the size of one; what it is made of beside.
        self.net_pay = kit.StatCard("Net pay", large=True)
        #: The same card: the slip on view is the net pay on view. Two cards for one figure
        #: said "October" twice, a hand's width apart.
        self.slip_net = self.net_pay
        self.gross = kit.StatCard("Earnings")
        self.deductions = kit.StatCard("Deductions")
        layout.addWidget(card_row(self.net_pay, self.gross, self.deductions))

        self._earnings = _Ledger("Earnings", ("Amount",), self._palette)
        self._deductions = _Ledger("Deductions", ("Amount",), self._palette)
        self._variables = _Ledger("Variables", ("Amount",), self._palette)
        layout.addWidget(card_row(self._earnings, self._deductions, self._variables))
        return view

    def _build_year(self) -> QWidget:
        view = kit.Box()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SNUG)
        self._monthly_header = kit.SectionHeader("The year by month")
        layout.addWidget(self._monthly_header)
        self._monthly = kit.TableWidget(("Particulars",), stretch_last=False, fit_rows=True)
        self._monthly.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self._monthly)
        return view

    def _build_ctc(self) -> QWidget:
        view = kit.Box()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.GAP)
        self.ctc_yearly = kit.StatCard("Cost to company")
        self.ctc_monthly = kit.StatCard("CTC per month")
        layout.addWidget(card_row(self.ctc_yearly, self.ctc_monthly))
        self._ctc = _Ledger("Item", ("Monthly", "Yearly"), self._palette)
        layout.addWidget(self._ctc)
        return view

    def _show_view(self, index: int) -> None:
        for position, view in enumerate(self._views):
            view.setVisible(position == index)

    def show_view(self, index: int) -> None:
        """Open one of the three documents, as a click on its tab would."""
        self._tabs.set_current(index)
        self._show_view(index)

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
        self._tabs.setVisible(enabled)
        self._fetched.setVisible(enabled)
        if not enabled:
            self._snapshot = None
            self._empty.setVisible(False)
            self._content.setVisible(False)
            return
        self._render()

    def show_snapshot(self, snapshot: PaySnapshot) -> None:
        self._snapshot = snapshot
        self._render()

    def hideEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        # Leaving the screen re-masks it: the figures were shown for a look, not for the day.
        self._reveal.setChecked(False)
        super().hideEvent(event)  # type: ignore[arg-type]

    def _on_reveal(self, _checked: bool) -> None:
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
        self._tabs.setVisible(not empty)
        if snapshot is None or empty:
            self._fetched.setText("Encrypted to your Windows account; masked until you ask.")
            return

        fetched = (
            f"Last fetched {fmt.relative_time(snapshot.last_synced, now=datetime.now())}"
            if snapshot.last_synced
            else "Not fetched yet"
        )
        self._fetched.setText(
            f"{fetched} · encrypted to your Windows account · figures hide again when you "
            f"leave this screen"
        )

        ctc = snapshot.ctc.cost_to_company if snapshot.ctc else None
        self.ctc_yearly.set_value(self._money(ctc.yearly) if ctc else "—")
        self.ctc_yearly.set_caption(
            f"for {snapshot.ctc.for_year}" if snapshot.ctc and snapshot.ctc.for_year else ""
        )
        self.ctc_monthly.set_value(self._money(ctc.monthly) if ctc else "—")
        self.ctc_monthly.set_caption("before the yearly items" if ctc else "")

        self._fill_periods(snapshot.payslips)
        self._render_slip()
        self._render_ctc(snapshot.ctc)
        self._render_monthly(snapshot.monthly)

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
        if slip is None:
            for ledger in (self._earnings, self._deductions, self._variables):
                ledger.set_lines([])
            self.net_pay.set_value(fmt.EMPTY)
            self.net_pay.set_caption("No payslip has been fetched.")
            for card in (self.gross, self.deductions):
                card.set_value(fmt.EMPTY)
                card.set_caption("")
            return

        def lines(items: Sequence[object], total: tuple[str, float] | None) -> list[Line]:
            rows: list[Line] = [
                (line.label, (self._money(line.amount),), LineStyle.NORMAL)  # type: ignore[attr-defined]
                for line in items
            ]
            if total is not None:
                rows.append((total[0], (self._money(total[1]),), LineStyle.TOTAL))
            return rows

        self._earnings.set_lines(lines(slip.earnings, ("Gross", slip.gross)))
        self._deductions.set_lines(
            lines(slip.deductions, ("Total deductions", slip.total_deductions))
        )
        self._variables.set_lines(lines(slip.variables, None))
        # A slip with no variables leaves an empty frame beside two full ones.
        self._variables.setVisible(bool(slip.variables))
        self.net_pay.title.setText(f"Net pay for {slip.label}")
        self.net_pay.set_value(self._money(slip.net_pay), tone="good" if self.revealed else None)
        words = slip.in_words if self.revealed else ""
        self.net_pay.set_caption(words or "the slip's net, after deductions")
        self.gross.set_value(self._money(slip.gross))
        self.gross.set_caption(f"{len(slip.earnings)} line(s) on the slip")
        self.deductions.set_value(self._money(slip.total_deductions))
        self.deductions.set_caption(f"{len(slip.deductions)} line(s) on the slip")

    def _render_ctc(self, ctc: CtcStatement | None) -> None:
        if ctc is None:
            self._ctc.set_lines([("Not fetched yet.", (), LineStyle.HEADING)])
            return
        rows: list[Line] = []
        section = ""
        for line in ctc.lines:
            if line.section and line.section != section and line.section != "Earnings":
                section = line.section
                rows.append((section, (), LineStyle.HEADING))
            rows.append(
                (
                    line.label,
                    (self._money(line.monthly), self._money(line.yearly)),
                    LineStyle.TOTAL if line.is_total else LineStyle.NORMAL,
                )
            )
        self._ctc.set_lines(rows)

    def _render_monthly(self, monthly: MonthlyStatement | None) -> None:
        if monthly is None:
            self._monthly_header.set_meta("not fetched yet")
            self._monthly.setRowCount(0)
            return
        self._monthly_header.set_meta(monthly.period_label)
        self._monthly.setColumnCount(1 + len(monthly.months))
        self._monthly.setHorizontalHeaderLabels(["Particulars", *monthly.months])
        header = self._monthly.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for column in range(1, 1 + len(monthly.months)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        rows = [(row.label, *[self._money(value) for value in row.values]) for row in monthly.rows]
        emphasis = ["total" in r.label.lower() or "net" in r.label.lower() for r in monthly.rows]
        _fill(self._monthly, rows, bold=emphasis, bold_last_column=True)

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
    table: QTableWidget,
    rows: Sequence[tuple[str, ...]],
    *,
    bold: list[bool] | None = None,
    bold_last_column: bool = False,
) -> None:
    table.setRowCount(len(rows))
    last = table.columnCount() - 1
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            item = QTableWidgetItem(text)
            if c > 0:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if (bold and bold[r]) or (bold_last_column and c == last):
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            table.setItem(r, c, item)
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


__all__ = ["MASK", "LineStyle", "PayView", "format_inr", "payslip_html", "write_payslip_pdf"]
