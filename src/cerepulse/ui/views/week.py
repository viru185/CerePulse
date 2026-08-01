"""Week — seven days against target, with a running surplus and where it is heading.

The week is the unit people actually think in: a short Tuesday matters less if Thursday
made it up, and only a weekly view shows that. It also answers the question a single day
cannot — whether Friday needs to be a long one — which is why the projection sits beside
the totals rather than being left as arithmetic for the reader.

Days are colour-coded by status rather than only by hours. A blank row for a public holiday
and a blank row for an absence look identical otherwise, and they mean opposite things.
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from cerepulse.intelligence.month import DayRollup, WeekAnalysis
from cerepulse.models.attendance import DayStatus
from cerepulse.models.values import Duration
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import Palette, Space
from cerepulse.ui.widgets import Card, SectionTitle, TargetBar, card_row


class WeekView(QWidget):
    """A seven-day strip with per-day bars, the week's delta, and its projected finish."""

    week_changed = Signal(date)
    day_selected = Signal(object)  # date

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._week_start: date | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.SECTION, 20, Space.SECTION, Space.SECTION)
        layout.setSpacing(Space.GAP)

        header = QHBoxLayout()
        header.setSpacing(Space.SNUG)
        previous = QPushButton("‹ Previous")
        previous.clicked.connect(lambda: self._step(-7))
        self._label = SectionTitle("")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._next = QPushButton("Next ›")
        self._next.clicked.connect(lambda: self._step(7))
        # A shortcut back, because the way out of five clicks into the past should not be
        # five clicks forward.
        self._this_week = QPushButton("This week")
        self._this_week.clicked.connect(lambda: self.week_changed.emit(_this_week_start()))
        header.addWidget(previous)
        header.addWidget(self._label, 1)
        header.addWidget(self._next)
        header.addWidget(self._this_week)
        layout.addLayout(header)

        self.total = Card("Worked")
        self.target = Card("Target")
        self.delta = Card("Difference")
        self.projected = Card("Projected")
        layout.addWidget(card_row(self.total, self.target, self.delta, self.projected))

        self._progress = TargetBar(palette)
        layout.addWidget(self._progress)

        layout.addWidget(SectionTitle("Days"))
        self._days = QVBoxLayout()
        self._days.setSpacing(Space.TIGHT + 2)
        host = QWidget()
        host.setLayout(self._days)
        layout.addWidget(host)

        self._extremes = QLabel()
        self._extremes.setObjectName("CardCaption")
        layout.addWidget(self._extremes)
        layout.addStretch(1)

    # --- rendering ------------------------------------------------------------------

    def show_week(self, analysis: WeekAnalysis, target_per_day: Duration) -> None:
        self._week_start = analysis.week_start
        end = analysis.week_start + timedelta(days=6)
        self._label.setText(
            f"{analysis.week_start.strftime('%d %b').lstrip('0')} – "
            f"{end.strftime('%d %b %Y').lstrip('0')}"
        )
        # There is nothing to read in a week that has not happened.
        self._next.setEnabled(analysis.week_start < _this_week_start())
        self._this_week.setEnabled(analysis.week_start != _this_week_start())

        self.total.set_value(fmt.duration(analysis.total_worked), accent=self._palette.work)
        self.total.set_caption(
            f"across {len(analysis.measured)} completed day(s)"
            + (
                f" · {fmt.duration(analysis.in_progress)} today so far"
                if analysis.in_progress.minutes
                else ""
            )
        )
        self.target.set_value(fmt.duration(analysis.target))
        self.target.set_caption(f"of {fmt.duration(analysis.full_target)} for the full week")

        ahead = analysis.delta.minutes >= 0
        self.delta.set_value(
            fmt.duration(analysis.delta, signed=True),
            accent=self._palette.good if ahead else self._palette.bad,
        )
        self.delta.set_caption("ahead of target" if ahead else "behind target")

        self._render_projection(analysis)
        self._progress.set_progress(
            analysis.progress,
            f"{round(analysis.progress * 100)}% of the week's {fmt.duration(analysis.full_target)}",
        )
        self._render_days(analysis, target_per_day)
        self._render_extremes(analysis)

    def _render_projection(self, analysis: WeekAnalysis) -> None:
        """Where the week lands if nothing changes — the one figure Friday depends on."""
        if not analysis.days_ahead:
            self.projected.set_value(fmt.duration(analysis.total_worked))
            self.projected.set_caption("the week is done")
            return

        projected = analysis.projected_total
        short = analysis.full_target - projected
        self.projected.set_value(fmt.duration(projected))
        if short.minutes > 0:
            self.projected.set_caption(
                f"{analysis.days_ahead} day(s) left · still {fmt.duration(short)} short"
            )
        else:
            self.projected.set_caption(f"{analysis.days_ahead} day(s) left at target · on track")

    def _render_extremes(self, analysis: WeekAnalysis) -> None:
        best, worst = analysis.best_day, analysis.worst_day
        if best is None or worst is None:
            # One day is not a comparison, and saying so beats naming a "best day" of one.
            self._extremes.setText("")
            return
        self._extremes.setText(
            f"Longest {best.day.strftime('%A').rstrip()} {fmt.duration(best.worked)}"
            f"  ·  shortest {worst.day.strftime('%A')} {fmt.duration(worst.worked)}"
        )

    def _render_days(self, analysis: WeekAnalysis, target_per_day: Duration) -> None:
        while self._days.count():
            item = self._days.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

        for rollup in analysis.days:
            row = _DayRow(rollup, target_per_day, self._palette)
            row.clicked.connect(lambda day=rollup.day: self.day_selected.emit(day))
            self._days.addWidget(row)

    def _step(self, days: int) -> None:
        if self._week_start is not None:
            self.week_changed.emit(self._week_start + timedelta(days=days))


