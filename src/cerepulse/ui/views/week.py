"""Week — am I on track, what did the week look like, and what needs doing.

The week is the unit people actually think in: a short Tuesday matters less if Thursday
made it up, and only a weekly view shows that.

The screen answers three questions in that order, because that is the order they get asked.
It previously answered none of them well: four cards, a row of 14-pixel slivers with one
number stranded at the end of each, and a terminal stretch that left the bottom third of the
window empty. A day was a label, a bar and an hours figure — less than the Attendance table
already showed on the same data.

Every day is now a row worth reading: what it was, how the worked time compares with the
target, and why it is unusual when it is. Days nobody worked say what they were — leave, a
holiday, outdoor duty — rather than rendering as an absence.
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from cerepulse.intelligence.attention import Attention
from cerepulse.intelligence.day import DayAnalysis
from cerepulse.intelligence.month import DayRollup, WeekAnalysis
from cerepulse.intelligence.segments import WorkSegment
from cerepulse.models.attendance import DayStatus
from cerepulse.models.values import Duration
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import Palette, Space
from cerepulse.ui.widgets import (
    Card,
    DayTimeline,
    EmptyState,
    HourAxis,
    SectionTitle,
    StatusChip,
    TargetBar,
    card_row,
    shared_domain,
)


class WeekView(QWidget):
    """A week judged, shaped, and made actionable."""

    week_changed = Signal(date)
    day_selected = Signal(object)  # date

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._week_start: date | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(Space.SECTION, 20, Space.SECTION, Space.SECTION)
        layout.setSpacing(Space.GAP)

        layout.addLayout(self._build_header())

        # The verdict, in a sentence, before any figure that supports it.
        self._verdict = QLabel()
        self._verdict.setWordWrap(True)
        self._verdict.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(self._verdict)

        self.total = Card("Worked")
        self.target = Card("Owed so far")
        self.delta = Card("Difference")
        self.needed = Card("Needed daily")
        layout.addWidget(card_row(self.total, self.target, self.delta, self.needed))

        self._progress = TargetBar(palette)
        layout.addWidget(self._progress)

        layout.addWidget(SectionTitle("Day by day"))
        self._days = QVBoxLayout()
        self._days.setSpacing(Space.TIGHT)
        days_host = QWidget()
        days_host.setLayout(self._days)
        layout.addWidget(days_host)

        # One ruler under all seven rows. Drawn here rather than inside each row so it is
        # visibly a single scale — which is exactly the claim the stacked timelines make.
        # Indented to line up with where the timelines start, not with the row's left edge.
        axis_row = QHBoxLayout()
        axis_row.setContentsMargins(62 + 86 + Space.ROW * 2, 0, 76 + Space.ROW, 0)
        self._axis = HourAxis(palette)
        axis_row.addWidget(self._axis)
        layout.addLayout(axis_row)

        layout.addWidget(SectionTitle("Needs doing"))
        self._attention = QVBoxLayout()
        self._attention.setSpacing(Space.TIGHT)
        attention_host = QWidget()
        attention_host.setLayout(self._attention)
        layout.addWidget(attention_host)
        self._nothing_needed = EmptyState(
            "Nothing outstanding this week.",
            "No short days without a request, and no missing punches.",
        )
        layout.addWidget(self._nothing_needed)

        layout.addStretch(1)

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(Space.SNUG)

        previous = QPushButton("◀ Previous")
        previous.clicked.connect(lambda: self._step(-7))
        self._label = SectionTitle("")
        following = QPushButton("Next ▶")
        following.clicked.connect(lambda: self._step(7))
        this_week = QPushButton("This week")
        this_week.clicked.connect(lambda: self.week_changed.emit(_monday_of(date.today())))

        row.addWidget(previous)
        row.addWidget(self._label, 1)
        row.addWidget(this_week)
        row.addWidget(following)
        return row

    # --- rendering ------------------------------------------------------------------

    def show_week(
        self,
        analysis: WeekAnalysis,
        target_per_day: Duration,
        *,
        attention: dict[date, Attention] | None = None,
        analyses: dict[date, DayAnalysis] | None = None,
    ) -> None:
        self._week_start = analysis.week_start
        end = analysis.week_start + timedelta(days=6)
        self._label.setText(
            f"{analysis.week_start.strftime('%d %b').lstrip('0')} – "
            f"{end.strftime('%d %b %Y').lstrip('0')}"
        )

        self._render_verdict(analysis)
        self._render_cards(analysis)
        self._render_progress(analysis)
        self._render_days(analysis, target_per_day, analyses or {})
        self._render_attention(analysis, attention or {})

    def _render_verdict(self, analysis: WeekAnalysis) -> None:
        """One sentence saying whether the week is fine, and what it would take if not."""
        palette = self._palette
        required = analysis.required_daily_average
        # The analysis's own count, not a re-derivation. Subtracting days_ahead from
        # working_days hit zero with today in progress, printing "0 day(s) already
        # worked" beside a sixteen-hour total summed from the very days it denied.
        settled = analysis.completed_days

        if analysis.target.minutes <= 0:
            # The target covers completed working days only, so a zero target means there
            # is not yet anything to judge. "On track, 0m across 0 days" is a verdict about
            # nothing — the same mistake as presenting a month's target as owed on day two.
            measurable = "Nothing measurable this week yet"
            reason = (
                " — the days so far were outdoor duty, leave or time off."
                if any(day.on_duty or day.status.is_off for day in analysis.days)
                else "."
            )
            self._verdict.setText(f"{measurable}{reason}")
            self._verdict.setStyleSheet(
                f"font-size: 15px; font-weight: 600; color: {palette.text_muted};"
            )
            return

        if analysis.delta.minutes >= 0:
            text = (
                f"On track — {fmt.duration(analysis.delta, signed=True)} across the "
                f"{settled} day(s) already worked."
            )
            colour = palette.good
        elif required is None:
            # Behind with no day left to make it up in. "Work X per day over the remaining
            # 0" would be arithmetic nobody can act on.
            text = (
                f"The week finished {fmt.duration(-analysis.delta)} short. "
                f"Nothing left to make it up in."
            )
            colour = palette.bad
        else:
            reachable = (
                ""
                if analysis.on_track
                else "  That is more than a full shift, so it is not realistic."
            )
            text = (
                f"{fmt.duration(-analysis.delta)} behind. Work {fmt.duration(required)} on "
                f"each of the remaining {analysis.days_ahead} day(s) to finish "
                f"level.{reachable}"
            )
            colour = palette.rest if analysis.on_track else palette.bad

        self._verdict.setText(text)
        self._verdict.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {colour};")

    def _render_cards(self, analysis: WeekAnalysis) -> None:
        palette = self._palette
        # The analysis's own count, not a re-derivation. Subtracting days_ahead from
        # working_days hit zero with today in progress, printing "0 day(s) already
        # worked" beside a sixteen-hour total summed from the very days it denied.
        settled = analysis.completed_days

        self.total.set_value(fmt.duration(analysis.total_worked), accent=palette.work)
        self.total.set_caption(f"across {settled} completed day(s)")

        self.target.set_value(fmt.duration(analysis.target))
        self.target.set_caption(f"{fmt.duration(analysis.full_target)} for the whole week")

        ahead = analysis.delta.minutes >= 0
        self.delta.set_value(
            fmt.duration(analysis.delta, signed=True),
            accent=palette.good if ahead else palette.bad,
        )
        self.delta.set_caption("ahead of target" if ahead else "behind target")

        required = analysis.required_daily_average
        if required is None:
            self.needed.set_value(fmt.EMPTY, accent=palette.text_muted)
            self.needed.set_caption("nothing to make up" if ahead else "no days left this week")
        else:
            self.needed.set_value(
                fmt.duration(required),
                accent=palette.rest if analysis.on_track else palette.bad,
            )
            self.needed.set_caption(f"on each of {analysis.days_ahead} remaining day(s)")

    def _render_progress(self, analysis: WeekAnalysis) -> None:
        share = analysis.progress
        worked = analysis.total_worked + analysis.in_progress
        today = (
            f", including {fmt.duration(analysis.in_progress)} logged today"
            if analysis.in_progress
            else ""
        )
        self._progress.set_progress(
            share,
            f"{share * 100:.0f}% of the week — {fmt.duration(worked)} of "
            f"{fmt.duration(analysis.full_target)}{today}",
        )

    def _render_days(
        self,
        analysis: WeekAnalysis,
        target_per_day: Duration,
        analyses: dict[date, DayAnalysis],
    ) -> None:
        """Every day as a row, the ones with punches carrying a timeline on a shared scale.

        One scale for all seven, and one axis under them, is the whole point: a bar that
        rescales per row says a late Tuesday and an early Monday are the same shape. The
        domain is computed from the days that have punches, so the days that do not — leave,
        a holiday, outdoor duty — cost nothing and still line up beneath the same ruler.
        """
        _clear(self._days)

        spans = [
            (day.segments[0].start, day.segments[-1].end)
            for rollup in analysis.days
            if (day := analyses.get(rollup.day)) is not None and day.segments
        ]
        domain = shared_domain(spans)
        self._axis.set_domain(domain)
        self._axis.setVisible(bool(spans))

        for rollup in analysis.days:
            detail = analyses.get(rollup.day)
            row = _DayRow(
                rollup,
                target_per_day,
                self._palette,
                segments=detail.segments if detail else (),
                domain=domain,
            )
            row.clicked.connect(lambda day=rollup.day: self.day_selected.emit(day))
            self._days.addWidget(row)

    def _render_attention(self, analysis: WeekAnalysis, attention: dict[date, Attention]) -> None:
        """The week's outstanding items, from the one place that decides what is outstanding.

        Filtered to this week rather than recomputed, so it cannot disagree with the
        Attendance row highlight or the heatmap ring.
        """
        _clear(self._attention)
        days = {rollup.day for rollup in analysis.days}
        found = [item for day, item in sorted(attention.items()) if day in days]

        self._nothing_needed.setVisible(not found)
        for item in found:
            row = _AttentionRow(item, self._palette)
            row.clicked.connect(lambda day=item.day: self.day_selected.emit(day))
            self._attention.addWidget(row)

    def _step(self, days: int) -> None:
        if self._week_start is not None:
            self.week_changed.emit(self._week_start + timedelta(days=days))


class _DayRow(QWidget):
    """One day: what it was, how much of the target it made, and why it is unusual."""

    clicked = Signal()

    BAR_HEIGHT = 10

    def __init__(
        self,
        rollup: DayRollup,
        target: Duration,
        palette: Palette,
        *,
        segments: tuple[WorkSegment, ...] = (),
        domain: tuple[int, int] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, Space.TIGHT // 2, 0, Space.TIGHT // 2)
        layout.setSpacing(Space.ROW)

        label = QLabel(rollup.day.strftime("%a %d").replace(" 0", " "))
        label.setFixedWidth(62)
        label.setStyleSheet(f"color: {palette.text_muted};")
        layout.addWidget(label)

        chip = StatusChip(_status_word(rollup), _status_colour(rollup, palette))
        chip.setFixedWidth(86)
        layout.addWidget(chip)

        # The bar and its note share the stretch, so the row spends the width on something
        # rather than leaving a 700-pixel sliver with one number at the end of it.
        middle = QVBoxLayout()
        middle.setSpacing(2)
        if segments:
            # When the punches are cached the row shows *when* the day happened, not merely
            # how much of it there was. A proportion bar cannot tell a 7 AM start from a
            # 1 PM one, and on a week screen that is most of what there is to notice.
            timeline = DayTimeline(palette)
            timeline.set_day(segments, domain=domain)
            middle.addWidget(timeline)
        else:
            bar = QWidget()
            bar.setFixedHeight(self.BAR_HEIGHT)
            bar.setStyleSheet(_bar_style(rollup, target, palette))
            middle.addWidget(bar)

        note = QLabel(_day_note(rollup))
        note.setObjectName("CardCaption")
        note.setWordWrap(True)
        middle.addWidget(note)
        layout.addLayout(middle, 1)

        hours = QLabel(_hours_text(rollup))
        hours.setFixedWidth(76)
        hours.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if rollup.estimated:
            hours.setToolTip("Estimated — this day's punch detail is not cached yet")
        layout.addWidget(hours)

        self.setToolTip(_day_note(rollup) or _status_word(rollup))

    def mouseReleaseEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        self.clicked.emit()
        super().mouseReleaseEvent(event)  # type: ignore[arg-type]


class _AttentionRow(QWidget):
    """One outstanding item, and the day it belongs to."""

    clicked = Signal()

    def __init__(self, item: Attention, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, Space.TIGHT // 2, 0, Space.TIGHT // 2)
        layout.setSpacing(Space.ROW)

        when = QLabel(item.day.strftime("%a %d").replace(" 0", " "))
        when.setFixedWidth(62)
        when.setStyleSheet(f"color: {palette.bad};")
        layout.addWidget(when)

        reason = QLabel(item.reason)
        reason.setWordWrap(True)
        layout.addWidget(reason, 1)

    def mouseReleaseEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        self.clicked.emit()
        super().mouseReleaseEvent(event)  # type: ignore[arg-type]


# --- how a day reads ----------------------------------------------------------------------


def _status_word(rollup: DayRollup) -> str:
    if rollup.on_duty:
        return "Outdoor"
    if rollup.in_progress:
        return "Today"
    if rollup.unmeasured:
        return "No data"
    return {
        DayStatus.PRESENT: "Worked",
        DayStatus.HALF_DAY: "Half day",
        DayStatus.ABSENT: "Absent",
        DayStatus.WEEKLY_OFF: "Off",
        DayStatus.HOLIDAY: "Holiday",
        DayStatus.LEAVE: "Leave",
        DayStatus.ON_DUTY: "Outdoor",
    }.get(rollup.status, fmt.EMPTY)


def _status_colour(rollup: DayRollup, palette: Palette) -> str:
    if rollup.on_duty:
        return palette.adjust
    if rollup.in_progress:
        return palette.work
    return {
        DayStatus.PRESENT: palette.good,
        DayStatus.HALF_DAY: palette.rest,
        DayStatus.ABSENT: palette.bad,
        DayStatus.LEAVE: palette.adjust,
        DayStatus.HOLIDAY: palette.rest,
    }.get(rollup.status, palette.text_muted)


def _day_note(rollup: DayRollup) -> str:
    """Why the day is what it is — the portal's remark when there is one, the state if not."""
    if rollup.note:
        return rollup.note
    if rollup.on_duty:
        return "Worked off site; no swipes to measure."
    if rollup.unmeasured:
        return "Marked worked, but the portal holds no punches or hours."
    if rollup.in_progress:
        return "Still being worked."
    if rollup.estimated:
        return "Estimated from the monthly summary."
    return ""


