"""The two rebuilt screens.

Week previously answered none of the three questions it exists for — it was four cards, a
row of 14-pixel slivers, and a terminal stretch that left the bottom third of the window
empty. Records replaces two screens that were split by which portal page the data came from.

Neither of the screens they replace had any test at all, which is why deleting them broke
nothing and proved nothing.
"""

from __future__ import annotations

from datetime import date, datetime, time

from PySide6.QtWidgets import QApplication, QLabel

from cerepulse.intelligence.attention import Attention, AttentionKind
from cerepulse.intelligence.day import DayAnalysis, analyze_day
from cerepulse.intelligence.month import DayRollup, WeekAnalysis
from cerepulse.intelligence.policy import ShiftPolicy
from cerepulse.intelligence.records import Record, RecordKind
from cerepulse.models.attendance import DayStatus, Punch, PunchDirection
from cerepulse.models.leave import Holiday
from cerepulse.models.values import Duration
from cerepulse.ui.theme import DARK
from cerepulse.ui.views.records import RecordsView
from cerepulse.ui.views.week import WeekView
from cerepulse.ui.widgets import DayTimeline

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


# --- week: one scale for all seven ---------------------------------------------------------


def analysis_for(offset: int, *, start_hour: int, end_hour: int) -> DayAnalysis:
    """A real analysis of a real pair of punches, rather than a hand-built stand-in."""
    when = MONDAY.replace(day=MONDAY.day + offset)
    return analyze_day(
        [
            Punch(at=time(start_hour, 0), direction=PunchDirection.IN),
            Punch(at=time(end_hour, 0), direction=PunchDirection.OUT),
        ],
        day=when,
    )


def test_a_day_with_punches_gets_a_timeline_rather_than_a_proportion_bar(
    qapp: QApplication,
) -> None:
    """A bar can say how much of the day there was. Only a timeline says when it happened,
    and on a week screen that is most of what there is to notice."""
    view = WeekView(DARK)
    view.show_week(
        week(rollup(0), worked=480, target=480),
        TARGET,
        analyses={MONDAY: analysis_for(0, start_hour=9, end_hour=18)},
    )

    row = view._days.itemAt(0).widget()
    assert row.findChildren(DayTimeline), "the punches are cached; draw them"


def test_all_seven_days_share_one_scale(qapp: QApplication) -> None:
    """Left to itself each timeline fits its own day, so a 9-to-6 Monday and a 1-to-10
    Tuesday draw as identical bars — hiding the difference the stack exists to show."""
    view = WeekView(DARK)
    view.show_week(
        week(rollup(0), rollup(1), worked=960, target=960),
        TARGET,
        analyses={
            MONDAY: analysis_for(0, start_hour=9, end_hour=18),
            MONDAY.replace(day=MONDAY.day + 1): analysis_for(1, start_hour=13, end_hour=22),
        },
    )

    timelines = [
        line
        for index in range(view._days.count())
        for line in view._days.itemAt(index).widget().findChildren(DayTimeline)
    ]
    assert len(timelines) == 2
    spans = {(line._start.hour, line._end.hour) for line in timelines}
    assert len(spans) == 1, "two different days must be drawn against the same ruler"


def test_the_hour_axis_is_hidden_when_no_day_has_punches(qapp: QApplication) -> None:
    """A ruler under seven proportion bars measures nothing."""
    view = WeekView(DARK)
    view.show_week(week(rollup(0), worked=480, target=480), TARGET)

    assert not view._axis.isVisibleTo(view)


def test_the_shared_domain_is_clamped_against_one_outlier() -> None:
    """One 3 AM deployment must not squash the other six days into an inch. It still
    draws — just against the same ruler as everything else."""
    from cerepulse.ui.widgets import DOMAIN_EARLIEST, DOMAIN_LATEST, shared_domain

    ordinary = (datetime(2026, 7, 20, 9, 0), datetime(2026, 7, 20, 18, 0))
    nightshift = (datetime(2026, 7, 21, 3, 0), datetime(2026, 7, 21, 23, 30))

    first, last = shared_domain([ordinary, nightshift])
    assert first >= DOMAIN_EARLIEST
    assert last <= DOMAIN_LATEST


