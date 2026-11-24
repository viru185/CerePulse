"""Week — am I on track, what did the week look like, and what needs doing.

The week is the unit people actually think in: a short Tuesday matters less if Thursday
made it up, and only a weekly view shows that.

The screen answers three questions in that order, because that is the order they get asked:
a verdict in a sentence, the four figures behind it, then the days themselves.

The days are lanes on one clock — when each one started and ended, how long every stretch in
the office and every break lasted, and the day's total at the right — because that is what a
week screen is for. The redesign tried a quieter list and the user sent it back: the lanes
were the better overview. So they stay, drawn on the kit: a status pill, the total and its
verdict in the shared grammar, hour lines under one ruler, and today's finish as the stretch
still to go. Days nobody worked say what they were — leave, a holiday, outdoor duty — rather
than rendering as an absence.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from cerepulse.intelligence.attention import Attention
from cerepulse.intelligence.day import DayAnalysis
from cerepulse.intelligence.month import DayRollup, WeekAnalysis
from cerepulse.intelligence.segments import WorkSegment
from cerepulse.models.attendance import DayStatus
from cerepulse.models.values import Duration
from cerepulse.ui import formatting as fmt
from cerepulse.ui import kit
from cerepulse.ui.kit.text import measure
from cerepulse.ui.theme import Palette, Space, tone_colour
from cerepulse.ui.widgets import (
    Banner,
    DayTimeline,
    FlowLayout,
    HourAxis,
    card_row,
    empty_layout,
    shared_domain,
    timeline_inset,
)

#: Room either side of a lane for its hover wash. The ruler keeps the same, so it lines up.
LANE_PAD = Space.SNUG


class WeekView(QWidget):
    """A week judged, shaped, and made actionable."""

    week_changed = Signal(date)
    day_selected = Signal(object)  # date

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._week_start: date | None = None
        # The three columns every lane and the ruler share, measured in the fonts the sheet
        # applies, so "In progress" and "so far · 2h 15m to go" fit at every scale.
        self._columns = (
            max(
                measure("Wed 30", role="body-strong"),
                measure("In progress", "Complete", role="body-strong") + 2 * 10,
            ),
            max(
                measure("12h 45m", role="subtitle"),
                measure("so far · 12h 45m to go", "no hours recorded", role="caption"),
            ),
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page = kit.ScrollArea(spacing=Space.GAP)
        outer.addWidget(page)
        layout = page.body

        layout.addLayout(self._build_header())
        # The one screen with no error surface: a Week that failed to load was
        # indistinguishable from a Week with no data.
        self.banner = Banner()
        layout.addWidget(self.banner)

        # The verdict, in a sentence, before any figure that supports it.
        self._verdict = kit.Text(role="subtitle", wrap=True)
        layout.addWidget(self._verdict)

        self.total = kit.StatCard("Worked")
        self.target = kit.StatCard("Owed so far")
        self.delta = kit.StatCard("Difference")
        self.needed = kit.StatCard("Needed each day")
        layout.addWidget(card_row(self.total, self.target, self.delta, self.needed))

        progress = QVBoxLayout()
        progress.setSpacing(Space.TIGHT)
        self._progress = kit.Meter(palette)
        self._progress_caption = kit.Text(role="caption", tone="muted", wrap=True)
        progress.addWidget(self._progress)
        progress.addWidget(self._progress_caption)
        layout.addLayout(progress)

        layout.addWidget(self._build_days())

        self._attention_header = kit.SectionHeader("Needs doing")
        layout.addWidget(self._attention_header)
        self._attention = QVBoxLayout()
        self._attention.setContentsMargins(0, 0, 0, 0)
        self._attention.setSpacing(2)
        # Hidden while empty, or its spacing sits between the heading and the empty line.
        self._attention_host = kit.Box()
        self._attention_host.setLayout(self._attention)
        layout.addWidget(self._attention_host)
        self._nothing_needed = kit.Text(
            "Nothing outstanding this week — no short day without a request, and no missing punch.",
            tone="muted",
            wrap=True,
        )
        layout.addWidget(self._nothing_needed)

        layout.addStretch(1)

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(Space.SNUG)
        row.addWidget(kit.Text("Week", role="title"))
        row.addSpacing(Space.SNUG)
        self._previous = kit.StepButton("‹", "Previous week", on_click=lambda: self._step(-7))
        self._label = kit.Text(role="body-strong")
        self._next = kit.StepButton("›", "Next week", on_click=lambda: self._step(7))
        row.addWidget(self._previous)
        row.addWidget(self._label)
        row.addWidget(self._next)
        row.addStretch(1)
        self._this_week = kit.Button(
            "This week", on_click=lambda: self.week_changed.emit(_monday_of(date.today()))
        )
        row.addWidget(self._this_week)
        return row

    def _build_days(self) -> QWidget:
        panel = kit.Surface(variant="panel")
        body = QVBoxLayout(panel)
        body.setContentsMargins(Space.GAP, Space.ROW, Space.GAP, Space.ROW)
        body.setSpacing(0)

        self._days_header = kit.SectionHeader("Day by day", panel)
        body.addWidget(self._days_header)
        body.addSpacing(Space.TIGHT)

        self._days = QVBoxLayout()
        self._days.setContentsMargins(0, 0, 0, 0)
        self._days.setSpacing(0)
        days_host = kit.Box(panel)
        days_host.setLayout(self._days)
        body.addWidget(days_host)

        # One ruler under every lane, in the lanes' own middle column. Drawn once rather than
        # per lane so it is visibly a single scale — which is exactly the claim the stacked
        # lanes make — and the lanes carry faint lines at its ticks to be read against it.
        self._axis_row = kit.Box(panel)
        axis = _lane_layout(self._axis_row)
        left, right = self._columns
        axis.addWidget(_spacer(left, self._axis_row))
        self._axis = HourAxis(self._palette, self._axis_row)
        axis.addWidget(self._axis, 1)
        axis.addWidget(_spacer(right, self._axis_row))
        body.addWidget(self._axis_row)

        body.addSpacing(Space.SNUG)
        self._legend = kit.Box(panel)
        self._legend_layout = FlowLayout(self._legend, spacing=Space.GAP)
        body.addWidget(self._legend)
        return panel

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
        self._label.setText(_range_label(analysis.week_start, end, date.today()))
        # The portal has nothing for next week, and stepping into it shows an empty screen
        # that looks like a failure.
        self._next.setEnabled(analysis.week_start < _monday_of(date.today()))

        tone = self._render_verdict(analysis)
        self._render_cards(analysis)
        self._render_progress(analysis, tone)
        self._render_days(analysis, analyses or {})
        self._render_attention(analysis, attention or {})

    def _render_verdict(self, analysis: WeekAnalysis) -> str:
        """One sentence saying whether the week is fine, and what it would take if not.

        Returns the verdict's tone, which the progress bar wears too.
        """
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
            tone = "muted"
            text = f"{measurable}{reason}"
        elif analysis.delta.minutes >= 0:
            text = (
                f"On track — {fmt.duration(analysis.delta, signed=True)} across the "
                f"{settled} day(s) already worked."
            )
            tone = "good"
        elif required is None:
            # Behind with no day left to make it up in. "Work X per day over the remaining
            # 0" would be arithmetic nobody can act on.
            text = (
                f"The week finished {fmt.duration(-analysis.delta)} short. "
                f"Nothing left to make it up in."
            )
            tone = "bad"
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
            tone = "rest" if analysis.on_track else "bad"

        self._verdict.setText(text)
        self._verdict.set_tone(tone)
        return tone

    def _render_cards(self, analysis: WeekAnalysis) -> None:
        settled = analysis.completed_days

        self.total.set_value(fmt.duration(analysis.total_worked), tone="work")
        self.total.set_caption(f"across {settled} completed day(s)")

        self.target.set_value(fmt.duration(analysis.target))
        self.target.set_caption(f"{fmt.duration(analysis.full_target)} for the whole week")

        ahead = analysis.delta.minutes >= 0
        self.delta.set_value(
            fmt.duration(analysis.delta, signed=True), tone="good" if ahead else "bad"
        )
        self.delta.set_caption("ahead of target" if ahead else "behind target")

        required = analysis.required_daily_average
        if required is None:
            self.needed.set_value(fmt.EMPTY, tone="muted")
            self.needed.set_caption("nothing to make up" if ahead else "no days left this week")
        else:
            self.needed.set_value(
                fmt.duration(required), tone="rest" if analysis.on_track else "bad"
            )
            self.needed.set_caption(f"on each of {analysis.days_ahead} remaining day(s)")

    def _render_progress(self, analysis: WeekAnalysis, tone: str) -> None:
        """How far through the week, with today's hours as a fainter part of the same bar.

        Today counts here, deliberately: "how far through the week am I" is a different
        question from "am I behind", and for this one the morning is as real as Monday.
        """
        whole = analysis.full_target.minutes
        done = analysis.total_worked.minutes / whole if whole else 0.0
        today = analysis.in_progress.minutes / whole if whole else 0.0
        self._progress.set_parts((done, tone, 1.0), (today, "work", 0.45))

        worked = analysis.total_worked + analysis.in_progress
        so_far = (
            f", with {fmt.duration(analysis.in_progress)} of today so far"
            if analysis.in_progress
            else ""
        )
        self._progress_caption.setText(
            f"{analysis.progress * 100:.0f}% of the week — {fmt.duration(worked)} of "
            f"{fmt.duration(analysis.full_target)}{so_far}"
        )

    def _render_days(self, analysis: WeekAnalysis, analyses: dict[date, DayAnalysis]) -> None:
        """Every day as a lane, the ones with punches drawn on one shared clock.

        One scale for all seven, and one ruler under them, is the whole point: a bar that
        rescales per row says a late Tuesday and an early Monday are the same shape. The
        domain is computed from the days that have punches — and today's finish, so the
        stretch still to go is not cut off at the edge — and the days without them cost
        nothing and still line up beneath the same ruler.
        """
        empty_layout(self._days)

        spans: list[tuple[datetime, datetime]] = []
        for rollup in analysis.days:
            day = analyses.get(rollup.day)
            if day is None or not day.segments:
                continue
            end = day.segments[-1].end
            finish = _finish(rollup, day)
            spans.append((day.segments[0].start, max(end, finish) if finish else end))
        domain = shared_domain(spans) if spans else None
        if domain is not None:
            self._axis.set_domain(domain)
        self._axis_row.setVisible(domain is not None)

        for index, rollup in enumerate(analysis.days):
            row = _DayRow(
                rollup,
                self._palette,
                analysis=analyses.get(rollup.day),
                columns=self._columns,
                domain=domain,
                first=index == 0,
            )
            row.clicked.connect(lambda day=rollup.day: self.day_selected.emit(day))
            self._days.addWidget(row)

        # Days the grid does not have yet are still owed; say how many rather than drawing
        # lanes for them, since only the portal knows which of them are holidays.
        to_come = analysis.days_ahead - sum(1 for day in analysis.days if day.in_progress)
        self._days_header.set_meta(f"{to_come} more working day(s) to come" if to_come else "")
        self._render_legend(analysis, analyses, shown=domain is not None)

    def _render_legend(
        self, analysis: WeekAnalysis, analyses: dict[date, DayAnalysis], *, shown: bool
    ) -> None:
        """Name only the marks the lanes actually use, so the key never explains nothing."""
        empty_layout(self._legend_layout)
        self._legend.setVisible(shown)
        if not shown:
            return
        days = [analyses[r.day] for r in analysis.days if r.day in analyses]
        ongoing = any(r.in_progress for r in analysis.days)
        palette = self._palette
        marks = [
            ("In the office", kit.Swatch(palette, fill="work")),
            ("Break", kit.Swatch(palette, fill="rest", line=True)),
        ]
        if ongoing:
            marks += [
                ("Still going", kit.Swatch(palette, fill="work", hatched=True)),
                ("Until you can leave", kit.Swatch(palette, outline="work", dashed=True)),
            ]
        if any(day.worked_spans for day in days):
            marks.append(
                ("A break you marked as work", kit.Swatch(palette, fill="adjust", hatched=True))
            )
        if any(_repaired(day) for day in days):
            marks.append(
                (
                    "Hatched: a missing punch, filled in",
                    kit.Swatch(palette, fill="work", hatched=True),
                )
            )
        for words, swatch in marks:
            self._legend_layout.addWidget(kit.LegendItem(swatch, words, self._legend))

    def _render_attention(self, analysis: WeekAnalysis, attention: dict[date, Attention]) -> None:
        """The week's outstanding items, from the one place that decides what is outstanding.

        Filtered to this week rather than recomputed, so it cannot disagree with the
        Attendance row highlight or the heatmap ring.
        """
        empty_layout(self._attention)
        days = {rollup.day for rollup in analysis.days}
        found = [item for day, item in sorted(attention.items()) if day in days]

        self._nothing_needed.setVisible(not found)
        self._attention_host.setVisible(bool(found))
        self._attention_header.set_meta(f"{len(found)} to see to" if found else "")
        for item in found:
            row = kit.ListRow(
                self._palette,
                icon="warning",
                icon_tone="bad",
                title=fmt.day_label(item.day),
                detail=item.reason,
                tooltip="Open this day",
                clickable=True,
            )
            row.clicked.connect(lambda day=item.day: self.day_selected.emit(day))
            self._attention.addWidget(row)

    def _step(self, days: int) -> None:
        if self._week_start is not None:
            self.week_changed.emit(self._week_start + timedelta(days=days))


class _DayRow(QWidget):
    """One day as a lane: what it was, when it happened, and how it measured up.

    Painted rather than styled: a hairline between lanes and a wash on hover, both from the
    palette the theme walk hands it.
    """

    clicked = Signal()

    def __init__(
        self,
        rollup: DayRollup,
        palette: Palette,
        *,
        analysis: DayAnalysis | None,
        columns: tuple[int, int],
        domain: tuple[int, int] | None,
        first: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self._separated = not first
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        left_width, right_width = columns
        layout = _lane_layout(self)

        word, tone = _status(rollup)
        who = kit.Box(self)
        who.setFixedWidth(left_width)
        stack = QVBoxLayout(who)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(Space.TIGHT)
        self.day = kit.Text(f"{rollup.day:%a} {rollup.day.day}", who, role="body-strong")
        self.status = kit.Pill(word, who, tone=tone)
        stack.addWidget(self.day)
        stack.addWidget(self.status, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(who, 0, Qt.AlignmentFlag.AlignVCenter)

        middle = QVBoxLayout()
        middle.setSpacing(0)
        # When the punches are cached the lane shows *when* the day happened, not merely how
        # much of it there was. A proportion bar cannot tell a 7 AM start from a 1 PM one,
        # and on a week screen that is most of what there is to notice.
        self.timeline = DayTimeline(palette, self, lane=True)
        middle.addWidget(self.timeline)
        if analysis is not None and analysis.segments:
            self.timeline.set_day(
                analysis.segments,
                domain=domain,
                worked_spans=analysis.worked_spans,
                leave_at=_finish(rollup, analysis),
                ongoing=rollup.in_progress and analysis.clocked_in,
            )
            if rollup.note:
                # Under the bars, where the lane's own times start.
                note = kit.Text(rollup.note, self, role="caption", tone="muted", wrap=True)
                note.setContentsMargins(round(timeline_inset(note.font())), 0, 0, 0)
                middle.addWidget(note)
        else:
            # A day with nothing to draw says what it was in its band, the portal's remark
            # with it: "Holiday — Diwali", "Outdoor duty — Bengaluru".
            band = _band(rollup)
            self.timeline.set_day(
                (),
                status_label=f"{band} — {rollup.note}" if rollup.note and band else band,
                status_colour=tone_colour(palette, tone),
                domain=domain,
            )
        layout.addLayout(middle, 1)

        total, total_tone, verdict = _figures(rollup, analysis)
        figures = kit.Box(self)
        figures.setFixedWidth(right_width)
        column = QVBoxLayout(figures)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        self.total = kit.Text(total, figures, role="subtitle", tone=total_tone)
        self.total.setAlignment(right)
        if rollup.estimated:
            self.total.setToolTip("Estimated — this day's punch detail is not cached yet")
        self.verdict = kit.Text(verdict, figures, role="caption", tone="muted")
        self.verdict.setAlignment(right)
        self.verdict.setVisible(bool(verdict))
        column.addWidget(self.total)
        column.addWidget(self.verdict)
        layout.addWidget(figures, 0, Qt.AlignmentFlag.AlignVCenter)

        self.setToolTip(rollup.note or _band(rollup) or word)

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802 — Qt override
        self.update()
        super().enterEvent(event)  # type: ignore[arg-type]

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 — Qt override
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse():
            painter.setPen(Qt.PenStyle.NoPen)
            wash = QColor(self._palette.text)
            wash.setAlphaF(0.05)
            painter.setBrush(wash)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0, 1, 0, 0), 6, 6)
        if self._separated:
            painter.setPen(QPen(QColor(self._palette.border), 1))
            painter.drawLine(QPointF(0, 0.5), QPointF(self.width(), 0.5))
        painter.end()


# --- how a day reads ----------------------------------------------------------------------


def _status(rollup: DayRollup) -> tuple[str, str]:
    """The day's state in a word, and its tone — always a word, never colour alone."""
    if rollup.on_duty:
        return "Outdoor", "adjust"
    if rollup.in_progress:
        return "In progress", "work"
    if rollup.unmeasured:
        return "No data", "muted"
    if rollup.status is DayStatus.PRESENT:
        short = rollup.owed.minutes > 0 and rollup.worked < rollup.owed
        return ("Short", "bad") if short else ("Complete", "good")
    return {
        DayStatus.HALF_DAY: ("Half day", "rest"),
        DayStatus.ABSENT: ("Absent", "bad"),
        DayStatus.WEEKLY_OFF: ("Off", "muted"),
        DayStatus.HOLIDAY: ("Holiday", "rest"),
        DayStatus.LEAVE: ("Leave", "adjust"),
        DayStatus.ON_DUTY: ("Outdoor", "adjust"),
    }.get(rollup.status, (fmt.EMPTY, "muted"))


