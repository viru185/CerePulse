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
    QGridLayout,
    QHBoxLayout,
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
from cerepulse.ui.widgets import Banner, BarChart, Card, SectionTitle, card_row, data_table

#: The trailing blank is a spacer. Every real column here is a short number, so without one
#: to absorb the leftover width either the last column is stranded against the far edge of
#: the window or the row stripes run on past where the data stops.
MONTH_COLUMNS = (
    "Month",
    # Without this, a month of twelve worked days sits beside one of twenty-two and the
    # Worked totals invite a comparison that means nothing.
    "Days",
    "Worked",
    "vs target",
    "Daily avg",
    "Overtime",
    "Short",
    "Typical in",
    "",
)
ANOMALY_COLUMNS = ("Date", "What", "Detail")

#: Monday first, matching ``WeekdayHabit.weekday``.
_WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class InsightsView(QWidget):
    """Forecast, habits, month-over-month and anomalies, from cached history only."""

    refresh_requested = Signal()
    sync_history_requested = Signal()

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._today = date.today()
        self._target = Duration(0)

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

        # The week leads because it is the only section that is different every day. A page
        # of long-run medians moves by minutes a month, reads identically on Tuesday and
        # Thursday, and stops being opened — which was the stated complaint.
        layout.addWidget(SectionTitle("This week vs your usual"))
        self._week = QVBoxLayout()
        self._week.setSpacing(4)
        week_host = QWidget()
        week_host.setLayout(self._week)
        layout.addWidget(week_host)
        self._week_note = _caption()
        layout.addWidget(self._week_note)

        # Predictions next: the one part of the record anyone can still act on; the rest is
        # the record, and a record is read after the forecast, not before it.
        layout.addWidget(SectionTitle("Predictions — how this month lands"))
        self.projected = Card("Projected")
        self.headroom = Card("Banked so far")
        self.needed = Card("Needed daily")
        self.affordable = Card("Short day")
        layout.addWidget(card_row(self.projected, self.headroom, self.needed, self.affordable))
        self._forecast_note = _caption()
        layout.addWidget(self._forecast_note)

        layout.addWidget(SectionTitle("Habits — your normal day"))
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

        # The same weekday figures the grid above lists, as shape. A table answers "how long
        # is a Tuesday"; the chart answers "which day is the long one", which is the question
        # the numbers are usually being scanned for.
        self.weekday_chart = BarChart(palette)
        layout.addWidget(self.weekday_chart)

        layout.addWidget(SectionTitle("Performance — streaks and records"))
        self.streak = Card("On-target streak")
        self.since_short = Card("Since a short day")
        self.longest = Card("Longest day")
        self.earliest = Card("Earliest start")
        layout.addWidget(card_row(self.streak, self.since_short, self.longest, self.earliest))
        self._record_note = _caption()
        layout.addWidget(self._record_note)

        layout.addWidget(SectionTitle("Trends — month by month"))
        self.month_chart = BarChart(palette)
        layout.addWidget(self.month_chart)
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
        self._target = view.work_target
        self._render_footing(view)
        self._render_week(view.week)
        self._render_forecast(report.forecast)
        self._render_habits(report.habits)
        self._render_records(report.records)
        self._render_months(report.months)
        self._render_anomalies(view.anomalies, view.today)

    def _render_week(self, week: object) -> None:
        """One line per day of this week, measured against that weekday's own baseline."""
        from cerepulse.intelligence.week_vs_usual import WeekComparison, describe

        while self._week.count():
            item = self._week.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        if not isinstance(week, WeekComparison) or not week.has_baseline:
            self._week_note.setText(
                "Comparisons appear once enough history is cached to know what your usual "
                "day looks like."
            )
            return
        if not week.days:
            self._week_note.setText("Nothing measured yet this week.")
            return

        for comparison in week.days:
            row = QLabel(f"{comparison.weekday_name} —  {describe(comparison)}")
            row.setWordWrap(True)
            if comparison.is_notable:
                row.setStyleSheet(f"color: {self._palette.text};")
            else:
                row.setObjectName("CardCaption")
            self._week.addWidget(row)

        self._week_note.setText(
            f"Measured against {week.baseline_days} day(s) of your own history. "
            "Deltas under 15m read as usual."
        )

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
        self._render_weekday_chart(habits)

    def _render_weekday_chart(self, habits: Habits) -> None:
        """Typical hours per weekday, against the target.

        Only the weekdays that carry a sample. Drawing an empty Saturday bar beside five
        real ones implies a zero-hour Saturday rather than a day nobody works.
        """
        if not habits.has_enough:
            self.weekday_chart.set_bars(())
            return

        with_sample = [entry for entry in habits.weekdays if entry.sample]
        self.weekday_chart.set_bars(
            [
                (
                    _WEEKDAY_NAMES[entry.weekday],
                    float(entry.typical_worked.minutes),
                    f"{fmt.duration(entry.typical_worked)} across {entry.sample} day(s)",
                )
                for entry in with_sample
            ],
            reference=float(self._target.minutes) if self._target.minutes else None,
            reference_label=f"target {fmt.duration(self._target)}",
            # On the bars, not in the tooltip: a chart that is scanned rather than hovered
            # never shows its tooltip, which made these unlabeled heights.
            value_labels=[fmt.duration(entry.typical_worked) for entry in with_sample],
        )

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

    def _render_month_chart(self, months: tuple[MonthSummary, ...]) -> None:
        """Worked hours per month, oldest to newest, against the per-month target.

        Chronological here even though the table is newest-first: a trend read right to left
        is not a trend anyone can see. The partial current month is included but captioned,
        because leaving it out makes the run of bars stop a month short of today for no
        visible reason.
        """
        if not months:
            self.month_chart.set_bars(())
            return

        target = max((summary.target.minutes for summary in months), default=0)
        self.month_chart.set_bars(
            [
                (
                    summary.label.split()[0][:3],
                    float(summary.worked.minutes),
                    f"{fmt.duration(summary.worked)} of {fmt.duration(summary.target)}"
                    + (
                        " (so far)"
                        if (summary.year, summary.month) == (self._today.year, self._today.month)
                        else ""
                    ),
                )
                for summary in months
            ],
            reference=float(target) if target else None,
            reference_label="full month",
            value_labels=[fmt.duration(summary.worked) for summary in months],
        )

    def _render_months(self, months: tuple[MonthSummary, ...]) -> None:
        self._render_month_chart(months)
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
                str(summary.working_days),
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
                if column == 3:
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
    # These sit inside a page that already scrolls, so they show every row.
    return data_table(columns, fit_rows=True)


def _abs(value: Duration) -> Duration:
    return Duration(abs(value.minutes))


__all__ = ["InsightsView"]
