"""Insights — what the history says, as opposed to what today says.

Questions in the order someone actually asks them: how is this week going against my usual,
where does this month land, what does a normal day look like for me, how do the months
compare, and is anything odd.

The screen refuses to fill itself. With too little history it says so and points at Sync
history rather than drawing charts from four days — a habit computed from four days is not
a habit, and presenting it as one teaches the user to distrust the whole screen.

What the screen stands on is said beside its title, before any figure: the redesign moved it
out of a banner, which is where errors go, and an honest footnote is not an error.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
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
    WeekdayHabit,
)
from cerepulse.models.values import Duration
from cerepulse.services.attendance import TrendsView
from cerepulse.ui import formatting as fmt
from cerepulse.ui import kit
from cerepulse.ui.kit.text import font_for, measure
from cerepulse.ui.theme import Palette, Space
from cerepulse.ui.widgets import Banner, BarChart, card_row, empty_layout

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
        page = kit.ScrollArea(spacing=Space.GAP)
        outer.addWidget(page)
        layout = page.body

        layout.addLayout(self._build_header())
        # What every figure below stands on, beside the title rather than in a banner.
        self.footing = kit.Text(tone="muted", wrap=True)
        layout.addWidget(self.footing)

        self.banner = Banner()
        layout.addWidget(self.banner)

        # The week leads because it is the only section that is different every day. A page
        # of long-run medians moves by minutes a month, reads identically on Tuesday and
        # Thursday, and stops being opened — which was the stated complaint.
        self._week_header = kit.SectionHeader("This week against your usual")
        layout.addWidget(self._week_header)
        # A grid, not padded text: a proportional font makes "Monday" and "Wednesday" very
        # different widths, so column alignment has to come from the layout.
        self._week = QGridLayout()
        self._week.setContentsMargins(0, 0, 0, 0)
        self._week.setHorizontalSpacing(Space.GAP)
        self._week.setVerticalSpacing(Space.TIGHT)
        self._week.setColumnStretch(1, 1)
        week_host = kit.Box()
        week_host.setLayout(self._week)
        layout.addWidget(week_host)
        self._week_note = _caption()
        layout.addWidget(self._week_note)

        # Predictions next: the one part of the record anyone can still act on; the rest is
        # the record, and a record is read after the forecast, not before it.
        layout.addWidget(kit.SectionHeader("How this month lands"))
        self.projected = kit.StatCard("Projected")
        self.headroom = kit.StatCard("Banked so far")
        self.needed = kit.StatCard("Needed each day")
        self.affordable = kit.StatCard("A short day")
        layout.addWidget(card_row(self.projected, self.headroom, self.needed, self.affordable))
        self._forecast_note = _caption()
        layout.addWidget(self._forecast_note)

        layout.addWidget(kit.SectionHeader("Your normal day"))
        self.typical_in = kit.StatCard("Typical arrival")
        self.typical_out = kit.StatCard("Typical leaving")
        self.typical_worked = kit.StatCard("Typical day")
        self.typical_break = kit.StatCard("Typical break")
        layout.addWidget(
            card_row(self.typical_in, self.typical_out, self.typical_worked, self.typical_break)
        )
        self._habit_note = _caption()
        layout.addWidget(self._habit_note)

        # When each weekday starts, as a dot for the median and a band for the middle half of
        # those days: the spread is what says whether "9:40" is a habit or an average of chaos.
        layout.addWidget(kit.SectionHeader("When you arrive, by weekday"))
        self.arrivals = _ArrivalChart(palette)
        layout.addWidget(self.arrivals)
        self._arrivals_note = _caption(
            "Dot: the median. Band: the middle half of your days — the wider it is, the "
            "less that weekday has a habit."
        )
        layout.addWidget(self._arrivals_note)

        # The same weekdays as shape. A table answers "how long is a Tuesday"; the chart
        # answers "which day is the long one", which is what the numbers are scanned for.
        layout.addWidget(kit.SectionHeader("How long each weekday runs"))
        self.weekday_chart = BarChart(palette)
        layout.addWidget(self.weekday_chart)

        layout.addWidget(kit.SectionHeader("Streaks and records"))
        self.streak = kit.StatCard("On-target streak")
        self.since_short = kit.StatCard("Since a short day")
        self.longest = kit.StatCard("Longest day")
        self.earliest = kit.StatCard("Earliest start")
        layout.addWidget(card_row(self.streak, self.since_short, self.longest, self.earliest))
        self._record_note = _caption()
        layout.addWidget(self._record_note)

        layout.addWidget(kit.SectionHeader("Month by month"))
        self.month_chart = BarChart(palette)
        layout.addWidget(self.month_chart)
        # These sit inside a page that already scrolls, so they show every row.
        self.months = kit.TableWidget(MONTH_COLUMNS, fit_rows=True)
        layout.addWidget(self.months)

        self._anomalies_header = kit.SectionHeader("Worth a look")
        layout.addWidget(self._anomalies_header)
        self._anomalies = QVBoxLayout()
        self._anomalies.setContentsMargins(0, 0, 0, 0)
        self._anomalies.setSpacing(2)
        self._anomalies_host = kit.Box()
        self._anomalies_host.setLayout(self._anomalies)
        layout.addWidget(self._anomalies_host)
        self._anomaly_note = _caption()
        layout.addWidget(self._anomaly_note)

        layout.addStretch(1)

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(Space.SNUG)
        row.addWidget(kit.Text("Insights", role="title"))
        row.addStretch(1)
        row.addWidget(
            kit.Button("Sync history", variant="quiet", on_click=self.sync_history_requested.emit)
        )
        row.addWidget(kit.Button("Refresh", on_click=self.refresh_requested.emit))
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

        empty_layout(self._week)
        self._week_header.set_meta("")

        if not isinstance(week, WeekComparison) or not week.has_baseline:
            self._week_note.setText(
                "Comparisons appear once enough history is cached to know what your usual "
                "day looks like."
            )
            return
        if not week.days:
            self._week_note.setText("Nothing measured yet this week.")
            return

        for row, comparison in enumerate(week.days):
            # The notable days in full text, the usual ones muted: the eye goes to what
            # differs, which is the only reason to read this section.
            tone = None if comparison.is_notable else "muted"
            self._week.addWidget(kit.Text(comparison.weekday_name, role="body-strong"), row, 0)
            self._week.addWidget(kit.Text(describe(comparison), tone=tone, wrap=True), row, 1)

        self._week_header.set_meta(f"from {week.baseline_days} day(s) of your history")
        self._week_note.setText("Differences under 15m read as usual.")

    def _render_footing(self, view: TrendsView) -> None:
        """State what the screen is standing on before it says anything else.

        Every figure below is only as good as the cache behind it, and the difference
        between "six months of history" and "eleven days" changes how much weight any of it
        deserves.
        """
        report = view.report
        if view.is_thin:
            self.footing.setText(
                f"Only {report.measured_days} measured day(s) cached — too few to read "
                f"anything into. Sync history to fill this in."
            )
            self.footing.set_tone("rest")
            return

        span = ""
        if report.span is not None:
            span = f", {fmt.day_label(report.span[0])} to {fmt.day_label(report.span[1])}"
        estimated = ""
        if report.estimated_days:
            estimated = (
                f" · {report.estimated_days} estimated from the grid, "
                f"{report.exact_days} exact from punch logs"
            )
        self.footing.setText(
            f"From {report.measured_days} working days across {view.months_cached} "
            f"month(s){span}{estimated}."
        )
        self.footing.set_tone("muted")

    def _render_forecast(self, forecast: Forecast | None) -> None:
        if forecast is None:
            for card in (self.projected, self.headroom, self.needed, self.affordable):
                card.set_value(fmt.EMPTY)
                card.set_caption("")
            self._forecast_note.setText("Nothing worked this month yet.")
            return

        ahead = forecast.projected_delta is not None and forecast.projected_delta.minutes >= 0
        self.projected.set_value(
            fmt.duration(forecast.projected_delta, signed=True), tone="good" if ahead else "bad"
        )
        self.projected.set_caption("at month end")

        banked = forecast.headroom.minutes >= 0
        self.headroom.set_value(
            fmt.duration(forecast.headroom, signed=True), tone="good" if banked else "bad"
        )
        self.headroom.set_caption("ahead" if banked else "behind")

        if forecast.required_daily is None:
            self.needed.set_value(fmt.EMPTY, tone="good")
            self.needed.set_caption("already level")
        else:
            self.needed.set_value(fmt.duration(forecast.required_daily), tone="rest")
            self.needed.set_caption(f"across {forecast.working_days_remaining} day(s) left")

        self.affordable.set_value(
            "Yes" if forecast.short_day_affordable else "No",
            tone="good" if forecast.short_day_affordable else "muted",
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
        # The count behind them is the note under the row, said once rather than four times.
        self.typical_in.set_value(fmt.clock_time(habits.typical_in))
        self.typical_in.set_caption("the median first in")
        self.typical_out.set_value(fmt.clock_time(habits.typical_out))
        self.typical_out.set_caption("the median last out")
        self.typical_worked.set_value(fmt.duration(habits.typical_worked))
        self.typical_worked.set_caption("first in to last out")

        if habits.typical_break is None:
            self.typical_break.set_value(fmt.EMPTY)
            # The grid has no break column, so this stays blank until day detail is cached
            # rather than being back-solved from the span.
            self.typical_break.set_caption("needs cached punch detail")
        else:
            self.typical_break.set_value(fmt.duration(habits.typical_break), tone="rest")
            self.typical_break.set_caption(f"from {habits.break_sample} day(s) with punch logs")

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

        self.arrivals.set_weekdays(habits.weekdays if habits.has_enough else ())
        self._arrivals_note.setVisible(habits.has_enough)
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

    def _render_records(self, records: Records) -> None:
        self.streak.set_value(
            str(records.current_streak), tone="good" if records.current_streak else None
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

    def _set_record(self, card: kit.StatCard, record: DayRecord | None) -> None:
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
        empty_layout(self._anomalies)
        for anomaly in anomalies:
            warning = anomaly.severity is Severity.WARNING
            self._anomalies.addWidget(
                kit.ListRow(
                    self._palette,
                    icon="warning" if warning else "info",
                    icon_tone="rest" if warning else "muted",
                    title=anomaly.kind.value.replace("_", " ").capitalize(),
                    detail=anomaly.detail,
                    kind=fmt.day_label(anomaly.day),
                )
            )
        self._anomalies_host.setVisible(bool(anomalies))
        self._anomalies_header.set_meta(f"{len(anomalies)} this month" if anomalies else "")
        # "Nothing unusual" is a finding. A bare heading over blank space reads as a
        # section that failed to load.
        self._anomaly_note.setText(
            "" if anomalies else f"Nothing unusual in {fmt.month_label(today.year, today.month)}."
        )
        self._anomaly_note.setVisible(not anomalies)

    def show_error(self, message: str) -> None:
        self.banner.show_message(message, Severity.CRITICAL)


# --- helpers ----------------------------------------------------------------------------


def _caption(text: str = "") -> kit.Text:
    return kit.Text(text, role="caption", tone="muted", wrap=True)


class _ArrivalChart(QWidget):
    """Each weekday's arrivals on one clock: a dot at the median, a band over the middle half.

    Painted in one piece so the rows share a scale and a ruler. The text is drawn in the
    kit's own role fonts, so it matches the labels around it at every display scale.
    """

    ROW = 26
    AXIS = 20
    #: Never narrower than an hour, so a very regular person's chart is not a smear of
    #: ticks five minutes apart.
    MIN_SPAN = 60

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._rows: tuple[WeekdayHabit, ...] = ()
        self._domain = (9 * 60, 10 * 60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_weekdays(self, weekdays: tuple[WeekdayHabit, ...]) -> None:
        self._rows = tuple(habit for habit in weekdays if habit.typical_in is not None)
        moments = [
            _minutes(moment)
            for habit in self._rows
            for moment in (habit.typical_in, *(habit.arrivals or ()))
            if moment is not None
        ]
        if moments:
            first = (min(moments) - 10) // 15 * 15
            last = -(-(max(moments) + 10) // 15) * 15
            if last - first < self.MIN_SPAN:
                last = first + self.MIN_SPAN
            self._domain = (first, last)
        self.setVisible(bool(self._rows))
        self.setToolTip(
            "\n".join(
                f"{habit.name}: usually {fmt.clock_time(habit.typical_in)}"
                + (
                    f", most days {fmt.clock_time(habit.arrivals[0])} to "
                    f"{fmt.clock_time(habit.arrivals[1])}"
                    if habit.arrivals
                    else ""
                )
                + f" · {habit.sample} day(s)"
                for habit in self._rows
            )
        )
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 — Qt override
        return QSize(400, len(self._rows) * self.ROW + self.AXIS + Space.TIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 — Qt override
        return QSize(0, self.sizeHint().height())

    def _columns(self) -> tuple[float, float]:
        left = measure("Wed", role="body-strong") + Space.ROW
        right = measure("10:30 AM · 45 days", role="caption") + Space.ROW
        return float(left), float(right)

    def _x(self, minutes: float, left: float, width: float) -> float:
        first, last = self._domain
        return left + (minutes - first) / (last - first) * width

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 — Qt override
        if not self._rows:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self._palette
        name_width, label_width = self._columns()
        inset = 6.0
        left = name_width + inset
        width = max(1.0, self.width() - name_width - label_width - 2 * inset)
        bottom = len(self._rows) * self.ROW

        # The ruler's ticks, as faint lines through every row and a time under each.
        first, last = self._domain
        step = 15 if last - first <= 90 else (30 if last - first <= 240 else 60)
        caption = font_for("caption")
        painter.setFont(caption)
        metrics = QFontMetrics(caption)
        tick = -(-first // step) * step
        while tick <= last:
            x = round(self._x(tick, left, width)) + 0.5
            painter.setPen(QPen(QColor(palette.border), 1))
            painter.drawLine(QPointF(x, 0), QPointF(x, bottom))
            text = _clock_short(tick)
            half = metrics.horizontalAdvance(text) / 2
            painter.setPen(QColor(palette.text_muted))
            painter.drawText(
                QRectF(x - half - 1, bottom + 2, 2 * half + 2, self.AXIS - 2),
                Qt.AlignmentFlag.AlignCenter,
                text,
            )
            tick += step

        work = QColor(palette.work)
        band = QColor(palette.work)
        band.setAlphaF(0.28)
        for index, habit in enumerate(self._rows):
            top = index * self.ROW
            middle = top + self.ROW / 2
            painter.setFont(font_for("body-strong"))
            painter.setPen(QColor(palette.text))
            painter.drawText(
                QRectF(0, top, name_width, self.ROW),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                habit.name[:3],
            )
            if habit.arrivals is not None:
                low, high = (_minutes(moment) for moment in habit.arrivals)
                x0, x1 = self._x(low, left, width), self._x(high, left, width)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(band)
                painter.drawRoundedRect(QRectF(x0, middle - 6, max(2.0, x1 - x0), 12), 3, 3)
            assert habit.typical_in is not None
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(work)
            painter.drawEllipse(
                QPointF(self._x(_minutes(habit.typical_in), left, width), middle), 5, 5
            )
            painter.setFont(caption)
            painter.setPen(QColor(palette.text_muted))
            painter.drawText(
                QRectF(self.width() - label_width, top, label_width, self.ROW),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{fmt.clock_time(habit.typical_in)} · {habit.sample} days",
            )
        painter.end()


def _minutes(moment: time) -> int:
    return moment.hour * 60 + moment.minute


def _clock_short(minutes: int) -> str:
    moment = datetime.combine(date.today(), time(0)) + timedelta(minutes=minutes)
    return moment.strftime("%I:%M").lstrip("0")


def _abs(value: Duration) -> Duration:
    return Duration(abs(value.minutes))


__all__ = ["InsightsView"]
