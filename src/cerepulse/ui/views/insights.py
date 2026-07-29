"""Insights — what the history says, as opposed to what today says.

Four questions, in the order someone actually asks them: where does this month land, what
does a normal day look like for me, how does this month compare with the last, and is
anything odd.

The screen refuses to fill itself. With too little history it says so and points at Sync
history rather than drawing charts from four days — a habit computed from four days is not
a habit, and presenting it as one teaches the user to distrust the whole screen.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cerepulse.intelligence.anomalies import Anomaly
from cerepulse.intelligence.insights import Severity
from cerepulse.intelligence.trends import (
    MIN_SAMPLE,
    RECENT_WINDOW,
    DayRecord,
    Forecast,
    Habits,
    MonthSummary,
    Records,
)
from cerepulse.models.values import Duration
from cerepulse.services.attendance import TrendsView
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import Palette
from cerepulse.ui.widgets import Banner, Card, SectionTitle, card_row

#: The trailing blank is a spacer. Every real column here is a short number, so without one
#: to absorb the leftover width either the last column is stranded against the far edge of
#: the window or the row stripes run on past where the data stops.
MONTH_COLUMNS = ("Month", "Worked", "vs target", "Daily avg", "Overtime", "Short", "Typical in", "")
ANOMALY_COLUMNS = ("Date", "What", "Detail")


class InsightsView(QWidget):
    """Forecast, habits, month-over-month and anomalies, from cached history only."""

    refresh_requested = Signal()
    sync_history_requested = Signal()

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._today = date.today()

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

        self.footing = Banner()
        layout.addWidget(self.footing)

        layout.addWidget(SectionTitle("How this month lands"))
        self.projected = Card("Projected")
        self.headroom = Card("Banked so far")
        self.needed = Card("Needed daily")
        self.affordable = Card("Short day")
        layout.addWidget(card_row(self.projected, self.headroom, self.needed, self.affordable))
        self._forecast_note = _caption()
        layout.addWidget(self._forecast_note)

        layout.addWidget(SectionTitle("Your normal day"))
        self.typical_in = Card("Typical in")
        self.typical_out = Card("Typical out")
        self.typical_worked = Card("Typical worked")
        self.typical_break = Card("Typical break")
        layout.addWidget(
            card_row(self.typical_in, self.typical_out, self.typical_worked, self.typical_break)
        )
        self._habit_note = _caption()
        layout.addWidget(self._habit_note)

        # A grid, not padded text: a proportional font makes "Monday" and "Wednesday" very
        # different widths, so column alignment has to come from the layout.
        self._weekdays = QGridLayout()
        self._weekdays.setHorizontalSpacing(18)
        self._weekdays.setVerticalSpacing(4)
        self._weekdays.setColumnStretch(4, 1)
        weekday_host = QWidget()
        weekday_host.setLayout(self._weekdays)
        layout.addWidget(weekday_host)

        layout.addWidget(SectionTitle("Streaks and records"))
        self.streak = Card("On-target streak")
        self.since_short = Card("Since a short day")
        self.longest = Card("Longest day")
        self.earliest = Card("Earliest start")
        layout.addWidget(card_row(self.streak, self.since_short, self.longest, self.earliest))
        self._record_note = _caption()
        layout.addWidget(self._record_note)

        layout.addWidget(SectionTitle("Month by month"))
        self.months = _table(MONTH_COLUMNS)
        layout.addWidget(self.months)

        layout.addWidget(SectionTitle("Worth a look"))
        self.anomalies = _table(ANOMALY_COLUMNS)
        layout.addWidget(self.anomalies)
        self._anomaly_note = _caption()
        layout.addWidget(self._anomaly_note)

        layout.addStretch(1)

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        title = QLabel("Insights")
        title.setObjectName("HeroLabel")
        row.addWidget(title)
        row.addStretch(1)

        history = QPushButton("Sync history")
        history.clicked.connect(self.sync_history_requested)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_requested)
        row.addWidget(history)
        row.addWidget(refresh)
        return row

    # --- rendering ------------------------------------------------------------------

    def show_trends(self, view: TrendsView) -> None:
        report = view.report
        self._today = view.today
        self._render_footing(view)
        self._render_forecast(report.forecast)
        self._render_habits(report.habits)
        self._render_records(report.records)
        self._render_months(report.months)
        self._render_anomalies(view.anomalies, view.today)

    def _render_footing(self, view: TrendsView) -> None:
        """State what the screen is standing on before it says anything else.

        Every figure below is only as good as the cache behind it, and the difference
        between "six months of history" and "eleven days" changes how much weight any of it
        deserves.
        """
        report = view.report
        if view.is_thin:
            self.footing.show_message(
                f"Only {report.measured_days} measured day(s) cached — too few to read "
                f"anything into. Sync history to fill this in.",
                Severity.WARNING,
            )
            return

        span = ""
        if report.span is not None:
            span = f"{fmt.day_label(report.span[0])} to {fmt.day_label(report.span[1])}"
        estimated = ""
        if report.estimated_days:
            estimated = (
                f" · {report.estimated_days} estimated from the grid, "
                f"{report.exact_days} exact from punch logs"
            )
        self.footing.show_message(
            f"{report.measured_days} working days across {view.months_cached} month(s), "
            f"{span}{estimated}.",
            Severity.INFO,
        )

    def _render_forecast(self, forecast: Forecast | None) -> None:
        if forecast is None:
            for card in (self.projected, self.headroom, self.needed, self.affordable):
                card.set_value(fmt.EMPTY)
                card.set_caption("")
            self._forecast_note.setText("Nothing worked this month yet.")
            return

        palette = self._palette
        ahead = forecast.projected_delta is not None and forecast.projected_delta.minutes >= 0
        self.projected.set_value(
            fmt.duration(forecast.projected_delta, signed=True),
            accent=palette.good if ahead else palette.bad,
        )
        self.projected.set_caption("at month end")

        banked = forecast.headroom.minutes >= 0
        self.headroom.set_value(
            fmt.duration(forecast.headroom, signed=True),
            accent=palette.good if banked else palette.bad,
        )
        self.headroom.set_caption("ahead" if banked else "behind")

        if forecast.required_daily is None:
            self.needed.set_value("—", accent=palette.good)
            self.needed.set_caption("already level")
        else:
            self.needed.set_value(fmt.duration(forecast.required_daily), accent=palette.rest)
            self.needed.set_caption(f"across {forecast.working_days_remaining} day(s) left")

        self.affordable.set_value(
            "Yes" if forecast.short_day_affordable else "No",
            accent=palette.good if forecast.short_day_affordable else palette.text_muted,
        )
        self.affordable.set_caption(
            "you could take a half day" if forecast.short_day_affordable else "nothing spare yet"
        )

        pace = fmt.duration(forecast.assumed_daily)
        self._forecast_note.setText(
            f"Projection assumes the remaining {forecast.working_days_remaining} day(s) match "
            f"your recent pace of {pace} a day — the median of your last {RECENT_WINDOW} "
            f"working days, not a promise."
        )

    def _render_habits(self, habits: Habits) -> None:
        self.typical_in.set_value(fmt.clock_time(habits.typical_in))
        self.typical_out.set_value(fmt.clock_time(habits.typical_out))
        self.typical_worked.set_value(fmt.duration(habits.typical_worked))

        if habits.typical_break is None:
            self.typical_break.set_value(fmt.EMPTY)
            # The grid has no break column, so this stays blank until day detail is cached
            # rather than being back-solved from the span.
            self.typical_break.set_caption("needs cached punch detail")
        else:
            self.typical_break.set_value(fmt.duration(habits.typical_break))
            self.typical_break.set_caption(f"across {habits.break_sample} day(s)")

        if not habits.has_enough:
            self._habit_note.setText(
                f"Fewer than {MIN_SAMPLE} measured days — these are not habits yet."
            )
        elif habits.drifting and habits.in_time_drift is not None:
            direction = "later" if habits.in_time_drift.minutes > 0 else "earlier"
            self._habit_note.setText(
                f"Lately you have been starting {fmt.duration_words(_abs(habits.in_time_drift))} "
                f"{direction} than usual — {fmt.clock_time(habits.recent_in)} across your last "
                f"{RECENT_WINDOW} days, against {fmt.clock_time(habits.typical_in)} overall."
            )
        else:
            self._habit_note.setText(
                f"Medians across {habits.measured_days} working days, so one unusual day "
                f"does not move them."
            )

        self._render_weekdays(habits)

    def _render_weekdays(self, habits: Habits) -> None:
        while self._weekdays.count():
            item = self._weekdays.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

        if not habits.has_enough:
            return

        muted = f"color: {self._palette.text_muted};"
        for row, habit in enumerate(habits.weekdays):
            cells = (
                habit.name,
                f"in {fmt.clock_time(habit.typical_in)}",
                f"{fmt.duration(habit.typical_worked)} worked",
                f"across {habit.sample} day(s)",
            )
            for column, text in enumerate(cells):
                label = QLabel(text)
                label.setStyleSheet(muted)
                self._weekdays.addWidget(label, row, column)

    def _render_records(self, records: Records) -> None:
        palette = self._palette
        self.streak.set_value(
            str(records.current_streak),
            accent=palette.good if records.current_streak else None,
        )
        self.streak.set_caption(f"best {records.best_streak}")

        if records.days_since_short is None:
            self.since_short.set_value(str(records.measured_days))
            self.since_short.set_caption("no short day on record")
        else:
            self.since_short.set_value(str(records.days_since_short))
            self.since_short.set_caption("working days")

        self._set_record(self.longest, records.longest_day)
        self._set_record(self.earliest, records.earliest_start)

        if not records.has_enough:
            self._record_note.setText(
                f"Fewer than {MIN_SAMPLE} measured days — no records worth the name yet."
            )
        elif records.best_week is not None:
            self._record_note.setText(
                f"Best week began {fmt.day_label(records.best_week.day)} "
                f"with {records.best_week.value} worked."
            )
        else:
            self._record_note.setText("")

    def _set_record(self, card: Card, record: DayRecord | None) -> None:
        if record is None:
            card.set_value(fmt.EMPTY)
            card.set_caption("")
            return
        card.set_value(record.value)
        card.set_caption(fmt.day_label(record.day))

    def _render_months(self, months: tuple[MonthSummary, ...]) -> None:
        # Newest first: the interesting comparison is with the month just gone, not with
        # whatever happens to be oldest in the cache.
        rows = list(reversed(months))
        self.months.setRowCount(len(rows))

        for index, summary in enumerate(rows):
            ahead = summary.delta.minutes >= 0
            # The current month is a fortnight of a month sitting next to complete ones;
            # without saying so, the Worked column invites a comparison of unlike things.
            partial = (summary.year, summary.month) == (self._today.year, self._today.month)
            cells = (
                f"{summary.label} (so far)" if partial else summary.label,
                fmt.duration(summary.worked),
                fmt.duration(summary.delta, signed=True),
                fmt.duration(summary.daily_average),
                fmt.duration(summary.overtime),
                str(summary.short_days),
                fmt.clock_time(summary.average_in),
            )
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                if column == 2:
                    item.setForeground(QColor(self._palette.good if ahead else self._palette.bad))
                if summary.estimated_days:
                    item.setToolTip(
                        f"{summary.estimated_days} of {summary.working_days} day(s) estimated "
                        f"from the grid rather than punch logs"
                    )
                self.months.setItem(index, column, item)

    def _render_anomalies(self, anomalies: list[Anomaly], today: date) -> None:
        self.anomalies.setRowCount(len(anomalies))
        for index, anomaly in enumerate(anomalies):
            cells = (
                fmt.day_label(anomaly.day),
                anomaly.kind.value.replace("_", " ").capitalize(),
                anomaly.detail,
            )
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 1 and anomaly.severity is Severity.WARNING:
                    item.setForeground(QColor(self._palette.rest))
                self.anomalies.setItem(index, column, item)

        self.anomalies.setVisible(bool(anomalies))
        # "Nothing unusual" is a finding. A bare heading over blank space reads as a
        # section that failed to load.
        self._anomaly_note.setText(
            "" if anomalies else f"Nothing unusual in {fmt.month_label(today.year, today.month)}."
        )

    def show_error(self, message: str) -> None:
        self.banner.show_message(message, Severity.CRITICAL)


# --- helpers ----------------------------------------------------------------------------


def _caption() -> QLabel:
    label = QLabel()
    label.setObjectName("CardCaption")
    label.setWordWrap(True)
    return label


def _table(columns: tuple[str, ...]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setShowGrid(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setStretchLastSection(True)
    # These tables sit inside a scrolling page, so they must show every row rather than
    # scrolling internally — a scrollbar inside a scrollbar is unusable with a wheel.
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setSizeAdjustPolicy(QAbstractItemView.SizeAdjustPolicy.AdjustToContents)
    return table


def _abs(value: Duration) -> Duration:
    return Duration(abs(value.minutes))


__all__ = ["InsightsView"]
