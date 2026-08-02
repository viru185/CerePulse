"""The two rebuilt screens.

Week previously answered none of the three questions it exists for — it was four cards, a
row of 14-pixel slivers, and a terminal stretch that left the bottom third of the window
empty. Records replaces two screens that were split by which portal page the data came from.

Neither of the screens they replace had any test at all, which is why deleting them broke
nothing and proved nothing.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import QApplication, QLabel

from cerepulse.intelligence.attention import Attention, AttentionKind
from cerepulse.intelligence.month import DayRollup, WeekAnalysis
from cerepulse.intelligence.policy import ShiftPolicy
from cerepulse.intelligence.records import Record, RecordKind
from cerepulse.models.attendance import DayStatus
from cerepulse.models.values import Duration
from cerepulse.ui.theme import DARK
from cerepulse.ui.views.records import RecordsView
from cerepulse.ui.views.week import WeekView

MONDAY = date(2026, 7, 20)
TARGET = Duration(8 * 60)


def rollup(
    offset: int,
    *,
    worked: int = 480,
    status: DayStatus = DayStatus.PRESENT,
    on_duty: bool = False,
    note: str = "",
    in_progress: bool = False,
) -> DayRollup:
    return DayRollup(
        day=MONDAY.replace(day=MONDAY.day + offset),
        worked=Duration(worked),
        status=status,
        estimated=False,
        is_working_day=status.counts_as_worked and not on_duty,
        on_duty=on_duty,
        note=note,
        in_progress=in_progress,
    )


def week(*days: DayRollup, worked: int, target: int, ahead: int = 0) -> WeekAnalysis:
    return WeekAnalysis(
        week_start=MONDAY,
        days=days,
        total_worked=Duration(worked),
        target=Duration(target),
        days_ahead=ahead,
        policy=ShiftPolicy.default(),
    )


def _text(widget: object) -> str:
    return " ".join(label.text() for label in widget.findChildren(QLabel))  # type: ignore[attr-defined]


# --- week: am I on track ------------------------------------------------------------------


def test_a_week_that_is_ahead_says_so(qapp: QApplication) -> None:
    view = WeekView(DARK)
    view.show_week(week(rollup(0, worked=540), worked=540, target=480), TARGET)

    assert "On track" in view._verdict.text()


def test_a_week_behind_says_what_each_remaining_day_needs(qapp: QApplication) -> None:
    """The forward-looking half. A deficit with no instruction is just bad news."""
    view = WeekView(DARK)
    view.show_week(week(rollup(0, worked=300), rollup(1), worked=300, target=480, ahead=2), TARGET)

    verdict = view._verdict.text()
    assert "behind" in verdict
    assert "remaining 2 day(s)" in verdict
    assert view.needed._value.text() != "—"


def test_a_finished_week_that_fell_short_does_not_ask_for_the_impossible(
    qapp: QApplication,
) -> None:
    """ "Work X per day over the remaining 0" is arithmetic nobody can act on."""
    view = WeekView(DARK)
    view.show_week(week(rollup(0, worked=300), worked=300, target=480, ahead=0), TARGET)

    assert "Nothing left to make it up in" in view._verdict.text()
    assert "no days left" in view.needed._caption.text()


def test_a_week_with_nothing_measurable_gives_no_verdict(qapp: QApplication) -> None:
    """A week of outdoor duty reported "On track — 0m across 0 day(s)", which is a
    judgement about nothing."""
    view = WeekView(DARK)
    view.show_week(
        week(
            rollup(0, worked=0, status=DayStatus.ON_DUTY, on_duty=True),
            worked=0,
            target=0,
        ),
        TARGET,
    )

    verdict = view._verdict.text()
    assert "Nothing measurable" in verdict
    assert "outdoor duty" in verdict


def test_an_unreachable_catch_up_is_named_as_such(qapp: QApplication) -> None:
    """Asking for fourteen hours tomorrow is not advice."""
    view = WeekView(DARK)
    view.show_week(week(rollup(0, worked=0), worked=0, target=1920, ahead=1), TARGET)

    assert "not realistic" in view._verdict.text()


# --- week: the day-by-day shape ------------------------------------------------------------


def test_every_day_of_the_week_gets_a_row(qapp: QApplication) -> None:
    view = WeekView(DARK)
    view.show_week(week(*(rollup(n) for n in range(5)), worked=2400, target=2400), TARGET)

    assert view._days.count() == 5


def test_an_outdoor_duty_day_says_what_it_was(qapp: QApplication) -> None:
    """It has no hours to draw, so without its own treatment it reads as a day off."""
    view = WeekView(DARK)
    view.show_week(
        week(
            rollup(0, worked=0, status=DayStatus.ON_DUTY, on_duty=True, note="Bengaluru"),
            worked=0,
            target=0,
        ),
        TARGET,
    )

    wording = _text(view)
    assert "Outdoor" in wording
    assert "Bengaluru" in wording


def test_a_holiday_and_an_absence_do_not_look_alike(qapp: QApplication) -> None:
    view = WeekView(DARK)
    view.show_week(
        week(
            rollup(0, worked=0, status=DayStatus.HOLIDAY),
            rollup(1, worked=0, status=DayStatus.ABSENT),
            worked=0,
            target=480,
        ),
        TARGET,
    )

    wording = _text(view)
    assert "Holiday" in wording
    assert "Absent" in wording


# --- week: what needs doing ----------------------------------------------------------------


def test_the_week_lists_its_outstanding_days(qapp: QApplication) -> None:
    view = WeekView(DARK)
    flagged = {
        MONDAY: Attention(MONDAY, AttentionKind.SHORT_NO_REQUEST, "Short by 1h with nothing filed.")
    }
    view.show_week(week(rollup(0), worked=480, target=480), TARGET, attention=flagged)

    assert view._attention.count() == 1
    assert "Short by 1h" in _text(view)


def test_attention_outside_this_week_is_not_shown(qapp: QApplication) -> None:
    """It is filtered from the month's set rather than recomputed, so it must be filtered."""
    view = WeekView(DARK)
    elsewhere = date(2026, 6, 3)
    flagged = {elsewhere: Attention(elsewhere, AttentionKind.MISSING_PUNCH, "A punch is missing.")}
    view.show_week(week(rollup(0), worked=480, target=480), TARGET, attention=flagged)

    assert view._attention.count() == 0
    assert view._nothing_needed.isVisibleTo(view)