def test_the_shared_domain_covers_an_ordinary_week() -> None:
    from cerepulse.ui.widgets import shared_domain

    first, last = shared_domain(
        [
            (datetime(2026, 7, 20, 9, 50), datetime(2026, 7, 20, 18, 51)),
            (datetime(2026, 7, 21, 8, 15), datetime(2026, 7, 21, 17, 5)),
        ]
    )
    assert first <= 8, "the earliest start must be inside the range"
    assert last >= 19, "the latest end must be inside the range"


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


def _pick(view: RecordsView, *, kind: str | None = None, state: str | None = None) -> None:
    from cerepulse.ui.views.records import KIND_FILTERS, STATE_FILTERS

    if kind is not None:
        view._kind.setCurrentIndex([name for name, _ in KIND_FILTERS].index(kind))
    if state is not None:
        view._state.setCurrentIndex([name for name, _ in STATE_FILTERS].index(state))


def test_filtering_narrows_the_timeline(qapp: QApplication) -> None:
    view = RecordsView(DARK)
    view.show_records(records())
    _pick(view, kind="Outdoor duty")

    assert view._timeline.count() == 1
    assert "Bengaluru" in _text(view)


def test_needs_doing_finds_the_one_thing_that_does(qapp: QApplication) -> None:
    view = RecordsView(DARK)
    view.show_records(records())
    _pick(view, state="Needs doing")

    assert view._timeline.count() == 1


def test_the_two_axes_combine(qapp: QApplication) -> None:
    """The question the single dropdown could not ask.

    Choosing a kind used to throw away the state and vice versa, because both lived in one
    control. "Swipe requests that are still waiting" needed both halves at once.
    """
    view = RecordsView(DARK)
    view.show_records(records())

    _pick(view, kind="Swipe requests", state="Waiting on approval")
    assert view._timeline.count() == 1

    # Same kind, a state it is not in.
    _pick(view, state="Needs doing")
    assert view._timeline.count() == 0


def test_absence_can_be_filtered_to(qapp: QApplication) -> None:
    """It had no entry at all before, so the one kind that always needs explaining was the
    one kind the filter could not isolate."""
    view = RecordsView(DARK)
    view.show_records(records())
    _pick(view, kind="Absence")

    assert view._timeline.count() == 1


def test_an_empty_timeline_explains_itself(qapp: QApplication) -> None:
    view = RecordsView(DARK)
    view.show_records([])

    assert view._timeline.count() == 0
    assert view._nothing.isVisibleTo(view)


def test_rebuilding_does_not_leave_rows_painting(qapp: QApplication) -> None:
    """Counting the layout was never enough to catch this.

    ``takeAt`` empties the layout immediately, so the old assertion passed while up to four
    generations of row widgets stayed parented and drawing at their last geometry, waiting on
    a ``deleteLater`` the event loop had not serviced yet. The records timeline is rebuilt
    once per source per sync, so what the screen actually showed mid-sync was the same row
    stacked four deep. The children are the thing to count.
    """
    from cerepulse.ui.views.records import _RecordRow

    view = RecordsView(DARK)
    for _ in range(4):
        view.show_records(records())

    assert view._timeline.count() == 5
    assert len(view.findChildren(_RecordRow)) == 5


def test_every_filter_combination_survives_an_empty_timeline(qapp: QApplication) -> None:
    from cerepulse.ui.views.records import KIND_FILTERS, STATE_FILTERS

    view = RecordsView(DARK)
    view.show_records([])
    for kind in range(len(KIND_FILTERS)):
        for state in range(len(STATE_FILTERS)):
            view._kind.setCurrentIndex(kind)
            view._state.setCurrentIndex(state)


# --- the period window ------------------------------------------------------------------


