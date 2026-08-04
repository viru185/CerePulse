"""The day timeline and the punch journey.

Both were redesigned around one complaint each. The timeline drew a break as the track
showing through, so a ten-minute coffee and a ninety-minute lunch were indistinguishable
without hovering. The punch list had a row per work *segment*, which meant the gaps between
them — the thing anyone reads a punch log for — were not rows at all.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import NamedTuple

from PySide6.QtWidgets import QApplication

from cerepulse.intelligence.day import analyze_day
from cerepulse.intelligence.segments import WorkSegment
from cerepulse.models.attendance import Punch, PunchDirection
from cerepulse.models.values import Duration
from cerepulse.ui.theme import DARK
from cerepulse.ui.widgets import DayJourney, DayTimeline, _gap_between

DAY = date(2026, 7, 24)


def punches(*pairs: tuple[str, str]) -> list[Punch]:
    out = []
    for clock, direction in pairs:
        hour, minute = (int(part) for part in clock.split(":"))
        out.append(Punch(at=time(hour, minute), direction=PunchDirection.parse(direction)))
    return out


BUSY = (
    ("09:21", "in"),
    ("11:43", "out"),
    ("11:51", "in"),
    ("12:55", "out"),
    ("13:30", "in"),
    ("16:54", "out"),
    ("16:56", "in"),
    ("18:47", "out"),
)


def segment(start: str, end: str, *, inferred: bool = False) -> WorkSegment:
    def at(clock: str) -> datetime:
        hour, minute = (int(part) for part in clock.split(":"))
        return datetime(2026, 7, 24, hour, minute)

    return WorkSegment(at(start), at(end), end_inferred=inferred)


# --- breaks are a quantity ----------------------------------------------------------------


def test_a_gap_is_measured_not_merely_drawn() -> None:
    gap = _gap_between(segment("09:21", "11:43"), segment("11:51", "12:55"))
    assert gap == Duration(8)


def test_touching_segments_have_no_gap() -> None:
    assert _gap_between(segment("09:00", "13:00"), segment("13:00", "18:00")).minutes == 0


def test_the_tooltip_names_every_break(qapp: QApplication) -> None:
    """It is the text form of the same day, and the one a screen reader gets."""
    analysis = analyze_day(punches(*BUSY), day=DAY)
    timeline = DayTimeline(DARK)
    timeline.set_day(analysis.segments)

    tip = timeline.toolTip()
    assert tip.count("break") == 3, tip
    assert "worked" in tip


# --- labels that do not sit on each other ---------------------------------------------------
#
# In and Out on separate rows keeps *neighbours* apart, because punches alternate by nature.
# It does nothing for two punches two apart in the sequence, which share a row — and at
# roughly three pixels per minute, two segments starting a few minutes apart wrote two times
# in the same place.


class Label(NamedTuple):
    """One string the timeline painted, and the box it went in."""

    left: float
    right: float
    top: float
    bottom: float
    text: str

    def overlaps(self, other: Label) -> bool:
        """Boxes that intersect in both axes. Two labels on different rows may share an x —
        that is the whole point of putting In above the bar and Out below it."""
        return (
            self.left < other.right
            and other.left < self.right
            and self.top < other.bottom
            and other.top < self.bottom
        )


def _drawn(timeline: DayTimeline) -> list[Label]:
    """Every string the timeline paints, with the rectangle it was given."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    placed: list[Label] = []
    real = QPainter.drawText

    def record(self: QPainter, target: object, *args: object) -> None:
        if isinstance(target, QRectF) and args and isinstance(args[-1], str) and args[-1]:
            placed.append(
                Label(target.left(), target.right(), target.top(), target.bottom(), args[-1])
            )
        real(self, target, *args)  # type: ignore[arg-type]

    image = QImage(870, DayTimeline.HEIGHT_FULL, QImage.Format.Format_ARGB32)
    QPainter.drawText = record  # type: ignore[method-assign]
    try:
        timeline.render(image)
    finally:
        QPainter.drawText = real  # type: ignore[method-assign]
    return placed


def _times(timeline: DayTimeline) -> list[Label]:
    return [label for label in _drawn(timeline) if ":" in label.text]


def _collisions(labels: list[Label]) -> list[tuple[str, str]]:
    return [
        (a.text, b.text)
        for index, a in enumerate(labels)
        for b in labels[index + 1 :]
        if a.overlaps(b)
    ]


def test_two_punches_minutes_apart_do_not_overlap(qapp: QApplication) -> None:
    """The report. A two-minute segment puts two In times fourteen pixels apart, against
    labels around thirty-five pixels wide."""
    timeline = DayTimeline(DARK)
    timeline.resize(870, DayTimeline.HEIGHT_FULL)
    timeline.set_day((segment("09:00", "09:02"), segment("09:05", "18:00")))

    assert not _collisions(_times(timeline))


def test_a_busy_day_still_shows_its_times(qapp: QApplication) -> None:
    """The rule this replaces dropped any label within 22 px of another, so a ten-punch day
    lost exactly the times it most needed to show."""
    timeline = DayTimeline(DARK)
    timeline.resize(870, DayTimeline.HEIGHT_FULL)
    analysis = analyze_day(punches(*BUSY), day=DAY)
    timeline.set_day(analysis.segments)

    assert len(_times(timeline)) == 8, "four segments, eight punches, eight times"
    assert not _collisions(_times(timeline))