# --- records --------------------------------------------------------------------------------


def records() -> list[Record]:
    return [
        Record(date(2026, 6, 20), RecordKind.COMP_OFF_EARNED, "Comp-off earned — 1 day(s)"),
        Record(date(2026, 6, 18), RecordKind.SWIPE_REQUEST, "Swipe request pending", pending=True),
        Record(date(2026, 6, 15), RecordKind.OUTDOOR_DUTY, "Outdoor duty", "Bengaluru"),
        Record(date(2026, 6, 10), RecordKind.LEAVE, "Leave"),
        Record(date(2026, 6, 2), RecordKind.ABSENCE, "Absent", needs_action=True),
    ]


def test_the_timeline_shows_everything_by_default(qapp: QApplication) -> None:
    view = RecordsView(DARK)
    view.show_records(records())

    assert view._timeline.count() == 5


def test_filtering_narrows_the_timeline(qapp: QApplication) -> None:
    from cerepulse.ui.views.records import FILTERS

    view = RecordsView(DARK)
    view.show_records(records())
    view._filter.setCurrentIndex([name for name, _ in FILTERS].index("Outdoor duty"))

    assert view._timeline.count() == 1
    assert "Bengaluru" in _text(view)


def test_needs_doing_finds_the_one_thing_that_does(qapp: QApplication) -> None:
    from cerepulse.ui.views.records import FILTERS

    view = RecordsView(DARK)
    view.show_records(records())
    view._filter.setCurrentIndex([name for name, _ in FILTERS].index("Needs doing"))

    assert view._timeline.count() == 1


def test_an_empty_timeline_explains_itself(qapp: QApplication) -> None:
    view = RecordsView(DARK)
    view.show_records([])

    assert view._timeline.count() == 0
    assert view._nothing.isVisibleTo(view)


def test_rebuilding_does_not_accumulate_rows(qapp: QApplication) -> None:
    view = RecordsView(DARK)
    view.show_records(records())
    view.show_records(records())

    assert view._timeline.count() == 5


def test_every_filter_survives_a_timeline_it_finds_nothing_in(qapp: QApplication) -> None:
    from cerepulse.ui.views.records import FILTERS

    view = RecordsView(DARK)
    view.show_records([])
    for index in range(len(FILTERS)):
        view._filter.setCurrentIndex(index)