class _DayRow(QWidget):
    """One day: label, proportional bar, hours, and how much of the target that was."""

    clicked = Signal()

    def __init__(
        self, rollup: DayRollup, target: Duration, palette: Palette, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SNUG + 2)

        worked = rollup.worked
        colour = _status_colour(rollup, target, palette)

        label = QLabel(rollup.day.strftime("%a %d").replace(" 0", " "))
        label.setFixedWidth(64)
        label.setStyleSheet(f"color: {palette.text_muted};")
        layout.addWidget(label)

        bar = QWidget()
        bar.setFixedHeight(14)
        bar.setStyleSheet(_bar_style(worked, target, rollup.status, colour, palette))
        layout.addWidget(bar, 1)

        hours = QLabel(fmt.duration(worked) if rollup.status.counts_as_worked else "—")
        hours.setFixedWidth(56)
        hours.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hours.setStyleSheet("font-variant-numeric: tabular-nums;")
        layout.addWidget(hours)

        share = QLabel(_share_text(rollup, target))
        share.setFixedWidth(48)
        share.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        share.setObjectName("CardCaption")
        layout.addWidget(share)

        self.setToolTip(_row_tooltip(rollup))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def _share_text(rollup: DayRollup, target: Duration) -> str:
    if not rollup.status.counts_as_worked or target.minutes <= 0:
        return ""
    if rollup.unmeasured:
        # Nothing was recorded, so 0% would be a measurement rather than the absence of one.
        return "—"
    return f"{round(rollup.worked.minutes / target.minutes * 100)}%"


def _row_tooltip(rollup: DayRollup) -> str:
    label = rollup.day.strftime("%A, %d %B").replace(" 0", " ")
    body = f"{label} — {rollup.status.value.replace('_', ' ')}"
    if rollup.in_progress:
        body += "\nStill in progress"
    if rollup.estimated:
        body += "\nEstimated from the grid; punch detail not synced"
    if rollup.unmeasured:
        body += "\nMarked worked, but the portal holds no hours for it"
    return body


#: Colour per status. A holiday and an absence both render as an empty row otherwise, and
#: nothing on the screen distinguishes a day off from a day missed.
def _status_colour(rollup: DayRollup, target: Duration, palette: Palette) -> str:
    if rollup.status is DayStatus.HOLIDAY:
        return palette.adjust
    if rollup.status is DayStatus.WEEKLY_OFF:
        return palette.border
    if not rollup.status.counts_as_worked:
        # Leave, absence, anything else the portal names but does not pay hours for.
        return palette.rest
    if rollup.unmeasured:
        return palette.bad
    return palette.good if rollup.worked >= target else palette.work


def _bar_style(
    worked: Duration, target: Duration, status: DayStatus, colour: str, palette: Palette
) -> str:
    """A hard-edged fill showing the fraction of target worked.

    A gradient needs its two stops to straddle the boundary. Letting them land on the same
    offset — which happens at 0% and at 100% — makes Qt interpolate across the whole bar,
    so a full day renders as a wash rather than a solid fill.
    """
    radius = "border-radius: 7px;"
    if not status.counts_as_worked:
        # Not a shortfall, so it is drawn as a flat band in the status colour rather than
        # as an empty track that reads as a day someone failed to work.
        return f"background: {colour}; {radius}"

    filled = fmt.percent(worked, target) if target.minutes else 0.0
    if filled >= 1.0:
        return f"background: {colour}; {radius}"
    if filled <= 0.0:
        return f"background: {palette.border}; {radius}"

    return (
        f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
        f" stop:0 {colour}, stop:{filled:.4f} {colour},"
        f" stop:{min(filled + 0.0001, 1.0):.4f} {palette.border}, stop:1 {palette.border});"
        f" {radius}"
    )


def _this_week_start() -> date:
    from cerepulse.intelligence.month import week_start_for

    return week_start_for(date.today())