def test_the_period_defaults_to_the_last_two_months(qapp: QApplication) -> None:
    """Whole calendar months, not sixty days — a day count would slide a request off the
    screen mid-conversation about it."""
    view = RecordsView(DARK)
    assert view.period_start(today=date(2026, 8, 5)) == date(2026, 7, 1)


def test_each_period_computes_its_own_floor(qapp: QApplication) -> None:
    from cerepulse.ui.views.records import PERIODS

    view = RecordsView(DARK)
    labels = [label for label, _months in PERIODS]
    today = date(2026, 8, 5)

    view._period.setCurrentIndex(labels.index("Last 6 months"))
    assert view.period_start(today=today) == date(2026, 3, 1)

    view._period.setCurrentIndex(labels.index("This year"))
    assert view.period_start(today=today) == date(2026, 1, 1)

    view._period.setCurrentIndex(labels.index("Everything"))
    assert view.period_start(today=today) is None


def test_a_period_crossing_the_year_boundary_lands_in_the_old_year(qapp: QApplication) -> None:
    from cerepulse.ui.views.records import period_start

    assert period_start(2, today=date(2026, 1, 15)) == date(2025, 12, 1)
    assert period_start(6, today=date(2026, 2, 10)) == date(2025, 9, 1)


def test_the_render_cap_announces_itself(qapp: QApplication) -> None:
    """A silently bounded list reads as complete, which is worse than the big list it
    avoids."""
    from cerepulse.ui.views.records import MAX_ROWS

    view = RecordsView(DARK)
    many = [
        Record(
            date(2026, 1, 1) + __import__("datetime").timedelta(days=offset),
            RecordKind.LEAVE,
            "Leave",
        )
        for offset in range(MAX_ROWS + 50)
    ]
    view.show_records(many)

    assert view._timeline.count() == MAX_ROWS
    assert view._overflow.isVisibleTo(view)
    assert f"{MAX_ROWS} most recent of {MAX_ROWS + 50}" in view._overflow.text()


def test_a_list_inside_the_cap_shows_no_overflow_note(qapp: QApplication) -> None:
    view = RecordsView(DARK)
    view.show_records(records())
    assert not view._overflow.isVisibleTo(view)


# --- the holiday calendar ---------------------------------------------------------------


def calendar_() -> list[Holiday]:
    return [
        Holiday(day=date(2026, 1, 14), weekday="Wednesday", name="Uttrayan"),
        Holiday(day=date(2026, 8, 15), weekday="Saturday", name="Independence Day"),
        Holiday(day=date(2026, 12, 25), weekday="Friday", name="Christmas"),
    ]


def test_the_whole_published_year_is_listed(qapp: QApplication) -> None:
    """Not the month on screen. The timeline bounds holidays to the displayed month, which
    left the full calendar with nowhere to live at all."""
    view = RecordsView(DARK)
    view.show_holidays(calendar_(), today=date(2026, 8, 4))

    assert view.holidays.rowCount() == 3


def test_passed_holidays_are_marked_and_the_next_one_named(qapp: QApplication) -> None:
    view = RecordsView(DARK)
    view.show_holidays(calendar_(), today=date(2026, 8, 4))

    marks = [view.holidays.item(row, 3).text() for row in range(view.holidays.rowCount())]
    assert marks == ["Passed", "Next", ""]
    assert "Independence Day" in view._holidays_note.text()
    assert "2 of 3" in view._holidays_note.text()


def test_an_unsynced_calendar_says_so_rather_than_showing_nothing(qapp: QApplication) -> None:
    view = RecordsView(DARK)
    view.show_holidays([], today=date(2026, 8, 4))

    assert not view.holidays.isVisibleTo(view)
    assert "not synced" in view._holidays_note.text()


def test_a_spent_year_says_so(qapp: QApplication) -> None:
    view = RecordsView(DARK)
    view.show_holidays(calendar_(), today=date(2026, 12, 31))

    assert "have passed" in view._holidays_note.text()
