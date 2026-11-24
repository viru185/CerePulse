"""Today — the default screen, and the one that answers "when can I leave?".

Everything here is rendered from a :class:`DayAnalysis`; the view holds no business logic
of its own. Each metric card is clickable and opens the explanation the intelligence layer
produced, so any number on screen can show the punches and arithmetic behind it.

The screen reads top to bottom as instruction, then progress, then evidence: what to do
now, how far through the day is, and only then the four durations and the punch log that
justify both. It used to open with the durations, which meant the answer to "so what do I
do" had to be assembled by the reader from four numbers and a chart.

The countdown ticks on a one-second timer but only re-renders the hero label. Re-running
the analysis every second would be wasteful and would make the numbers flicker.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import QDate, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from cerepulse.intelligence.day import DayAnalysis, DayState
from cerepulse.intelligence.insights import Insight, InsightKind
from cerepulse.intelligence.next_action import NextActionKind, Presence, presence_of
from cerepulse.models.attendance import PunchDirection
from cerepulse.models.values import Duration
from cerepulse.services.commute import CommuteView
from cerepulse.ui import formatting as fmt
from cerepulse.ui import kit
from cerepulse.ui.journey import DayJourney
from cerepulse.ui.theme import Palette, Space
from cerepulse.ui.widgets import (
    Banner,
    CommuteCard,
    DayTimeline,
    InsightStrip,
    NextActionCard,
    Skeleton,
    card_row,
)

#: The date field, sized to its widest content rather than left to stretch across the
#: header. "25 Aug 2026" measures ~143px at the app's font; the stylesheet then adds 10px of
#: left padding and reserves 28px on the right for the popup arrow, plus the border. Set
#: below that sum and the year is what falls off the end — which is exactly what 132 did.
PICKER_WIDTH = 190

#: Insight kinds each next action has already said, so repeating them as a chip directly
#: below the instruction is padding. Kept as data rather than branching in the render path,
#: so adding a next action is one entry rather than an extra condition.
_COVERED_BY: dict[NextActionKind, set[InsightKind]] = {
    NextActionKind.CLOCK_IN: {InsightKind.NO_PUNCHES},
    NextActionKind.FILE_SWIPE_REQUEST: {InsightKind.SWIPE_NEEDED, InsightKind.NO_PUNCHES},
    NextActionKind.CHECK_PUNCHES: {InsightKind.MISSING_PUNCH},
    NextActionKind.RETURN_FROM_BREAK: {InsightKind.BREAK_HEADROOM},
    NextActionKind.FREE_TO_GO: {InsightKind.ON_TRACK},
}


class _HeroValue(QLabel):
    """The sixty-point time, clickable for the arithmetic behind it.

    The explanation for the finish time — both the flat and the break-adjusted figure, with
    a note saying why they differ — has existed since 0.5 and was reachable from nothing.
    """

    clicked = Signal()

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setProperty("role", "display")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Why this time?")

    def mousePressEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        self.clicked.emit()
        super().mousePressEvent(event)  # type: ignore[arg-type]


class TodayView(QWidget):
    """Instruction, progress, metric cards, timeline, and the punch log."""

    insight_action = Signal(object)  # Insight
    action_triggered = Signal(object)  # Action, from the next-action card
    refresh_requested = Signal()
    back_to_today = Signal()
    sync_day_requested = Signal(object)  # date
    date_selected = Signal(object)  # date
    commute_refresh_requested = Signal()
    commute_setup_requested = Signal()
    #: (date, gap start, whether it is work). The view knows which day is on screen; the
    #: window owns the storage.
    gap_flagged = Signal(object, object, bool, str)

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._analysis: DayAnalysis | None = None
        #: Whether the screen was reached by drilling in from another one, which is what
        #: keeps the context bar up even on today's own date.
        self._drilled_in = False
        self._status_label = ""
        self._status_colour: str | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Never sideways: anything that would extend past the window wraps instead.
        page = kit.ScrollArea(spacing=Space.GAP)
        outer.addWidget(page)
        layout = page.body

        self.banner = Banner()
        layout.addWidget(self.banner)

        layout.addWidget(self._build_hero())

        # Shown until the first analysis arrives, so a cold start reads as "loading"
        # rather than as a day with nothing logged in it.
        self._skeleton = Skeleton(palette, rows=4)
        layout.addWidget(self._skeleton)

        # Everything below the hero swaps out for the skeleton in one move, rather than
        # each card having to know how to look absent.
        self._content = kit.Box()
        body = QVBoxLayout(self._content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(Space.GAP)
        layout.addWidget(self._content)

        self._next_action = NextActionCard(palette)
        self._next_action.action_triggered.connect(self._on_next_action)
        body.addWidget(self._next_action)

        # Insights sit under the instruction, before the numbers. The screen is meant to say
        # what the day means; leading with four figures and burying the sentences that
        # interpret them below a chart makes it a dashboard instead.
        self._insights = InsightStrip(palette)
        self._insights.action_triggered.connect(self.insight_action)
        body.addWidget(self._insights)

        body.addWidget(self._build_cards())

        # Deliberately its own row rather than a third card beside Worked and Break. Those
        # two share one grammar — what has been *done*, then whether it is enough — and this
        # is a prediction about something that has not happened. Putting it in that row
        # would break the pairing that made them readable as a pair in the first place.
        self.commute = CommuteCard(palette)
        self.commute.refresh_requested.connect(self.commute_refresh_requested)
        self.commute.setup_requested.connect(self.commute_setup_requested)
        body.addWidget(self.commute)

        body.addSpacing(Space.SNUG)
        body.addWidget(kit.SectionHeader("Your day"))
        self._timeline = DayTimeline(palette)
        body.addWidget(self._timeline)
        self._legend = kit.Text(role="caption", tone="muted", wrap=True)
        body.addWidget(self._legend)

        # The punches, and how long each stretch between them lasted: in the office between
        # an In and an Out, a break between an Out and an In.
        body.addSpacing(Space.SNUG)
        self._punches_header = kit.SectionHeader("Punches")
        body.addWidget(self._punches_header)
        self._journey = DayJourney(palette)
        self._journey.gap_flagged.connect(self._on_gap_flagged)
        body.addWidget(self._journey)
        self._no_punches = kit.Text(
            "No punches recorded for this day.", role="caption", tone="muted"
        )
        body.addWidget(self._no_punches)

        layout.addStretch(1)

        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)

    # --- construction ---------------------------------------------------------------

    def _build_hero(self) -> QWidget:
        host = kit.Box()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Viewing a past day is a distinct mode and has to look like one, with an obvious
        # way out. The button names its actual destination: arriving here by double-clicking
        # a date in Attendance and being offered only "Back to today" is a dead end, because
        # the thing the user wants back is the month they were reading. It rides in the
        # title row, so opening a past day does not push the whole screen down a line.
        context = QHBoxLayout()
        context.setContentsMargins(0, 0, 0, 0)
        context.setSpacing(Space.SNUG)
        self._viewing = kit.Text(role="caption", tone="muted")
        self._back = kit.Button("Back to today", on_click=self.back_to_today.emit)
        # The punch log for a given day arrives one portal request at a time, so a date
        # opened from Attendance may have none yet. This asks for that day and no other.
        self._sync_day = kit.Button("Sync this day", on_click=self._request_day_sync)
        context.addWidget(self._viewing)
        context.addWidget(self._back)
        context.addWidget(self._sync_day)
        self._context_bar = kit.Box()
        self._context_bar.setLayout(context)
        self._context_bar.setVisible(False)

        top = QHBoxLayout()
        top.setSpacing(Space.SNUG)
        top.addWidget(kit.Text("Today", role="title"))
        top.addSpacing(Space.SNUG)
        top.addWidget(self._context_bar)
        top.addStretch(1)

        # Reading back through a week is the common case, and a calendar popup makes it
        # three clicks a day. These make it one.
        self._previous_day = kit.StepButton(
            "‹", "Previous day  ([)", on_click=lambda: self._step_day(-1)
        )
        top.addWidget(self._previous_day)

        # Any date, without going via Attendance first. The upper bound is today: the
        # portal has nothing for tomorrow, and offering it invites an empty screen that
        # looks like a failure.
        self._picker = kit.DateEdit()
        self._picker.setMaximumDate(QDate.currentDate())
        self._picker.setToolTip("Jump to another date")
        self._picker.setFixedWidth(PICKER_WIDTH)
        self._picker.dateChanged.connect(self._on_date_picked)
        top.addWidget(self._picker)

        self._next_day = kit.StepButton("›", "Next day  (])", on_click=lambda: self._step_day(1))
        # Reading back through a week is the common case; the brackets make it one key.
        QShortcut(QKeySequence("["), self).activated.connect(lambda: self._step_day(-1))
        QShortcut(QKeySequence("]"), self).activated.connect(lambda: self._step_day(1))
        top.addWidget(self._next_day)

        self._copy = kit.Button("Copy summary", variant="quiet", on_click=self._copy_summary)
        self._refresh = kit.Button("Refresh", on_click=self.refresh_requested.emit)
        top.addWidget(self._copy)
        top.addWidget(self._refresh)
        layout.addLayout(top)
        layout.addSpacing(Space.ROW)

        self._hero_label = kit.Text("You can leave at", tone="muted")
        layout.addWidget(self._hero_label)

        # The finish, and beside it how long until then: the two things the screen is
        # opened to learn, at the two largest sizes on it.
        value_row = QHBoxLayout()
        value_row.setSpacing(Space.SECTION)
        self._hero_value = _HeroValue(fmt.EMPTY)
        self._hero_value.setAccessibleName("The time you can leave, or left")
        self._hero_value.clicked.connect(lambda: self._explain("expected_out"))
        value_row.addWidget(self._hero_value, 0, Qt.AlignmentFlag.AlignBottom)

        self._countdown_box = kit.Box()
        countdown = QVBoxLayout(self._countdown_box)
        countdown.setContentsMargins(0, 0, 0, Space.ROW)
        countdown.setSpacing(0)
        self._countdown = kit.Text(role="title", tone="work")
        self._countdown_note = kit.Text(tone="muted")
        countdown.addWidget(self._countdown)
        countdown.addWidget(self._countdown_note)
        self._countdown_box.setVisible(False)
        value_row.addWidget(self._countdown_box, 0, Qt.AlignmentFlag.AlignBottom)

        self._presence = kit.Pill(tone="muted")
        self._presence.setVisible(False)
        value_row.addWidget(self._presence, 0, Qt.AlignmentFlag.AlignVCenter)
        value_row.addStretch(1)
        layout.addLayout(value_row)

        # The awaiting-today sentence runs to 135 characters; it wraps rather than pushing
        # the window wider than the screen.
        self._hero_caption = kit.Text(tone="muted", wrap=True)
        layout.addWidget(self._hero_caption)
        layout.addSpacing(Space.SNUG)

        self._progress = kit.Progress(self._palette)
        layout.addWidget(self._progress)
        return host

    def _build_cards(self) -> QWidget:
        # Worked, work remaining and overtime are three readings of one quantity — how the
        # day stands against its target — so they were three cards saying the same thing in
        # three ways, each given a quarter of the width to hold four characters. One card
        # carries the figure and its consequence; break is a separate question and keeps
        # its own.
        #
        # Both lead with what has been *done*. They used to disagree — Worked showed elapsed
        # and Break showed remaining, each relegating the other half to its caption — which
        # made two numbers sitting side by side impossible to read as a pair. Whether that
        # is enough is the caption's job, and it says so for both in the same words.
        # The shift card measures the span, which is what decides the day; net work is its
        # caption. See intelligence.policy for why the span rules.
        self.worked = kit.StatCard("Shift", clickable=True)
        self.break_taken = kit.StatCard("Break", clickable=True)

        self.worked.clicked.connect(lambda: self._explain("gross_span"))
        self.break_taken.clicked.connect(lambda: self._explain("break_taken"))

        return card_row(self.worked, self.break_taken)

    # --- rendering ------------------------------------------------------------------

    def show_commute(self, view: CommuteView, *, now: datetime | None = None) -> None:
        """Render the journey home. Delegated wholesale, so the view decides nothing."""
        self.commute.show_view(view, now=now)

    def set_loading(self, loading: bool) -> None:
        """Show placeholders instead of empty cards while the first day is being read."""
        if loading and self._analysis is None:
            self._skeleton.start()
            self._content.setVisible(False)
        else:
            self._skeleton.stop()
            self._content.setVisible(True)

    def _request_day_sync(self) -> None:
        if self._analysis is not None:
            self.sync_day_requested.emit(self._analysis.day)

    def _on_date_picked(self, chosen: QDate) -> None:
        picked = chosen.toPython()
        if self._analysis is not None and picked == self._analysis.day:
            return
        self.date_selected.emit(picked)

    def _step_day(self, delta: int) -> None:
        """Move one day. Stops at today, since the portal holds nothing for tomorrow."""
        current = self._analysis.day if self._analysis is not None else date.today()
        target = current + timedelta(days=delta)
        if target > date.today():
            return
        self.date_selected.emit(target)

    def _show_date(self, day: date) -> None:
        """Point the picker at the day on screen without asking to load it again."""
        self._picker.blockSignals(True)
        self._picker.setDate(QDate(day.year, day.month, day.day))
        self._picker.blockSignals(False)
        # Nothing to step forward into on today itself; a live button that does nothing
        # reads as broken.
        self._next_day.setEnabled(day < date.today())

    def _on_next_action(self, action: object) -> None:
        if action is not None:
            self.action_triggered.emit(action)

    def _on_gap_flagged(self, gap_start: object, worked: bool, note: str) -> None:
        """Attach the day to the click before passing it on."""
        if self._analysis is not None:
            self.gap_flagged.emit(self._analysis.day, gap_start, worked, note)

    def set_latest_date(self, today: date) -> None:
        """Raise the ceiling when the day rolls over.

        The maximum was set once at construction, so an app left running overnight refused
        to select the new day — the picker simply would not go there, with nothing said.
        """
        self._picker.blockSignals(True)
        self._picker.setMaximumDate(QDate(today.year, today.month, today.day))
        self._picker.blockSignals(False)
        self._next_day.setEnabled(self._shown_date() < today)

    def _shown_date(self) -> date:
        return self._analysis.day if self._analysis is not None else date.today()

    def set_back_target(self, origin: str | None) -> None:
        """Name where the exit leads. ``None`` means the only way out is back to today."""
        # Recorded as a flag rather than re-read from the button's own text later. Inferring
        # it from a leading arrow character made a glyph load-bearing, so changing the arrow
        # silently changed when the context bar appears.
        self._drilled_in = origin is not None
        self._back.setText(f"◀ Back to {origin}" if origin else "Back to today")

    def show_awaiting_today(self, day: date, last_synced: datetime | None = None) -> None:
        """State plainly that today has not arrived in the data yet.

        Preferable to rendering the most recent cached day in its place: that reads as a
        successful refresh showing wrong numbers, and it is how a missed clock-in hides.
        """
        self._analysis = None
        self._clock.stop()
        self._context_bar.setVisible(False)
        self._show_date(day)

        self._hero_label.setText("Waiting for today")
        self._hero_value.setText(fmt.EMPTY)
        self._countdown_box.setVisible(False)
        self._presence.setVisible(False)
        self._progress.set_progress(0.0, "")
        synced = f" Last synced {fmt.relative_time(last_synced)}." if last_synced else ""
        self._hero_caption.setText(
            f"{fmt.long_day_label(day)} is not in the portal's data yet.{synced} "
            f"It appears once your first punch of the day is recorded."
        )

        for card in (self.worked, self.break_taken):
            card.set_value(fmt.EMPTY)
            card.set_caption("")
        self._next_action.setVisible(False)
        self._timeline.set_day(())
        self._legend.setText("")
        self._insights.set_insights([])
        self._render_punches(None)

    def set_day_context(self, label: str = "", colour: str | None = None) -> None:
        """Say what a day with no punches actually was — leave, a holiday, outdoor duty.

        Set before :meth:`show_analysis`, because the analysis knows only about punches and
        a day with none of them is not necessarily a day with nothing in it.
        """
        self._status_label = label
        self._status_colour = colour

    def show_analysis(self, analysis: DayAnalysis, *, is_today: bool = True) -> None:
        """Render a day. ``is_today`` drives whether the live countdown runs."""
        self._analysis = analysis
        self._show_date(analysis.day)

        # The bar is also the way back when the user drilled in from another screen, so it
        # stays up for today's own date when there is somewhere to return to.
        self._context_bar.setVisible(not is_today or self._drilled_in)
        self._viewing.setText("" if is_today else f"Viewing {fmt.long_day_label(analysis.day)}")

        self._render_presence(analysis, is_today=is_today)
        self._render_progress(analysis, is_today=is_today)
        self._render_cards(analysis)

        self._next_action.set_action(analysis.next_action)
        self._insights.set_insights(self._strip_insights(analysis, is_today=is_today))

        self._timeline.set_day(
            analysis.segments,
            leave_at=analysis.leave_at if analysis.segments else None,
            # The muted marker is the break-counted time now: shown, never the target.
            flat_at=(
                analysis.expected_out_break_adjusted
                if analysis.segments and analysis.finish_times_differ
                else None
            ),
            now=datetime.now() if is_today and analysis.is_ongoing else None,
            status_label=self._status_label,
            status_colour=self._status_colour,
            worked_spans=analysis.worked_spans,
        )
        self._legend.setText(self._legend_text(analysis))
        self._render_punches(
            analysis, ongoing=is_today and analysis.is_ongoing and analysis.clocked_in
        )
        self._render_hero(analysis, is_today=is_today)

        if is_today and analysis.state is DayState.INCOMPLETE:
            self._clock.start()
        else:
            self._clock.stop()

    def _render_cards(self, analysis: DayAnalysis) -> None:
        empty = analysis.state is DayState.EMPTY

        # Two cards, one grammar: the value is what has been done, the caption names the
        # target and then says where that leaves you. Remaining and overtime were once
        # separate cards, but they are the same fact read from either side and only one of
        # them is ever non-zero — so they are the verdict, not a figure of their own.
        self.worked.set_value(fmt.duration(analysis.gross_span), tone="work")
        self.worked.set_caption(
            "nothing logged"
            if empty
            else _verdict(
                analysis.policy.day_target,
                left=analysis.span_remaining,
                over=analysis.extra_span,
                over_word="extra",
                noun="shift",
            )
            + f"  ·  {fmt.duration(analysis.worked)} net"
        )

        if empty:
            self.break_taken.set_value(fmt.EMPTY)
            self.break_taken.set_caption("")
        else:
            # Over the allowance the card used to read "None" — true of what was *left*,
            # silent about by how much it had been overrun, which is the number anyone
            # actually wants. Colour carries the same judgement as the words.
            over = analysis.break_over
            self.break_taken.set_value(
                fmt.duration(analysis.break_taken), tone="bad" if over else "rest"
            )
            self.break_taken.set_caption(
                _verdict(
                    analysis.policy.break_target,
                    left=analysis.break_remaining,
                    over=over,
                    over_word="over",
                    noun="allowance",
                )
            )

    def _render_presence(self, analysis: DayAnalysis, *, is_today: bool) -> None:
        """Say where you are now, which the four durations between them never did."""
        presence = presence_of(analysis, is_today=is_today)
        if presence is Presence.NOT_STARTED and not is_today:
            self._presence.setVisible(False)
            return

        tone = {
            Presence.NOT_STARTED: "muted",
            Presence.WORKING: "work",
            Presence.ON_BREAK: "rest",
            Presence.FINISHED: "good",
        }[presence]
        self._presence.set_state(presence.label, tone)
        self._presence.setVisible(True)

    def _render_progress(self, analysis: DayAnalysis, *, is_today: bool) -> None:
        if analysis.state is DayState.EMPTY:
            self._progress.set_progress(0.0, "")
            return

        percent = round(analysis.completion * 100)
        caption = f"{percent}% of the {analysis.policy.day_target} shift"

        # The counterfactual the hero cannot state: it says when you *can* leave, and this
        # says what walking out this minute would actually cost.
        if is_today and analysis.is_ongoing:
            if analysis.span_remaining:
                caption += f"  ·  leave now and the day is {analysis.span_remaining} short"
            else:
                caption += f"  ·  leave now and the day is {analysis.extra_span} over"
        self._progress.set_progress(analysis.completion, caption)

    def _render_hero(self, analysis: DayAnalysis, *, is_today: bool) -> None:
        live = analysis.state is DayState.INCOMPLETE and is_today
        self._countdown_box.setVisible(live)
        if analysis.state is DayState.EMPTY:
            self._hero_label.setText("Nothing logged")
            self._hero_value.setText(fmt.EMPTY)
            self._hero_caption.setText(fmt.long_day_label(analysis.day))
            return

        if live:
            self._hero_label.setText("You can leave at")
            self._hero_value.setText(fmt.clock(analysis.leave_at))
            self._tick()
            return

        self._hero_label.setText("Left at" if not is_today else "You left at")
        self._hero_value.setText(fmt.clock(analysis.last_out))
        self._hero_caption.setText(
            f"{fmt.long_day_label(analysis.day)} · in at {fmt.clock(analysis.first_in)} · "
            f"{fmt.duration_words(analysis.gross_span)} shift"
        )

    @staticmethod
    def _break_note(analysis: DayAnalysis) -> str:
        """The time that would make up an over-long break, muted beside the finish.

        The two used to be the other way round: the big time absorbed the extra break and
        this note gave the nine-hour time. Nine hours from arrival is the rule, so it leads,
        and the break-counted time is the aside.
        """
        if not analysis.finish_times_differ:
            return ""
        return f" · {fmt.clock(analysis.expected_out_break_adjusted)} if the extra break is counted"

    def _tick(self) -> None:
        """Update only the countdown. Re-analysing every second would make numbers flicker."""
        analysis = self._analysis
        if analysis is None or analysis.leave_at is None:
            return
        remaining = fmt.countdown(analysis.leave_at, now=datetime.now())
        if remaining == "now":
            self._countdown.setText("Shift complete")
            self._countdown_note.setText("You're free to go.")
        else:
            self._countdown.setText(f"{remaining} to go")
            self._countdown_note.setText(
                f"{analysis.policy.day_target} from your {fmt.clock(analysis.first_in)} "
                "arrival, breaks included"
            )
        self._hero_caption.setText(self._break_note(analysis).removeprefix(" · "))

    def _strip_insights(self, analysis: DayAnalysis, *, is_today: bool) -> list[Insight]:
        """Drop what the hero and the next-action card have already said.

        On a live day the hero *is* "you can leave at 6:14 PM", in sixty-point type, and the
        next-action card has just repeated the reasoning. Saying it a third time as the first
        chip makes the screen look like it is padding. The insights stay in the analysis,
        because the tray summary and the clipboard have no hero to lean on.
        """
        drop = {InsightKind.STILL_WORKING, InsightKind.ON_TRACK} if is_today else set()
        if analysis.next_action is not None:
            # Whatever the instruction is about is not also a finding to report below it.
            drop |= _COVERED_BY.get(analysis.next_action.kind, set())
        return [insight for insight in analysis.insights if insight.kind not in drop]

    def _legend_text(self, analysis: DayAnalysis) -> str:
        parts = [
            f"Work {fmt.duration(analysis.worked)}",
            f"Break {fmt.duration(analysis.break_taken)}",
            f"Span {fmt.duration(analysis.gross_span)}",
        ]
        if analysis.leave_at is not None and analysis.segments:
            parts.append(f"dashed = free at {fmt.clock(analysis.leave_at)}")
            if analysis.finish_times_differ:
                parts.append(
                    f"faint dashed = {fmt.clock(analysis.expected_out_break_adjusted)} "
                    "with the extra break counted"
                )
        if any(segment.end_inferred or segment.start_inferred for segment in analysis.segments):
            parts.append("hatched = inferred from a missing punch")
        if analysis.worked_spans:
            parts.append("violet = a break you marked as work")
        return "  ·  ".join(parts)

    def _render_punches(self, analysis: DayAnalysis | None, *, ongoing: bool = False) -> None:
        segments = analysis.segments if analysis is not None else ()
        self._journey.set_segments(
            segments,
            worked_spans=analysis.worked_spans if analysis is not None else (),
            # Only where there is a day to attach a flag to.
            can_flag=analysis is not None,
            ongoing=ongoing,
        )
        self._no_punches.setVisible(not segments)
        self._punches_header.set_meta(
            f"{fmt.duration(analysis.worked)} in · {fmt.duration(analysis.break_taken)} out"
            if analysis is not None and segments
            else ""
        )

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
        clipboard = QApplication.clipboard()
        if clipboard is None:  # offscreen and some remote sessions have none
            return
        clipboard.setText(summary_text(self._analysis))
        self._copy.setText("Copied")
        QTimer.singleShot(1500, lambda: self._copy.setText("Copy summary"))


def _verdict(
    target: Duration, *, left: Duration, over: Duration, over_word: str, noun: str = "target"
) -> str:
    """ "of 9h shift · 45m left", or "· 12m extra", or "· complete".

    One sentence shape for the shift and for the break, because the whole complaint about
    these two cards was that they described the same relationship in two different grammars.
    Only one of ``left`` and ``over`` is ever non-zero; both zero is the target landed exactly.
    """
    stem = f"of {target} {noun}  ·  "
    if left:
        return f"{stem}{fmt.duration(left)} left"
    if over:
        return f"{stem}{fmt.duration(over)} {over_word}"
    return f"{stem}{'complete' if noun == 'shift' else 'target met'}"


def summary_text(analysis: DayAnalysis) -> str:
    """A plain-text day summary, suitable for pasting into Teams or an email."""
    lines = [
        f"{fmt.long_day_label(analysis.day)}",
        f"In {fmt.clock(analysis.first_in)} · Out {fmt.clock(analysis.last_out)}",
        f"Shift {fmt.duration_words(analysis.gross_span)} "
        f"· Worked {fmt.duration_words(analysis.worked)} "
        f"· Break {fmt.duration_words(analysis.break_taken)}",
    ]
    if analysis.extra_span:
        lines.append(f"Extra {fmt.duration_words(analysis.extra_span)}")
    if analysis.span_remaining:
        lines.append(f"Short by {fmt.duration_words(analysis.span_remaining)}")
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
