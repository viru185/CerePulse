"""Attendance — month rollup, hours bank, heatmap, day table, and the day drawer.

The hours-bank banner is careful about honesty. Most days in a month have no cached punch
log, so their worked time is estimated from the grid's gross span minus the break
allowance. The banner says how many days are estimates rather than presenting a precise
figure it cannot support.

The heatmap and the table answer different questions and both are worth having. A table of
thirty rows answers "what happened on the 14th"; the calendar answers "what does this month
look like" — whether the short days cluster in one bad week or are spread across four.

The drawer answers a third: "what happened *inside* the 14th". Reaching that used to mean
leaving for the Today screen and finding the way back, which is a lot of navigation for a
glance at four punches.

Whether a day needs attention is decided in ``intelligence/attention.py``, not here, so the
row highlight, the filter and the heatmap ring cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cerepulse.intelligence.attention import Attention, swipe_state
from cerepulse.intelligence.day import DayAnalysis
from cerepulse.intelligence.insights import Severity
from cerepulse.intelligence.month import DayRollup, MonthAnalysis
from cerepulse.models.attendance import AttendanceDay, DayStatus
from cerepulse.models.swipe import SwipeRequest, SwipeStatus
from cerepulse.services.attendance import MonthView
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import Palette, Space
from cerepulse.ui.widgets import (
    Banner,
    Card,
    DayTimeline,
    MonthHeatmap,
    SectionTitle,
    StatusChip,
    card_row,
    data_table,
    step_button,
)

COLUMNS = ("Date", "Day", "Status", "In", "Out", "Worked", "Late", "Swipe", "Remarks")


class _Row:
    """One table row's data, gathered so a filter reads it without touching widgets."""

    __slots__ = ("analysis", "attention", "day", "requests", "rollup")

    def __init__(
        self,
        day: AttendanceDay,
        rollup: DayRollup | None,
        attention: Attention | None,
        requests: list[SwipeRequest],
        analysis: DayAnalysis | None,
    ) -> None:
        self.day = day
        self.rollup = rollup
        self.attention = attention
        self.requests = requests
        self.analysis = analysis


_AWAY = (DayStatus.LEAVE, DayStatus.ABSENT, DayStatus.HALF_DAY)
_OFF = (DayStatus.HOLIDAY, DayStatus.WEEKLY_OFF)

#: The filters, in the order they are offered. Each decides from a :class:`_Row` alone, so
#: adding one is a line here rather than a branch in the render path.
FILTERS: tuple[tuple[str, Callable[[_Row, MonthAnalysis], bool]], ...] = (
    ("All days", lambda row, month: True),
    ("Needs attention", lambda row, month: row.attention is not None),
    (
        "Short days",
        lambda row, month: (
            row.rollup is not None
            and row.rollup.is_working_day
            and not row.rollup.in_progress
            and row.rollup.worked < month.policy.work_target
        ),
    ),
    ("Overtime days", lambda row, month: row.day.total_hours > month.policy.shift_span),
    ("Present", lambda row, month: row.day.status is DayStatus.PRESENT),
    ("Outdoor duty", lambda row, month: row.day.has_outdoor_duty),
    ("Leave and absence", lambda row, month: row.day.status in _AWAY),
    ("Holidays and offs", lambda row, month: row.day.status in _OFF),
    ("Has a swipe request", lambda row, month: bool(row.requests)),
    (
        "Punch detail missing",
        lambda row, month: row.day.status.counts_as_worked and not row.day.detail_loaded,
    ),
)