def _band(rollup: DayRollup) -> str:
    """What a lane with no punches to draw says instead."""
    if rollup.on_duty:
        return "Outdoor duty" if rollup.note else "Outdoor duty — worked off site, no swipes"
    if rollup.unmeasured:
        return "Marked worked, but the portal holds no punches or hours"
    if rollup.in_progress:
        return "Still being worked — no punches read yet"
    if rollup.status.counts_as_worked:
        return f"{fmt.duration(rollup.worked)} from the monthly summary — punches not read yet"
    return {
        DayStatus.WEEKLY_OFF: "Weekly off",
        DayStatus.HOLIDAY: "Holiday",
        DayStatus.LEAVE: "On leave",
        DayStatus.ABSENT: "Absent — nothing recorded",
    }.get(rollup.status, "")


def _figures(rollup: DayRollup, analysis: DayAnalysis | None) -> tuple[str, str, str]:
    """The day's total, its tone, and the verdict under it: over, short, or still going."""
    if rollup.on_duty:
        return fmt.EMPTY, "faint", "outdoor duty"
    if rollup.unmeasured:
        return fmt.EMPTY, "faint", "no hours recorded"
    if not rollup.status.counts_as_worked and not rollup.off_day_worked:
        return fmt.EMPTY, "faint", "nothing worked" if rollup.owed.minutes else "owes nothing"

    total = fmt.duration(rollup.worked)
    if rollup.in_progress:
        left = analysis.span_remaining if analysis is not None else Duration(0)
        return total, "work", f"so far · {fmt.duration(left)} to go" if left else "so far"
    if rollup.off_day_worked or rollup.owed.minutes <= 0:
        return total, "text", "on a day off"
    difference = rollup.worked - rollup.owed
    if difference.minutes > 0:
        verdict = f"{fmt.duration(difference)} over"
    elif difference.minutes < 0:
        verdict = f"{fmt.duration(-difference)} short"
    else:
        verdict = "exactly"
    return total, "good" if difference.minutes >= 0 else "bad", verdict


