"""The day's punches, and how long each stretch between them lasted.

Every punch is a line of its own — the time, then In or Out — and between each pair is the
stretch they enclose, drawn as a length of the day's spine with its duration beside it:
"3h 25m in the office" between an In and an Out, "35m break" between an Out and an In. The
user asked for exactly that: the punches alone left lunch to be worked out by subtracting
one time from another, and a single "Worked · 3h 25m" line per block hid the punches that
bounded it.

A break can still be marked as work — a trip to the other office is work, and nothing in the
data can tell — and a marked stretch offers the way back. A day still being worked ends on
"now", its last stretch counting up as "so far".
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, time

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QHBoxLayout, QInputDialog, QVBoxLayout, QWidget

from cerepulse.intelligence.segments import WorkedGap, WorkSegment
from cerepulse.models.values import Duration
from cerepulse.ui import formatting as fmt
from cerepulse.ui import kit
from cerepulse.ui.kit.text import measure
from cerepulse.ui.theme import Palette, Space, tone_colour
from cerepulse.ui.widgets import empty_layout

#: The spine's column: wide enough for a punch's dot, narrow enough to read as a line.
SPINE = 20
DOT = 9


def within(span: WorkedGap, segment: WorkSegment) -> bool:
    """Whether a reclaimed gap lies inside this block.

    Compared as full datetimes rather than clock times: on a shift running past midnight a
    23:50 gap has a smaller clock time than a 07:45 start, and the clock-time comparison
    quietly excluded exactly the days most likely to need it.
    """
    return segment.start <= span.start and span.end <= segment.end


def segment_detail(segment: WorkSegment, worked_spans: Sequence[WorkedGap]) -> str:
    """Why a stretch is not simply a measurement, when it is not.

    Every case is stated rather than hidden: an end the app inferred, an arrival taken from
    the portal's summary, and a stretch that only reads as continuous because the user said
    a gap inside it was work — named with whatever they said it was, because "a break you
    marked as work" three weeks later is a fact without a reason attached.
    """
    notes = []
    if segment.start_inferred:
        notes.append("the in punch is missing, so this start came from the summary")
    if segment.end_inferred:
        notes.append("the out punch is missing, so this end was inferred")
    inside = [span.note or "no note" for span in worked_spans if within(span, segment)]
    if inside:
        notes.append("includes work you marked: " + "; ".join(inside))
    return "; ".join(notes)


def _gap(previous: WorkSegment, following: WorkSegment) -> Duration:
    return Duration(max(0, int((following.start - previous.end).total_seconds() // 60)))


class DayJourney(QWidget):
    """Punches on a spine, with the length of every stretch between them."""

    #: (gap start, whether it is now work, what it was). The window owns the storage; this
    #: only reports the click.
    gap_flagged = Signal(object, bool, str)

    #: A gap at or under this is an ordinary break. Past it, the day has a hole in it and the
    #: stretch says so rather than calling four hours a break.
    LONG_BREAK = 90

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

    def set_segments(
        self,
        segments: Sequence[WorkSegment],
        *,
        worked_spans: Sequence[WorkedGap] = (),
        can_flag: bool = False,
        ongoing: bool = False,
    ) -> None:
        """Render the day.

        ``can_flag`` offers each break a "This was work" action — only where there is a day
        to store it against, because a button that silently does nothing is worse than none.
        ``ongoing`` means the last stretch is still being worked: it ends on "now".
        """
        empty_layout(self._layout)
        self.setVisible(bool(segments))
        if not segments:
            return

        stamp_width = measure("12:59 PM", role="body-strong")
        rows: list[_SpineRow] = []
        previous: WorkSegment | None = None
        for index, segment in enumerate(segments):
            last = index == len(segments) - 1
            if previous is not None:
                gap = _gap(previous, segment)
                if gap.minutes > 0:
                    away = gap.minutes > self.LONG_BREAK
                    stretch = _StretchRow(
                        self._palette,
                        duration=gap,
                        words="away" if away else "break",
                        tone="bad" if away else "rest",
                        dashed=True,
                    )
                    if can_flag:
                        stretch.offer_worked_flag(previous.end.time(), self.gap_flagged)
                    rows.append(stretch)
                rows.append(_PunchRow(self._palette, segment.start, "In", stamp_width))
            else:
                rows.append(_PunchRow(self._palette, segment.start, "In", stamp_width))

            inside = [span for span in worked_spans if within(span, segment)]
            still_going = ongoing and last
            # A stretch still being worked has no out punch because it has not ended, not
            # because one went missing — so it is not called inferred.
            measured = (
                segment
                if not still_going
                else WorkSegment(
                    start=segment.start, end=segment.end, start_inferred=segment.start_inferred
                )
            )
            stretch = _StretchRow(
                self._palette,
                duration=segment.duration,
                words="so far, still in" if still_going else "in the office",
                # Violet is the palette's "adjustment" accent, and a marked stretch is one.
                tone="adjust" if inside else "work",
                detail=segment_detail(measured, worked_spans),
                fading=still_going,
                inferred=measured.end_inferred or measured.start_inferred,
            )
            # Merging removed the gap's own stretch, so the way back has to live here.
            if can_flag and inside:
                stretch.offer_undo_flag(inside[0].start.time(), self.gap_flagged)
            rows.append(stretch)
            rows.append(
                _PunchRow(
                    self._palette,
                    segment.end,
                    "now" if still_going else "Out",
                    stamp_width,
                    hollow=not still_going,
                    quiet=still_going,
                )
            )
            previous = segment

        for row in rows:
            self._layout.addWidget(row)

    @property
    def stretches(self) -> list[_StretchRow]:
        """The stretches on screen, in order — work and breaks alike."""
        return self.findChildren(_StretchRow)


class _SpineRow(QWidget):
    """A row with the day's spine running down its left edge."""

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette


class _PunchRow(_SpineRow):
    """One punch: its time, a dot on the spine, and In, Out or now."""

    def __init__(
        self,
        palette: Palette,
        moment: datetime,
        label: str,
        stamp_width: int,
        *,
        hollow: bool = False,
        quiet: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(palette, parent)
        self._hollow, self._quiet = hollow, quiet
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(Space.SNUG)
        self.time = kit.Text(
            fmt.clock(moment), self, role="body-strong", tone="muted" if quiet else None
        )
        self.time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.time.setFixedWidth(stamp_width)
        layout.addWidget(self.time)
        layout.addSpacing(SPINE)
        self.label = kit.Text(label, self, tone="muted")
        layout.addWidget(self.label)
        layout.addStretch(1)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        x = self.time.geometry().right() + Space.SNUG + SPINE / 2
        middle = self.height() / 2
        colour = QColor(tone_colour(self._palette, "muted" if self._quiet else "work"))
        if self._quiet:
            painter.setPen(QPen(colour, 2))
            painter.drawLine(QPointF(x - DOT / 2, middle), QPointF(x + DOT / 2, middle))
        else:
            painter.setPen(QPen(colour, 2))
            painter.setBrush(QColor(self._palette.surface) if self._hollow else colour)
            painter.drawEllipse(QPointF(x, middle), DOT / 2 - 1, DOT / 2 - 1)
        painter.end()


class _StretchRow(_SpineRow):
    """The time between two punches: a length of spine, how long, and what it was."""

    def __init__(
        self,
        palette: Palette,
        *,
        duration: Duration,
        words: str,
        tone: str,
        detail: str = "",
        dashed: bool = False,
        fading: bool = False,
        inferred: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(palette, parent)
        self._tone, self._dashed, self._fading = tone, dashed, fading
        self.words = words
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, Space.TIGHT, 0, Space.TIGHT)
        layout.setSpacing(Space.SNUG)
        self._gutter = measure("12:59 PM", role="body-strong") + Space.SNUG + SPINE
        layout.addSpacing(self._gutter)

        text = QVBoxLayout()
        text.setSpacing(0)
        line = QHBoxLayout()
        line.setSpacing(6)
        self.length = kit.Text(
            str(duration), self, role="body-strong", tone="muted" if inferred else tone
        )
        line.addWidget(self.length)
        self.what = kit.Text(words, self, tone="muted")
        line.addWidget(self.what)
        line.addStretch(1)
        text.addLayout(line)
        if detail:
            text.addWidget(kit.Text(detail, self, role="caption", tone="muted", wrap=True))
        layout.addLayout(text, 1)
        self._actions = layout

    def offer_worked_flag(self, gap_start: time, signal: object) -> None:
        """Add "This was work" to a break, and ask what the work was.

        The punches call every Out-then-In a break, and so does the portal. Somebody who went
        downstairs to the other office was working, and nothing in the data can know that —
        so the row asks, once, rather than the app guessing from the duration. The note is
        asked for at the same moment, because the reason is the part that survives.
        """

        def flag() -> None:
            note, accepted = QInputDialog.getText(
                self, "Mark as work", "What was this? (optional)", text=""
            )
            if accepted:
                signal.emit(gap_start, True, note.strip())  # type: ignore[attr-defined]

        self._actions.addWidget(
            kit.Button(
                "This was work",
                self,
                variant="quiet",
                small=True,
                tooltip=(
                    "Count this gap as work instead of a break. Stored on this device only; "
                    "it does not change anything in SpineHR."
                ),
                on_click=flag,
            ),
            0,
            Qt.AlignmentFlag.AlignTop,
        )

    def offer_undo_flag(self, gap_start: time, signal: object) -> None:
        """Take a flag back. Anything the user can set, they must be able to unset."""
        self._actions.addWidget(
            kit.Button(
                "Not work",
                self,
                variant="quiet",
                small=True,
                tooltip="Count this as a break again.",
                on_click=lambda: signal.emit(gap_start, False, ""),  # type: ignore[attr-defined]
            ),
            0,
            Qt.AlignmentFlag.AlignTop,
        )

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        x = self._gutter - SPINE / 2
        colour = QColor(tone_colour(self._palette, self._tone))
        if self._fading:
            colour.setAlphaF(0.55)
        pen = QPen(colour, 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        if self._dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidthF(2.5)
        painter.setPen(pen)
        painter.drawLine(QPointF(x, 2), QPointF(x, self.height() - 2))
        painter.end()
