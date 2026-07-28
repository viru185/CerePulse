"""Today — the default screen, and the one that answers "when can I leave?".

Everything here is rendered from a :class:`DayAnalysis`; the view holds no business logic
of its own. Each metric card is clickable and opens the explanation the intelligence layer
produced, so any number on screen can show the punches and arithmetic behind it.

The countdown ticks on a one-second timer but only re-renders the hero label. Re-running
the analysis every second would be wasteful and would make the numbers flicker.
"""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from cerepulse.intelligence.day import DayAnalysis, DayState
from cerepulse.intelligence.insights import Insight
from cerepulse.models.attendance import PunchDirection
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import Palette
from cerepulse.ui.widgets import Banner, Card, InsightStrip, SectionTitle, SegmentBar, card_row


class TodayView(QWidget):
    """Hero, metric cards, day shape, punch timeline, and insights."""

    insight_action = Signal(object)
    refresh_requested = Signal()

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._analysis: DayAnalysis | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)

        self.banner = Banner()
        layout.addWidget(self.banner)

        layout.addWidget(self._build_hero())
        layout.addWidget(self._build_cards())

        layout.addWidget(SectionTitle("Your day"))
        self._segments = SegmentBar(palette)
        layout.addWidget(self._segments)
        self._legend = QLabel()
        self._legend.setObjectName("CardCaption")
        layout.addWidget(self._legend)

        self._insights = InsightStrip(palette)
        self._insights.action_triggered.connect(self.insight_action)
        layout.addWidget(self._insights)

        layout.addWidget(SectionTitle("Punches"))
        self._timeline = QVBoxLayout()
        self._timeline.setSpacing(4)
        timeline_host = QWidget()
        timeline_host.setLayout(self._timeline)
        layout.addWidget(timeline_host)

        layout.addStretch(1)

        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)

    # --- construction ---------------------------------------------------------------

    def _build_hero(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        top = QHBoxLayout()
        self._hero_label = QLabel("You can leave at")
        self._hero_label.setObjectName("HeroLabel")
        top.addWidget(self._hero_label)
        top.addStretch(1)

        self._copy = QPushButton("Copy summary")
        self._copy.clicked.connect(self._copy_summary)
        self._refresh = QPushButton("Refresh")
        self._refresh.clicked.connect(self.refresh_requested)
        top.addWidget(self._copy)
        top.addWidget(self._refresh)
        layout.addLayout(top)

        self._hero_value = QLabel(fmt.EMPTY)
        self._hero_value.setObjectName("HeroValue")
        layout.addWidget(self._hero_value)

        self._hero_caption = QLabel()
        self._hero_caption.setObjectName("HeroCaption")
        layout.addWidget(self._hero_caption)
        return host

    def _build_cards(self) -> QWidget:
        self.worked = Card("Worked", clickable=True)
        self.break_taken = Card("Break", clickable=True)
        self.remaining = Card("Remaining", clickable=True)
        self.extra = Card("Extra", clickable=True)

        self.worked.clicked.connect(lambda: self._explain("worked"))
        self.break_taken.clicked.connect(lambda: self._explain("break_taken"))
        self.remaining.clicked.connect(lambda: self._explain("expected_out_break_adjusted"))
        self.extra.clicked.connect(lambda: self._explain("worked"))

        return card_row(self.worked, self.break_taken, self.remaining, self.extra)

    # --- rendering ------------------------------------------------------------------

    def show_analysis(self, analysis: DayAnalysis, *, is_today: bool = True) -> None:
        """Render a day. ``is_today`` drives whether the live countdown runs."""
        self._analysis = analysis
        palette = self._palette

        self.worked.set_value(fmt.duration(analysis.worked), accent=palette.work)
        self.break_taken.set_value(fmt.duration(analysis.break_taken), accent=palette.rest)
        if analysis.state is DayState.EMPTY:
            self.break_taken.set_caption("")
        elif analysis.break_remaining:
            self.break_taken.set_caption(
                f"{fmt.duration(analysis.break_remaining)} of allowance left"
            )
        else:
            self.break_taken.set_caption("allowance used")

        if analysis.state is DayState.EMPTY:
            # Nothing was worked, so "target met" would be actively wrong.
            self.remaining.set_value(fmt.EMPTY)
            self.remaining.set_caption("nothing logged")
        elif analysis.work_remaining:
            self.remaining.set_value(fmt.duration(analysis.work_remaining), accent=palette.bad)
            self.remaining.set_caption("still to work")
        else:
            self.remaining.set_value("Done", accent=palette.good)
            self.remaining.set_caption("target met")

        self.extra.set_value(
            fmt.duration(analysis.extra_worked),
            accent=palette.good if analysis.extra_worked else None,
        )
        self.extra.set_caption("beyond target" if analysis.extra_worked else "")

        self._segments.set_segments(analysis.segments)
        self._legend.setText(self._legend_text(analysis))
        self._insights.set_insights(list(analysis.insights))
        self._render_timeline(analysis)
        self._render_hero(analysis, is_today=is_today)

        if is_today and analysis.state is DayState.INCOMPLETE:
            self._clock.start()
        else:
            self._clock.stop()

    def _render_hero(self, analysis: DayAnalysis, *, is_today: bool) -> None:
        if analysis.state is DayState.EMPTY:
            self._hero_label.setText("Nothing logged")
            self._hero_value.setText(fmt.EMPTY)
            self._hero_caption.setText(fmt.long_day_label(analysis.day))
            return

        if analysis.state is DayState.INCOMPLETE and is_today:
            self._hero_label.setText("You can leave at")
            self._hero_value.setText(fmt.clock(analysis.leave_at))
            self._tick()
            return

        self._hero_label.setText("Left at" if not is_today else "You left at")
        self._hero_value.setText(fmt.clock(analysis.last_out))
        self._hero_caption.setText(
            f"{fmt.long_day_label(analysis.day)} · in at {fmt.clock(analysis.first_in)} · "
            f"{fmt.duration_words(analysis.worked)} worked"
        )

    def _tick(self) -> None:
        """Update only the caption. Re-analyzing every second would make numbers flicker."""
        if self._analysis is None or self._analysis.leave_at is None:
            return
        remaining = fmt.countdown(self._analysis.leave_at, now=datetime.now())
        if remaining == "now":
            self._hero_caption.setText("Target met — you're free to go.")
        else:
            self._hero_caption.setText(
                f"{remaining} to go · in at {fmt.clock(self._analysis.first_in)}"
            )

    def _legend_text(self, analysis: DayAnalysis) -> str:
        parts = [
            f"Work {fmt.duration(analysis.worked)}",
            f"Break {fmt.duration(analysis.break_taken)}",
            f"Span {fmt.duration(analysis.gross_span)}",
        ]
        if any(segment.end_inferred for segment in analysis.segments):
            parts.append("hatched = inferred from a missing punch")
        return "  ·  ".join(parts)

    def _render_timeline(self, analysis: DayAnalysis) -> None:
        while self._timeline.count():
            item = self._timeline.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

        if not analysis.segments:
            empty = QLabel("No punches recorded for this day.")
            empty.setObjectName("CardCaption")
            self._timeline.addWidget(empty)
            return

        for index, segment in enumerate(analysis.segments, start=1):
            row = QLabel(
                f"{index}.  {fmt.clock(segment.start)}  →  {fmt.clock(segment.end)}"
                f"    ({fmt.duration(segment.duration)})"
                + ("   · end inferred" if segment.end_inferred else "")
            )
            row.setStyleSheet(
                f"color: {self._palette.text_muted}; font-variant-numeric: tabular-nums;"
            )
            self._timeline.addWidget(row)

    # --- interaction ----------------------------------------------------------------

    def _explain(self, metric: str) -> None:
        """Show the arithmetic behind a metric — the "why this number?" popover."""
        if self._analysis is None:
            return
        explanation = self._analysis.explanations.get(metric)
        if explanation is None:
            return

        body = [f"<b>{explanation.value}</b>", "", explanation.formula]
        if explanation.notes:
            body.append("")
            body.extend(f"• {note}" for note in explanation.notes)

        box = QMessageBox(self)
        box.setWindowTitle("Why this number?")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText("<br>".join(body))
        box.exec()

    def _copy_summary(self) -> None:
        """Put a shareable day summary on the clipboard, carried over from ninetofive."""
        from PySide6.QtWidgets import QApplication

        if self._analysis is None:
            return
        QApplication.clipboard().setText(summary_text(self._analysis))
        self._copy.setText("Copied")
        QTimer.singleShot(1500, lambda: self._copy.setText("Copy summary"))


def summary_text(analysis: DayAnalysis) -> str:
    """A plain-text day summary, suitable for pasting into Teams or an email."""
    lines = [
        f"{fmt.long_day_label(analysis.day)}",
        f"In {fmt.clock(analysis.first_in)} · Out {fmt.clock(analysis.last_out)}",
        f"Worked {fmt.duration_words(analysis.worked)} "
        f"· Break {fmt.duration_words(analysis.break_taken)}",
    ]
    if analysis.extra_worked:
        lines.append(f"Extra {fmt.duration_words(analysis.extra_worked)}")
    if analysis.work_remaining:
        lines.append(f"Short by {fmt.duration_words(analysis.work_remaining)}")
    for issue in analysis.issues:
        lines.append(f"Note: {issue.message}")
    return "\n".join(lines)


def punch_rows(analysis: DayAnalysis) -> list[tuple[str, str]]:
    """Flat punch list, used by the day-detail table on the Attendance screen."""
    rows: list[tuple[str, str]] = []
    for segment in analysis.segments:
        rows.append((fmt.clock(segment.start), PunchDirection.IN.value))
        rows.append((fmt.clock(segment.end), PunchDirection.OUT.value))
    return rows


def is_today(day: date) -> bool:
    return day == date.today()


__all__ = ["Insight", "TodayView", "is_today", "punch_rows", "summary_text"]
