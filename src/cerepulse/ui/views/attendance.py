"""Attendance — month rollup, hours bank, and the day table.

The hours-bank banner is careful about honesty. Most days in a month have no cached punch
log, so their worked time is estimated from the grid's gross span minus the break
allowance. The banner says how many days are estimates rather than presenting a precise
figure it cannot support.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cerepulse.intelligence.month import MonthAnalysis
from cerepulse.models.attendance import AttendanceMonth, DayStatus
from cerepulse.services.attendance import MonthView
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import Palette
from cerepulse.ui.widgets import Banner, Card, SectionTitle, card_row

COLUMNS = ("Date", "Day", "Status", "In", "Out", "Worked", "Late", "Remarks")


class AttendanceView(QWidget):
    """A month of attendance, with a row per day."""

    day_selected = Signal(date)
    month_changed = Signal(int, int)
    refresh_requested = Signal()

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(14)

        layout.addLayout(self._build_header())

        self.banner = Banner()
        layout.addWidget(self.banner)

        self.bank = Banner()
        layout.addWidget(self.bank)

        self.worked = Card("Worked")
        self.overtime = Card("Overtime")
        self.short_days = Card("Short days")
        self.average_in = Card("Average in")
        layout.addWidget(card_row(self.worked, self.overtime, self.short_days, self.average_in))

        layout.addWidget(SectionTitle("Days"))
        self.table = self._build_table()
        layout.addWidget(self.table, 1)

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._period = QComboBox()
        self._period.setMinimumWidth(160)
        self._period.currentIndexChanged.connect(self._emit_month)
        row.addWidget(self._period)
        row.addStretch(1)

        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_requested)
        row.addWidget(refresh)
        return row

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(0, len(COLUMNS))
        table.setHorizontalHeaderLabels(COLUMNS)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.cellDoubleClicked.connect(self._emit_day)
        return table

    # --- rendering ------------------------------------------------------------------

    def set_available_months(self, months: list[tuple[int, int]], current: tuple[int, int]) -> None:
        """Populate the period picker without re-emitting a change for the current month."""
        self._period.blockSignals(True)
        self._period.clear()
        for year, month in months:
            self._period.addItem(fmt.month_label(year, month), (year, month))
        index = self._period.findData(current)
        if index >= 0:
            self._period.setCurrentIndex(index)
        self._period.blockSignals(False)

    def show_month(self, view: MonthView) -> None:
        analysis = view.analysis
        self.worked.set_value(fmt.duration(analysis.total_worked), accent=self._palette.work)
        self.worked.set_caption(f"of {fmt.duration(analysis.month_target)} target")
        self.overtime.set_value(
            fmt.duration(analysis.total_overtime),
            accent=self._palette.good if analysis.total_overtime else None,
        )
        self.short_days.set_value(
            str(analysis.short_days),
            accent=self._palette.bad if analysis.short_days else None,
        )
        self.average_in.set_value(
            analysis.average_in_time.strftime("%I:%M %p").lstrip("0")
            if analysis.average_in_time
            else fmt.EMPTY
        )
        self._render_bank(analysis)
        self._render_table(view.month)

    def _render_bank(self, analysis: MonthAnalysis) -> None:
        from cerepulse.intelligence.insights import Severity

        qualifier = ""
        if analysis.estimated_days:
            # Say so rather than implying a precision the cache cannot support.
            qualifier = (
                f"  ({analysis.estimated_days} of {analysis.working_days_elapsed} days "
                f"estimated until their punch detail syncs)"
            )

        if analysis.is_ahead:
            self.bank.show_message(
                f"Hours bank: {fmt.duration(analysis.bank_delta, signed=True)} ahead across "
                f"{analysis.working_days_elapsed} working days.{qualifier}",
                Severity.SUCCESS,
            )
            return

        tail = ""
        if analysis.required_daily_average and analysis.working_days_remaining:
            tail = (
                f" Work {fmt.duration(analysis.required_daily_average)} per day over the "
                f"remaining {analysis.working_days_remaining} to finish even."
            )
        self.bank.show_message(
            f"Hours bank: {fmt.duration(analysis.bank_delta)} behind.{tail}{qualifier}",
            Severity.WARNING,
        )

    def _render_table(self, month: AttendanceMonth) -> None:
        self.table.setRowCount(len(month.days))
        for row, day in enumerate(month.days):
            values = (
                day.day.strftime("%d %b").lstrip("0"),
                day.weekday,
                _status_text(day.status),
                fmt.clock_time(day.first_in),
                fmt.clock_time(day.last_out),
                day.total_hours.as_clock() if day.total_hours else fmt.EMPTY,
                day.late_mark.as_clock() if day.late_mark else "",
                day.remarks,
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 0:
                    # Carries the real date, so double-click can open that day.
                    item.setData(Qt.ItemDataRole.UserRole, day.day)
                if column >= 3:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                if column == 2:
                    item.setForeground(_status_colour(day.status, self._palette))
                self.table.setItem(row, column, item)

            if not day.detail_loaded and day.status.counts_as_worked:
                # A subtle marker: this row's numbers come from the grid, not its punches.
                worked_cell = self.table.item(row, 5)
                if worked_cell is not None:
                    worked_cell.setToolTip("Punch detail not synced yet")

    # --- interaction ----------------------------------------------------------------

    def _emit_day(self, row: int, _column: int) -> None:
        item = self.table.item(row, 0)
        if item is not None and item.data(Qt.ItemDataRole.UserRole):
            self.day_selected.emit(item.data(Qt.ItemDataRole.UserRole))

    def _emit_month(self, index: int) -> None:
        data = self._period.itemData(index)
        if data:
            self.month_changed.emit(*data)


def _status_text(status: DayStatus) -> str:
    return {
        DayStatus.PRESENT: "Present",
        DayStatus.HALF_DAY: "Half day",
        DayStatus.ABSENT: "Absent",
        DayStatus.WEEKLY_OFF: "Weekly off",
        DayStatus.HOLIDAY: "Holiday",
        DayStatus.LEAVE: "Leave",
        DayStatus.UNKNOWN: "—",
    }[status]


def _status_colour(status: DayStatus, palette: Palette) -> QColor:
    return QColor(
        {
            DayStatus.PRESENT: palette.good,
            DayStatus.HALF_DAY: palette.rest,
            DayStatus.ABSENT: palette.bad,
            DayStatus.LEAVE: palette.adjust,
        }.get(status, palette.text_muted)
    )
