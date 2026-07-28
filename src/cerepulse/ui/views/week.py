"""Week — seven days against target, with a running surplus or deficit.

The week is the unit people actually think in: a short Tuesday matters less if Thursday
made it up, and only a weekly view shows that.
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from cerepulse.intelligence.month import DayRollup, WeekAnalysis
from cerepulse.models.attendance import DayStatus
from cerepulse.models.values import Duration
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import Palette
from cerepulse.ui.widgets import Card, SectionTitle, card_row


class WeekView(QWidget):
    """A seven-day strip with per-day bars and the week's running delta."""

    week_changed = Signal(date)

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._week_start: date | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(14)

        header = QHBoxLayout()
        previous = QPushButton("‹ Previous")
        previous.clicked.connect(lambda: self._step(-7))
        self._label = SectionTitle("")
        following = QPushButton("Next ›")
        following.clicked.connect(lambda: self._step(7))
        header.addWidget(previous)
        header.addWidget(self._label, 1)
        header.addWidget(following)
        layout.addLayout(header)

        self.total = Card("Worked")
        self.target = Card("Target")
        self.delta = Card("Difference")
        layout.addWidget(card_row(self.total, self.target, self.delta))

        layout.addWidget(SectionTitle("Days"))
        self._days = QVBoxLayout()
        self._days.setSpacing(6)
        host = QWidget()
        host.setLayout(self._days)
        layout.addWidget(host)
        layout.addStretch(1)

    # --- rendering ------------------------------------------------------------------

    def show_week(self, analysis: WeekAnalysis, target_per_day: Duration) -> None:
        self._week_start = analysis.week_start
        end = analysis.week_start + timedelta(days=6)
        self._label.setText(
            f"{analysis.week_start.strftime('%d %b').lstrip('0')} – "
            f"{end.strftime('%d %b %Y').lstrip('0')}"
        )

        self.total.set_value(fmt.duration(analysis.total_worked), accent=self._palette.work)
        self.total.set_caption(f"across {analysis.working_days} working day(s)")
        self.target.set_value(fmt.duration(analysis.target))

        ahead = analysis.delta.minutes >= 0
        self.delta.set_value(
            fmt.duration(analysis.delta, signed=True),
            accent=self._palette.good if ahead else self._palette.bad,
        )
        self.delta.set_caption("ahead of target" if ahead else "behind target")

        self._render_days(analysis, target_per_day)

    def _render_days(self, analysis: WeekAnalysis, target_per_day: Duration) -> None:
        while self._days.count():
            item = self._days.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

        for rollup in analysis.days:
            self._days.addWidget(_DayRow(rollup, target_per_day, self._palette))

    def _step(self, days: int) -> None:
        if self._week_start is not None:
            self.week_changed.emit(self._week_start + timedelta(days=days))


class _DayRow(QWidget):
    """One day: label, proportional bar, and hours."""

    def __init__(
        self, rollup: DayRollup, target: Duration, palette: Palette, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        worked = rollup.worked
        status = rollup.status

        label = QLabel(rollup.day.strftime("%a %d").replace(" 0", " "))
        label.setFixedWidth(64)
        label.setStyleSheet(f"color: {palette.text_muted};")
        layout.addWidget(label)

        bar = QWidget()
        bar.setFixedHeight(14)
        bar.setStyleSheet(_bar_style(worked, target, status, palette))
        layout.addWidget(bar, 1)

        hours = QLabel(fmt.duration(worked) if status.counts_as_worked else "—")
        hours.setFixedWidth(56)
        hours.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hours.setStyleSheet("font-variant-numeric: tabular-nums;")
        if rollup.estimated:
            hours.setToolTip("Estimated — this day's punch detail is not cached yet")
        layout.addWidget(hours)


def _bar_style(worked: Duration, target: Duration, status: DayStatus, palette: Palette) -> str:
    """A hard-edged fill showing the fraction of target worked.

    A gradient needs its two stops to straddle the boundary. Letting them land on the same
    offset — which happens at 0% and at 100% — makes Qt interpolate across the whole bar,
    so a full day renders as a wash rather than a solid fill.
    """
    radius = "border-radius: 7px;"
    if not status.counts_as_worked:
        return f"background: {palette.border}; {radius}"

    filled = fmt.percent(worked, target) if target.minutes else 0.0
    colour = palette.good if worked >= target else palette.work
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