def _repaired(analysis: DayAnalysis) -> bool:
    """Whether a lane draws a block the app filled in. Today's open block is not one: it
    has no out punch because it has not ended, and the key calls that "Still going"."""
    segments = list(analysis.segments)
    if analysis.clocked_in and analysis.is_ongoing and segments:
        segments[-1] = WorkSegment(
            start=segments[-1].start,
            end=segments[-1].end,
            start_inferred=segments[-1].start_inferred,
        )
    return any(segment.end_inferred or segment.start_inferred for segment in segments)


def _finish(rollup: DayRollup, analysis: DayAnalysis) -> datetime | None:
    """When today can end, while some of it is still owed — the lane's dashed stretch."""
    if rollup.in_progress and analysis.span_remaining:
        return analysis.expected_out
    return None


def _range_label(start: date, end: date, today: date) -> str:
    """``Mon 23 – Sun 29 Nov``; the month on both ends when it changes, the year when it is
    not this one."""
    first = f"{start:%a} {start.day}" + ("" if start.month == end.month else f" {start:%b}")
    last = f"{end:%a} {end.day} {end:%b}" + ("" if end.year == today.year else f" {end.year}")
    return f"{first} – {last}"


def _lane_layout(host: QWidget) -> QHBoxLayout:
    """The row every lane and the ruler are laid out in, so their columns agree exactly."""
    layout = QHBoxLayout(host)
    layout.setContentsMargins(LANE_PAD, Space.TIGHT, LANE_PAD, Space.TIGHT)
    layout.setSpacing(Space.ROW)
    return layout


def _spacer(width: int, parent: QWidget) -> QWidget:
    """A real, empty widget of a fixed width. A layout spacer would not get the spacing a
    widget does, and the ruler would sit a few pixels off the lanes above it."""
    box = kit.Box(parent)
    box.setFixedWidth(width)
    return box


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())