def _hours_text(rollup: DayRollup) -> str:
    if rollup.on_duty or not rollup.status.counts_as_worked:
        return fmt.EMPTY
    return fmt.duration(rollup.worked)


def _bar_style(rollup: DayRollup, target: Duration, palette: Palette) -> str:
    """A hard-edged fill showing the fraction of target worked.

    A gradient needs its two stops to straddle the boundary. Letting them land on the same
    offset — which happens at 0% and at 100% — makes Qt interpolate across the whole bar,
    so a full day renders as a wash rather than a solid fill.
    """
    radius = "border-radius: 5px;"
    if rollup.on_duty:
        # No hours to draw, but the day was worked. A flat band says something happened
        # here without claiming a quantity that does not exist.
        return f"background: {_tinted(palette.adjust)}; {radius}"
    if not rollup.status.counts_as_worked:
        return f"background: {palette.border}; {radius}"

    filled = fmt.percent(rollup.worked, target) if target.minutes else 0.0
    colour = palette.work if rollup.in_progress or rollup.worked < target else palette.good
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


def _tinted(colour: str) -> str:
    from PySide6.QtGui import QColor

    value = QColor(colour)
    return f"rgba({value.red()}, {value.green()}, {value.blue()}, 110)"


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _clear(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget() if item is not None else None
        if widget is not None:
            widget.deleteLater()