class AttendanceView(QWidget):
    """A month of attendance, as a calendar, a row per day, and a drawer for one day."""

    day_selected = Signal(date)
    month_changed = Signal(int, int)
    refresh_requested = Signal()
    fetch_detail_requested = Signal()
    open_portal = Signal()

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._view: MonthView | None = None
        self._rows: list[_Row] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.SECTION, 20, Space.SECTION, Space.SECTION)
        layout.setSpacing(Space.ROW + 2)

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

        # Two columns that actually divide the width. The calendar used to sit at a fixed
        # 222 px beside an addStretch, so several hundred pixels to its right were simply
        # void while the day drawer below was crushed into 300 px.
        body = QHBoxLayout()
        body.setSpacing(Space.GAP)

        left = QVBoxLayout()
        left.setSpacing(Space.SNUG)

        # The legend sits beside the calendar rather than under it. A calendar cell is
        # square and capped, so seven of them stop at about 400 px however wide the window
        # is — and stacking the legend below left that leftover strip empty while making the
        # column taller. Side by side, the strip is the legend's and the table starts sooner.
        calendar = QHBoxLayout()
        calendar.setSpacing(Space.GAP)
        self.heatmap = MonthHeatmap(palette)
        self.heatmap.day_selected.connect(self.show_day)
        calendar.addWidget(self.heatmap)
        calendar.addWidget(self._build_legend(), 1)
        left.addLayout(calendar)

        left.addWidget(SectionTitle("Days"))
        self.table = self._build_table()
        left.addWidget(self.table, 1)
        body.addLayout(left, 3)

        self.drawer = _DayDrawer(palette)
        self.drawer.open_in_today.connect(self.day_selected)
        self.drawer.open_portal.connect(self.open_portal)
        body.addWidget(self.drawer, 2)
        layout.addLayout(body, 1)

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(Space.SNUG)

        # Stepping is what people actually do; the dropdown was the only way to move a month
        # and it makes reading three months in sequence six clicks instead of two.
        previous = step_button("◀", "Previous month", lambda: self._step_month(-1))
        row.addWidget(previous)

        self._period = QComboBox()
        self._period.setMinimumWidth(160)
        self._period.currentIndexChanged.connect(self._emit_month)
        row.addWidget(self._period)

        self._next = step_button("▶", "Next month", lambda: self._step_month(1))
        row.addWidget(self._next)

        self._filter = QComboBox()
        self._filter.setMinimumWidth(170)
        for label, _predicate in FILTERS:
            self._filter.addItem(label)
        self._filter.currentIndexChanged.connect(lambda _index: self._apply_filter())
        row.addWidget(self._filter)
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
        layout.setContentsMargins(0, Space.ROW, 0, 0)
        layout.setSpacing(3)
        for text in (
            "Deeper fill = more hours",
            "Green = target met",
            "Red outline = needs attention",
            "Dashed outline = outdoor duty",
            "Click a day for its detail",
        ):
            label = QLabel(text)
            label.setObjectName("CardCaption")
            label.setWordWrap(True)
            layout.addWidget(label)
        # Now that this sits beside the calendar rather than under it, the stretch does
        # something: it holds the lines at the top of the strip instead of centring them.
        layout.addStretch(1)
        return host

    def _build_table(self) -> QTableWidget:
        # Sized to content with Remarks taking the slack. Stretching every column equally
        # gave a clock time the same width as a sentence, so Remarks truncated to
        # "Attendance ..." while In and Out sat in a sea of padding.
        table = data_table(COLUMNS, selectable=True)
        table.cellDoubleClicked.connect(self._emit_day)
        table.itemSelectionChanged.connect(self._on_selection)
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
        # Against what the month has asked for *so far*, not against its eventual total.
        # Comparing with the full month read "0m of 168h" on the second of August — a
        # month's work presented as a debt on a day when nothing was owed.
        if analysis.has_started:
            self.worked.set_caption(
                f"of {fmt.duration(analysis.elapsed_target)} owed so far  ·  "
                f"{fmt.duration(analysis.month_target)} for the full month"
            )
        else:
            self.worked.set_caption(
                f"nothing owed yet  ·  {fmt.duration(analysis.month_target)} once the "
                f"month is worked"
            )
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
            year=analysis.year,
            month=analysis.month,
        )
        self._render_table(view)
        self._apply_filter()

    def _render_bank(self, analysis: MonthAnalysis) -> None:
        if not analysis.has_started:
            # No completed working day means no bank. Reporting "+0m ahead across 0 working
            # days" is arithmetic about nothing, and it made an empty month look assessed.
            self.bank.show_message(
                f"{analysis.working_days_remaining} working day(s) ahead this month. "
                f"Nothing to compare yet.",
                Severity.INFO,
            )
            return

        notes = []
        if analysis.on_duty_days:
            # Real working days with no swipes to measure. They were excluded from the bank
            # and from the excluded count too, so a week of outdoor duty simply vanished.
            notes.append(
                f"{analysis.on_duty_days} day(s) on outdoor duty — worked, but with no "
                f"swipes to measure"
            )
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
        rollups = {rollup.day: rollup for rollup in view.analysis.days}
        self._rows = [
            _Row(
                day,
                rollups.get(day.day),
                view.attention.get(day.day),
                view.swipes.get(day.day, []),
                view.analyses.get(day.day),
            )
            for day in month.days
        ]

        self.table.setRowCount(len(self._rows))
        for row, entry in enumerate(self._rows):
            day = entry.day
            values = (
                day.day.strftime("%d %b").lstrip("0"),
                day.weekday,
                _status_text(day.status),
                fmt.clock_time(day.first_in),
                fmt.clock_time(day.last_out),
                fmt.duration(day.total_hours) if day.total_hours else fmt.EMPTY,
                fmt.duration(day.late_mark) if day.late_mark else "",
                _swipe_text(entry.requests),
                day.remarks,
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 0:
                    # Carries the real date, so double-click can open that day.
                    item.setData(Qt.ItemDataRole.UserRole, day.day)
                if column in (3, 4, 5, 6):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                if column == 2:
                    item.setForeground(_status_colour(day.status, self._palette))
                if column == 7:
                    item.setForeground(_swipe_colour(entry.requests, self._palette))
                    item.setToolTip(_swipe_tooltip(entry.requests))
                if entry.attention is not None and column == 0:
                    # The date cell alone. Colouring the whole row would fight the status
                    # and swipe columns, which already carry meaning in the same channel.
                    item.setForeground(QColor(self._palette.bad))
                    item.setToolTip(entry.attention.reason)
                self.table.setItem(row, column, item)

            if not day.detail_loaded and day.status.counts_as_worked:
                # A subtle marker: this row's numbers come from the grid, not its punches.
                worked_cell = self.table.item(row, 5)
                if worked_cell is not None:
                    worked_cell.setToolTip("Punch detail not synced yet")

    # --- interaction ----------------------------------------------------------------

    def _apply_filter(self) -> None:
        """Hide rows the filter excludes. Filtering keeps the selection; rebuilding loses it."""
        if self._view is None:
            return
        label, predicate = FILTERS[max(0, self._filter.currentIndex())]
        month = self._view.analysis

        shown = 0
        for row, entry in enumerate(self._rows):
            keep = predicate(entry, month)
            shown += int(keep)
            self.table.setRowHidden(row, not keep)

        if self._filter.currentIndex() == 0:
            self.banner.clear_message(key="filter")
        elif not shown:
            # An empty table under a filter reads as a bug, so name which filter emptied it.
            self.banner.show_message(
                f"No days match “{label}” this month.", Severity.SUCCESS, key="filter"
            )
        else:
            self.banner.show_message(
                f"Showing {shown} of {len(self._rows)} days — {label.lower()}.",
                Severity.INFO,
                key="filter",
            )

    def _on_selection(self) -> None:
        """A single click fills the drawer; a double click still opens the day fully."""
        rows = {index.row() for index in self.table.selectedIndexes()}
        if len(rows) != 1:
            return
        entry = self._rows[rows.pop()] if self._rows else None
        if entry is not None:
            self.drawer.show_day(entry)

    def show_day(self, day: date) -> None:
        """Select and reveal one date — the heatmap's click lands here."""
        for row, entry in enumerate(self._rows):
            if entry.day.day == day:
                self.table.selectRow(row)
                cell = self.table.item(row, 0)
                if cell is not None:
                    self.table.scrollToItem(cell)
                self.drawer.show_day(entry)
                return

    def _emit_day(self, row: int, _column: int) -> None:
        item = self.table.item(row, 0)
        if item is not None and item.data(Qt.ItemDataRole.UserRole):
            self.day_selected.emit(item.data(Qt.ItemDataRole.UserRole))

    def _emit_month(self, index: int) -> None:
        data = self._period.itemData(index)
        if data:
            self.month_changed.emit(*data)

    def _step_month(self, delta: int) -> None:
        """Move one entry through the picker, which is already in newest-first order."""
        target = self._period.currentIndex() - delta
        if 0 <= target < self._period.count():
            self._period.setCurrentIndex(target)


class _DayDrawer(QWidget):
    """One day beside the table: its shape, its punches, and what can be done about it."""

    open_in_today = Signal(object)  # date
    open_portal = Signal()

    #: A floor, not a fixed width. Pinned at 300 px the timeline inside it was drawn at a
    #: third of the width its constants assumed, and the fact lines wrapped mid-column.
    MIN_WIDTH = 300

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._entry: _Row | None = None
        self.setMinimumWidth(self.MIN_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.GAP, 0, 0, 0)
        layout.setSpacing(Space.SNUG)

        top = QHBoxLayout()
        self._date = QLabel()
        self._date.setStyleSheet("font-size: 14px; font-weight: 600;")
        top.addWidget(self._date)
        top.addStretch(1)
        self._status = StatusChip("", palette.text_muted)
        top.addWidget(self._status)
        layout.addLayout(top)

        self._headline = QLabel()
        self._headline.setObjectName("CardCaption")
        self._headline.setWordWrap(True)
        layout.addWidget(self._headline)

        self._timeline = DayTimeline(palette)
        layout.addWidget(self._timeline)

        self._facts = QLabel()
        self._facts.setWordWrap(True)
        self._facts.setStyleSheet("font-variant-numeric: tabular-nums;")
        layout.addWidget(self._facts)

        self._note = QLabel()
        self._note.setObjectName("CardCaption")
        self._note.setWordWrap(True)
        layout.addWidget(self._note)

        layout.addStretch(1)

        self._open = QPushButton("Open in Today")
        self._open.clicked.connect(self._emit_open)
        self._copy = QPushButton("Copy summary")
        self._copy.clicked.connect(self._copy_summary)
        self._portal = QPushButton("Open in SpineHR")
        self._portal.clicked.connect(self.open_portal)
        for button in (self._open, self._copy, self._portal):
            layout.addWidget(button)

        self.setVisible(False)

    def show_day(self, entry: _Row) -> None:
        self._entry = entry
        day, analysis = entry.day, entry.analysis

        self._date.setText(day.day.strftime("%A, %d %B").replace(" 0", " "))
        self._status.set_state(_status_text(day.status), _status_hex(day.status, self._palette))

        if analysis is not None and analysis.next_action is not None:
            self._headline.setText(analysis.next_action.headline)
        elif entry.attention is not None:
            self._headline.setText(entry.attention.reason)
        else:
            self._headline.setText("")

        # Outdoor duty leads, whatever else the day says. There are no hours to report, so
        # the remark — "EAE Training in Bengaluru" — is the entire content of the day.
        if entry.day.has_outdoor_duty:
            self._headline.setText(
                f"Outdoor duty — {entry.day.remarks.strip()}"
                if entry.day.remarks.strip()
                else "Outdoor duty — worked off site, no swipes to measure"
            )

        segments = analysis.segments if analysis is not None else ()
        self._timeline.set_day(segments, leave_at=analysis.leave_at if analysis else None)
        self._timeline.setVisible(bool(segments))

        self._facts.setText(self._fact_lines(entry))
        self._note.setText(self._note_text(entry))
        self._copy.setEnabled(analysis is not None)
        self.setVisible(True)

    def _fact_lines(self, entry: _Row) -> str:
        day, analysis = entry.day, entry.analysis
        lines = [
            f"In {fmt.clock_time(day.first_in)}     Out {fmt.clock_time(day.last_out)}",
            f"Span {fmt.duration(day.total_hours) if day.total_hours else fmt.EMPTY}",
        ]
        if analysis is not None:
            lines.append(f"Worked {analysis.worked}     Break {analysis.break_taken}")
        elif entry.rollup is not None and entry.rollup.estimated:
            lines.append(f"Worked ≈{entry.rollup.worked}  (estimated)")
        if day.late_mark:
            lines.append(f"Late {day.late_mark}")
        return "\n".join(lines)

    def _note_text(self, entry: _Row) -> str:
        notes = []
        if entry.analysis is None and entry.day.status.counts_as_worked:
            notes.append(
                "Punch detail is not synced for this day, so the figures come from the "
                "monthly summary and any break inside the day is not counted."
            )
        if entry.day.remarks:
            notes.append(entry.day.remarks)
        for request in entry.requests:
            notes.append(
                f"Swipe request ({request.direction}): "
                f"{request.status.value.replace('_', ' ')}"
                + (f" — {request.remark}" if request.remark else "")
            )
        return "\n".join(notes)

    def _emit_open(self) -> None:
        if self._entry is not None:
            self.open_in_today.emit(self._entry.day.day)

    def _copy_summary(self) -> None:
        from PySide6.QtWidgets import QApplication

        from cerepulse.ui.views.today import summary_text

        if self._entry is None or self._entry.analysis is None:
            return
        QApplication.clipboard().setText(summary_text(self._entry.analysis))
        self._copy.setText("Copied")
        _restore_later(self._copy, "Copy summary")


def _restore_later(button: QPushButton, text: str) -> None:
    from PySide6.QtCore import QTimer

    QTimer.singleShot(1500, lambda: button.setText(text))


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


def _status_hex(status: DayStatus, palette: Palette) -> str:
    return {
        DayStatus.PRESENT: palette.good,
        DayStatus.HALF_DAY: palette.rest,
        DayStatus.ABSENT: palette.bad,
        DayStatus.LEAVE: palette.adjust,
        DayStatus.ON_DUTY: palette.work,
    }.get(status, palette.text_muted)


def _status_colour(status: DayStatus, palette: Palette) -> QColor:
    return QColor(_status_hex(status, palette))


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
