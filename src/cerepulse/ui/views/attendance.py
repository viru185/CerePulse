"""Attendance — month rollup, hours bank, heatmap, and the day table.

The hours-bank banner is careful about honesty. Most days in a month have no cached punch
log, so their worked time is estimated from the grid's gross span minus the break
allowance. The banner says how many days are estimates rather than presenting a precise
figure it cannot support.

The heatmap and the table answer different questions and both are worth having. A table of
thirty rows answers "what happened on the 14th"; the calendar answers "what does this month
look like" — whether the short days cluster in one bad week or are spread across all four.

Whether a day needs attention is decided in ``intelligence/attention.py``, not here, so the
row highlight, the filter and the heatmap ring cannot drift apart.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cerepulse.intelligence.attention import swipe_state
from cerepulse.intelligence.insights import Severity
from cerepulse.intelligence.month import MonthAnalysis
from cerepulse.models.attendance import DayStatus
from cerepulse.models.swipe import SwipeRequest, SwipeStatus
from cerepulse.services.attendance import MonthView
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import Palette
from cerepulse.ui.widgets import Banner, Card, MonthHeatmap, SectionTitle, card_row, data_table

COLUMNS = ("Date", "Day", "Status", "In", "Out", "Worked", "Late", "Swipe", "Remarks")


class AttendanceView(QWidget):
    """A month of attendance, as a calendar and as a row per day."""

    day_selected = Signal(date)
    month_changed = Signal(int, int)
    refresh_requested = Signal()
    fetch_detail_requested = Signal()

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._view: MonthView | None = None

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

        shape = QHBoxLayout()
        shape.setSpacing(18)
        self.heatmap = MonthHeatmap(palette)
        self.heatmap.day_selected.connect(self.day_selected)
        shape.addWidget(self.heatmap)
        shape.addWidget(self._build_legend())
        shape.addStretch(1)
        layout.addLayout(shape)

        layout.addWidget(SectionTitle("Days"))
        self.table = self._build_table()
        layout.addWidget(self.table, 1)

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._period = QComboBox()
        self._period.setMinimumWidth(160)
        self._period.currentIndexChanged.connect(self._emit_month)
        row.addWidget(self._period)

        self._only_attention = QCheckBox("Needs attention only")
        self._only_attention.toggled.connect(self._apply_filter)
        row.addWidget(self._only_attention)
        row.addStretch(1)

        # Punch detail arrives five days per refresh, so a fresh month needs four or five
        # of them. This asks for the rest in one go rather than leaving the user to guess.
        self._fetch_detail = QPushButton("Fetch punch detail")
        self._fetch_detail.clicked.connect(self.fetch_detail_requested)
        row.addWidget(self._fetch_detail)

        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_requested)
        row.addWidget(refresh)
        return row

    def _build_legend(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(3)
        for text in (
            "Deeper fill = more hours",
            "Green = target met",
            "Red outline = needs attention",
        ):
            label = QLabel(text)
            label.setObjectName("CardCaption")
            layout.addWidget(label)
        layout.addStretch(1)
        return host

    def _build_table(self) -> QTableWidget:
        # Sized to content with Remarks taking the slack. Stretching every column equally
        # gave a clock time the same width as a sentence, so Remarks truncated to
        # "Attendance ..." while In and Out sat in a sea of padding.
        table = data_table(COLUMNS, selectable=True)
        table.cellDoubleClicked.connect(self._emit_day)
        return table

    # --- rendering ------------------------------------------------------------------

    def set_available_months(
        self,
        months: list[tuple[int, int]],
        current: tuple[int, int],
        *,
        cached: set[tuple[int, int]] | None = None,
    ) -> None:
        """Populate the period picker without re-emitting a change for the current month.

        Months the portal can serve are all listed, not just the cached ones, so history is
        reachable. Uncached entries are marked, since choosing one costs a round trip.
        """
        held = cached if cached is not None else set(months)
        self._period.blockSignals(True)
        self._period.clear()
        for year, month in months:
            label = fmt.month_label(year, month)
            if (year, month) not in held:
                label = f"{label}  ·  not synced"
            self._period.addItem(label, (year, month))
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
        self._view = view
        # Nothing to fetch is worth saying with the button rather than in a banner.
        self._fetch_detail.setEnabled(view.pending_detail > 0)
        self._fetch_detail.setText(
            f"Fetch punch detail ({view.pending_detail})"
            if view.pending_detail
            else "Punch detail complete"
        )
        self._render_bank(analysis)
        self.heatmap.set_days(
            list(analysis.days),
            target=analysis.policy.work_target,
            attention=set(view.attention),
        )
        self._render_table(view)
        self._apply_filter(self._only_attention.isChecked())

    def _render_bank(self, analysis: MonthAnalysis) -> None:
        from cerepulse.intelligence.insights import Severity

        notes = []
        if analysis.estimated_days:
            # Say so rather than implying a precision the cache cannot support.
            notes.append(
                f"{analysis.estimated_days} of {analysis.working_days_elapsed} days "
                f"estimated until their punch detail syncs"
            )
        if analysis.unmeasured_days:
            # These are excluded from the bank entirely; saying so stops the totals looking
            # unaccountably low against the calendar.
            notes.append(
                f"{analysis.unmeasured_days} day(s) excluded — the portal holds no punches for them"
            )
        qualifier = f"  ({'; '.join(notes)})" if notes else ""

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

    def _render_table(self, view: MonthView) -> None:
        month = view.month
        self.table.setRowCount(len(month.days))
        for row, day in enumerate(month.days):
            attention = view.attention.get(day.day)
            requests = view.swipes.get(day.day, [])
            values = (
                day.day.strftime("%d %b").lstrip("0"),
                day.weekday,
                _status_text(day.status),
                fmt.clock_time(day.first_in),
                fmt.clock_time(day.last_out),
                day.total_hours.as_clock() if day.total_hours else fmt.EMPTY,
                day.late_mark.as_clock() if day.late_mark else "",
                _swipe_text(requests),
                day.remarks,
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 0:
                    # Carries the real date, so double-click can open that day.
                    item.setData(Qt.ItemDataRole.UserRole, day.day)
                    # And whether the row is outstanding, so the filter needs no second pass.
                    item.setData(Qt.ItemDataRole.UserRole + 1, attention is not None)
                if column in (3, 4, 5, 6):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                if column == 2:
                    item.setForeground(_status_colour(day.status, self._palette))
                if column == 7:
                    item.setForeground(_swipe_colour(requests, self._palette))
                    item.setToolTip(_swipe_tooltip(requests))
                if attention is not None and column == 0:
                    # The date cell alone. Colouring the whole row would fight the status
                    # and swipe columns, which already carry meaning in the same channel.
                    item.setForeground(QColor(self._palette.bad))
                    item.setToolTip(attention.reason)
                self.table.setItem(row, column, item)

            if not day.detail_loaded and day.status.counts_as_worked:
                # A subtle marker: this row's numbers come from the grid, not its punches.
                worked_cell = self.table.item(row, 5)
                if worked_cell is not None:
                    worked_cell.setToolTip("Punch detail not synced yet")

    # --- interaction ----------------------------------------------------------------

    def _apply_filter(self, only_attention: bool) -> None:
        """Hide settled rows. Filtering by row rather than rebuilding keeps the selection."""
        outstanding = 0
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            flagged = bool(item and item.data(Qt.ItemDataRole.UserRole + 1))
            outstanding += int(flagged)
            self.table.setRowHidden(row, only_attention and not flagged)

        if only_attention and not outstanding and self._view is not None:
            # An empty table under a checkbox reads as a bug. This is good news, so say it.
            self.banner.show_message("Nothing outstanding this month.", Severity.SUCCESS)
        elif only_attention:
            self.banner.show_message(
                f"Showing {outstanding} day(s) that need something doing.", Severity.INFO
            )
        else:
            self.banner.clear_message()

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
        DayStatus.ON_DUTY: "On duty",
        DayStatus.UNKNOWN: "—",
    }[status]


def _status_colour(status: DayStatus, palette: Palette) -> QColor:
    return QColor(
        {
            DayStatus.PRESENT: palette.good,
            DayStatus.HALF_DAY: palette.rest,
            DayStatus.ABSENT: palette.bad,
            DayStatus.LEAVE: palette.adjust,
            DayStatus.ON_DUTY: palette.work,
        }.get(status, palette.text_muted)
    )


def _swipe_text(requests: list[SwipeRequest]) -> str:
    """What the Swipe column says. Blank, not a dash, when nothing was ever filed.

    Most days have no request and never needed one; a column of dashes would read as
    thirty missing values rather than as an ordinary month.
    """
    state = swipe_state(requests)
    if state is None:
        return ""
    label = {
        SwipeStatus.IN_PROCESS: "Pending",
        SwipeStatus.APPROVED: "Approved",
        SwipeStatus.REJECTED: "Rejected",
        SwipeStatus.CANCELLED: "Cancelled",
        SwipeStatus.UNKNOWN: "Filed",
    }[state]
    return f"{label} ×{len(requests)}" if len(requests) > 1 else label


def _swipe_colour(requests: list[SwipeRequest], palette: Palette) -> QColor:
    state = swipe_state(requests)
    return QColor(
        {
            SwipeStatus.APPROVED: palette.good,
            SwipeStatus.IN_PROCESS: palette.rest,
            SwipeStatus.REJECTED: palette.bad,
        }.get(state, palette.text_muted)  # type: ignore[arg-type]
    )


def _swipe_tooltip(requests: list[SwipeRequest]) -> str:
    lines = []
    for request in requests:
        remark = f" — {request.remark}" if request.remark else ""
        lines.append(f"{request.direction}: {request.status.value.replace('_', ' ')}{remark}")
    return "\n".join(lines)