def test_a_label_that_cannot_fit_is_dropped_rather_than_misplaced(qapp: QApplication) -> None:
    """Sliding far enough to clear a neighbour would park the time above the wrong dot, which
    reads as a wrong answer rather than a missing one."""
    timeline = DayTimeline(DARK)
    timeline.resize(870, DayTimeline.HEIGHT_FULL)
    timeline.set_day(
        (
            segment("09:00", "09:01"),
            segment("09:02", "09:03"),
            segment("09:04", "09:05"),
            segment("09:06", "18:00"),
        )
    )

    shown = _times(timeline)
    assert len(shown) < 8, "not everything can fit inside six minutes"
    assert not _collisions(shown)


def test_the_break_duration_does_not_write_through_the_in_time(qapp: QApplication) -> None:
    """Break labels sat at `bar.top() - 15` against In times at `- 17` — the same band, so a
    gap just wide enough to earn a duration wrote it through the time at its right edge."""
    timeline = DayTimeline(DARK)
    timeline.resize(870, DayTimeline.HEIGHT_FULL)
    timeline.set_day((segment("09:00", "12:30"), segment("13:10", "18:00")))

    drawn = _drawn(timeline)
    assert any(label.text == "40m" for label in drawn), "a forty-minute gap is worth labelling"
    assert not _collisions(drawn), "nothing on this widget may overlap anything else"


# --- modes ---------------------------------------------------------------------------------


def test_it_starts_in_the_full_mode(qapp: QApplication) -> None:
    timeline = DayTimeline(DARK)
    timeline.resize(900, 10)
    assert timeline.sizeHint().height() == DayTimeline.HEIGHT_FULL


def test_a_narrow_timeline_drops_to_the_compact_mode(qapp: QApplication) -> None:
    """The same widget is drawn full-width on Today and in a side panel on Attendance."""
    timeline = DayTimeline(DARK)
    timeline.resize(300, DayTimeline.HEIGHT_FULL)

    assert timeline._compact
    assert timeline.sizeHint().height() == DayTimeline.HEIGHT_COMPACT


def test_it_returns_to_full_when_there_is_room_again(qapp: QApplication) -> None:
    timeline = DayTimeline(DARK)
    timeline.resize(300, DayTimeline.HEIGHT_FULL)
    timeline.resize(900, DayTimeline.HEIGHT_FULL)

    assert not timeline._compact
    assert timeline.sizeHint().height() == DayTimeline.HEIGHT_FULL


# --- a day with no punches ------------------------------------------------------------------


def test_a_status_day_says_what_it_was(qapp: QApplication) -> None:
    """Leave, a holiday and outdoor duty all rendered "No punches recorded" on a dead track."""
    timeline = DayTimeline(DARK)
    timeline.set_day((), status_label="Outdoor duty · Bengaluru training")

    assert "Bengaluru" in timeline.toolTip()


def test_a_genuinely_empty_day_still_says_so(qapp: QApplication) -> None:
    timeline = DayTimeline(DARK)
    timeline.set_day(())
    assert timeline.toolTip() == "No punches recorded"


# --- the journey ----------------------------------------------------------------------------


def test_breaks_are_rows_of_their_own(qapp: QApplication) -> None:
    """Four work spans with three gaps between them is seven entries, not four."""
    analysis = analyze_day(punches(*BUSY), day=DAY)
    journey = DayJourney(DARK)
    journey.set_segments(analysis.segments)

    assert journey._layout.count() == 7


def test_the_journey_is_empty_for_a_day_with_no_punches(qapp: QApplication) -> None:
    journey = DayJourney(DARK)
    journey.set_segments(())
    assert journey._layout.count() == 0


def test_rebuilding_does_not_accumulate_rows(qapp: QApplication) -> None:
    analysis = analyze_day(punches(*BUSY), day=DAY)
    journey = DayJourney(DARK)
    journey.set_segments(analysis.segments)
    journey.set_segments(analysis.segments)

    assert journey._layout.count() == 7


def test_an_inferred_end_is_marked_on_its_own_row(qapp: QApplication) -> None:
    """Rather than in a Note column that was empty on every other row."""
    from PySide6.QtWidgets import QLabel

    analysis = analyze_day(punches(("09:00", "in"), ("12:00", "in"), ("18:00", "out")), day=DAY)
    journey = DayJourney(DARK)
    journey.set_segments(analysis.segments)

    wording = " ".join(label.text() for label in journey.findChildren(QLabel))
    assert "inferred" in wording


def test_every_row_names_what_it_is(qapp: QApplication) -> None:
    analysis = analyze_day(punches(*BUSY), day=DAY)
    journey = DayJourney(DARK)
    journey.set_segments(analysis.segments)

    from PySide6.QtWidgets import QLabel

    wording = " ".join(label.text() for label in journey.findChildren(QLabel))
    assert wording.count("Worked") == 4
    assert wording.count("Break") == 3
    # And the durations read as durations, not as clock times.
    assert "8m" in wording
