"""UI unit tests — formatting, theming, widgets, and the background task runner.

Run headless via ``QT_QPA_PLATFORM=offscreen`` (set in conftest), so these work in CI with
no display attached.
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QApplication

from cerepulse.core.errors import TransportError
from cerepulse.intelligence.day import analyze_day
from cerepulse.intelligence.insights import Insight, InsightKind, Severity
from cerepulse.models.attendance import Punch, PunchDirection
from cerepulse.models.values import Duration
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import DARK, LIGHT, MIN_CONTRAST, contrast_ratio, palette_for, stylesheet
from cerepulse.ui.views.today import TodayView, summary_text
from cerepulse.ui.widgets import Banner, Card, DayTimeline, InsightStrip, SegmentBar
from cerepulse.ui.workers import TaskRunner

DAY = date(2026, 7, 28)


def _rgb(hex_colour: str) -> str:
    """``"#34D399"`` -> ``"rgba(52, 211, 153"`` — the prefix a heatmap fill starts with."""
    red, green, blue = (int(hex_colour[index : index + 2], 16) for index in (1, 3, 5))
    return f"rgba({red}, {green}, {blue}"


def punches(*pairs: tuple[str, str]) -> list[Punch]:
    out = []
    for clock, direction in pairs:
        hour, minute = (int(part) for part in clock.split(":"))
        out.append(Punch(at=time(hour, minute), direction=PunchDirection.parse(direction)))
    return out


# --- formatting -----------------------------------------------------------------------


def test_clock_strips_the_leading_zero() -> None:
    assert fmt.clock(datetime(2026, 7, 28, 9, 21)) == "9:21 AM"
    assert fmt.clock(datetime(2026, 7, 28, 18, 31)) == "6:31 PM"
    assert fmt.clock(None) == fmt.EMPTY


def test_duration_can_be_signed() -> None:
    assert fmt.duration(Duration(63)) == "1h 03m"
    assert fmt.duration(Duration(63), signed=True) == "+1h 03m"
    assert fmt.duration(Duration(-63), signed=True) == "-1h 03m"
    assert fmt.duration(Duration(0), signed=True) == "0m"


def test_duration_words_reads_as_prose() -> None:
    assert fmt.duration_words(Duration(543)) == "9h 03m"
    assert fmt.duration_words(Duration(480)) == "8h"
    assert fmt.duration_words(Duration(7)) == "7m"


def test_countdown_says_now_rather_than_a_negative() -> None:
    target = datetime(2026, 7, 28, 18, 0)
    assert fmt.countdown(target, now=datetime(2026, 7, 28, 17, 0)) == "1h"
    assert fmt.countdown(target, now=datetime(2026, 7, 28, 18, 30)) == "now"


def test_relative_time_reads_naturally() -> None:
    now = datetime(2026, 7, 29, 12, 0)
    assert fmt.relative_time(datetime(2026, 7, 29, 11, 59, 30), now=now) == "just now"
    assert fmt.relative_time(datetime(2026, 7, 29, 11, 59), now=now) == "1 min ago"
    assert fmt.relative_time(datetime(2026, 7, 29, 11, 30), now=now) == "30 min ago"
    assert fmt.relative_time(datetime(2026, 7, 29, 9, 0), now=now) == "3 hours ago"
    assert fmt.relative_time(None) == "never"


def test_percent_is_clamped() -> None:
    assert fmt.percent(Duration(240), Duration(480)) == 0.5
    assert fmt.percent(Duration(600), Duration(480)) == 1.0
    assert fmt.percent(Duration(60), Duration(0)) == 0.0


# --- theme ----------------------------------------------------------------------------


def test_both_palettes_build_a_stylesheet() -> None:
    for palette in (DARK, LIGHT):
        sheet = stylesheet(palette)
        assert palette.surface in sheet
        assert "QLabel" in sheet


def test_labels_are_transparent_so_they_do_not_box_over_cards() -> None:
    """Regression: labels inherited the QWidget background and painted opaque rectangles."""
    sheet = stylesheet(DARK)
    assert "QLabel, QCheckBox {" in sheet
    assert "background: transparent" in sheet


@pytest.mark.parametrize("palette", [DARK, LIGHT], ids=["dark", "light"])
def test_every_text_colour_clears_the_contrast_floor(palette) -> None:  # type: ignore[no-untyped-def]
    """text_faint sat at 2.49:1 — barely half of AA — and it is every caption in the app.

    Checked against the darkest and lightest things text is ever drawn on, so a colour that
    reads on a card but not on a banner still fails here.
    """
    backgrounds = (palette.surface, palette.elevated, palette.overlay)
    for name in ("text", "text_muted", "text_faint"):
        colour = getattr(palette, name)
        worst = min(contrast_ratio(colour, behind) for behind in backgrounds)
        assert worst >= MIN_CONTRAST, f"{palette.name} {name} is only {worst:.2f}:1"


def test_accents_stay_legible_too() -> None:
    """They carry meaning, not just decoration, so they have to be readable as text."""
    for name in ("work", "rest", "good", "bad", "adjust"):
        colour = getattr(DARK, name)
        assert contrast_ratio(colour, DARK.elevated) >= MIN_CONTRAST


def test_muted_and_faint_stay_distinguishable() -> None:
    """Raising faint must not collapse the hierarchy into one grey."""
    for palette in (DARK, LIGHT):
        muted = contrast_ratio(palette.text_muted, palette.elevated)
        faint = contrast_ratio(palette.text_faint, palette.elevated)
        assert muted > faint


def test_named_themes_resolve() -> None:
    assert palette_for("dark") is DARK
    assert palette_for("light") is LIGHT
    assert palette_for("system") in (DARK, LIGHT)


# --- widgets --------------------------------------------------------------------------


def test_card_updates(qapp: QApplication) -> None:
    card = Card("Worked", value="0:00")
    card.set_value("9:03", accent=DARK.work)
    card.set_caption("vs 8:00 target")
    assert card._value.text() == "9:03"
    assert card._caption.isVisible() or not card.isVisible()


def test_banner_shows_and_clears(qapp: QApplication) -> None:
    banner = Banner()
    assert not banner.isVisible()
    banner.show_message("Offline", Severity.WARNING)
    assert banner.text() == "Offline"
    assert banner.objectName() == "BannerWarning"
    banner.clear_message()
    assert not banner.isVisible()


def test_the_banner_leads_with_the_worst_of_several_problems(qapp: QApplication) -> None:
    banner = Banner()
    banner.show_message("Leave ledger unavailable", Severity.WARNING, key="leave")
    banner.show_message("Could not sign in", Severity.CRITICAL, key="error")

    assert banner.text().startswith("Could not sign in")
    assert "+1 more" in banner.text()
    # The one it did not lead with is still reachable rather than merely counted.
    assert "Leave ledger unavailable" in banner.toolTip()


def test_a_banner_can_offer_the_logs(qapp: QApplication) -> None:
    """ "Check the logs for details" is an instruction nobody could follow: the path lived
    in a tooltip on a button on a screen there was no reason to open."""
    opened: list[bool] = []
    banner = Banner()
    banner.logs_requested.connect(lambda: opened.append(True))
    banner.show_message("Something went wrong.", Severity.CRITICAL, offer_logs=True)

    assert Banner.LOGS_LINK in banner.text()
    banner.linkActivated.emit(Banner.LOGS_LINK)
    assert opened == [True]


def test_a_banner_without_the_offer_has_no_link(qapp: QApplication) -> None:
    banner = Banner()
    banner.show_message("Offline", Severity.WARNING)
    assert "<a href" not in banner.text()


def test_message_text_is_escaped_rather_than_rendered(qapp: QApplication) -> None:
    """Portal text and exception strings reach these banners. Now that the banner renders
    rich text so it can carry a link, the vendor's markup must not come with it."""
    banner = Banner()
    banner.show_message("<b>Tot. Hrs.</b> & more", Severity.WARNING)

    assert "&lt;b&gt;" in banner.text()
    assert "&amp;" in banner.text()


def test_clearing_a_message_also_clears_its_log_offer(qapp: QApplication) -> None:
    banner = Banner()
    banner.show_message("Broke", Severity.CRITICAL, key="error", offer_logs=True)
    banner.clear_message("error")
    banner.show_message("Broke again", Severity.CRITICAL, key="error")

    assert "<a href" not in banner.text()


def test_one_source_clearing_does_not_erase_another(qapp: QApplication) -> None:
    """A clean sync used to wipe the sign-in error sitting above it."""
    banner = Banner()
    banner.show_message("Could not sign in", Severity.CRITICAL, key="error")
    banner.show_message("Some data could not be refreshed", Severity.WARNING, key="sync")

    banner.clear_message("sync")

    assert banner.isVisible()
    assert banner.text() == "Could not sign in"


def test_clearing_everything_still_works(qapp: QApplication) -> None:
    banner = Banner()
    banner.show_message("One", Severity.INFO, key="a")
    banner.show_message("Two", Severity.INFO, key="b")
    banner.clear_message()
    assert not banner.isVisible()


def test_insight_strip_rebuilds(qapp: QApplication) -> None:
    strip = InsightStrip(DARK)
    strip.set_insights([Insight(InsightKind.OVERTIME, Severity.SUCCESS, "1:03 extra worked")])
    assert strip._layout.count() == 1

    strip.set_insights([])
    assert strip._layout.count() == 0


def test_segment_bar_accepts_an_empty_day(qapp: QApplication) -> None:
    bar = SegmentBar(DARK)
    bar.set_segments(())
    assert "No punches" in bar.toolTip()


def test_segment_bar_summarizes_the_span(qapp: QApplication) -> None:
    analysis = analyze_day(punches(("09:21", "in"), ("18:31", "out")), day=DAY)
    bar = SegmentBar(DARK)
    bar.set_segments(analysis.segments)
    assert "9.2h" in bar.toolTip()


# --- today summary --------------------------------------------------------------------


def test_summary_text_is_pasteable() -> None:
    analysis = analyze_day(
        punches(("09:21", "in"), ("13:00", "out"), ("14:00", "in"), ("18:31", "out")), day=DAY
    )
    text = summary_text(analysis)

    assert "9:21 AM" in text
    assert "6:31 PM" in text
    assert "Worked" in text


def test_summary_notes_a_repaired_punch() -> None:
    analysis = analyze_day(punches(("09:00", "in"), ("12:00", "in"), ("18:00", "out")), day=DAY)
    assert "Note:" in summary_text(analysis)


# --- today view -------------------------------------------------------------------------


def test_the_strip_does_not_repeat_the_hero_on_a_live_day(qapp: QApplication) -> None:
    """The hero already says "you can leave at 6:00 PM" in sixty-point type."""
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=datetime(2026, 7, 28, 14, 0))
    view = TodayView(DARK)
    view.show_analysis(analysis, is_today=True)

    assert InsightKind.STILL_WORKING in {i.kind for i in analysis.insights}
    assert view._insights._layout.count() == len(analysis.insights) - 1


def test_a_past_day_still_shows_it_because_there_is_no_countdown(qapp: QApplication) -> None:
    """Viewing a day you never punched out of, the hero has no answer to duplicate."""
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=datetime(2026, 7, 28, 14, 0))
    view = TodayView(DARK)
    view.show_analysis(analysis, is_today=False)

    assert view._insights._layout.count() == len(analysis.insights)


def test_today_leads_with_the_instruction(qapp: QApplication) -> None:
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=datetime(2026, 7, 28, 14, 0))
    view = TodayView(DARK)
    view.show_analysis(analysis, is_today=True)

    assert view._next_action._headline.text() == analysis.next_action.headline


def test_both_cards_lead_with_what_has_been_done(qapp: QApplication) -> None:
    """They used to disagree: Worked showed elapsed, Break showed remaining. Two numbers
    side by side described in opposite grammars cannot be read as a pair."""
    analysis = analyze_day(
        punches(("09:00", "in"), ("12:30", "out"), ("13:00", "in")),
        day=DAY,
        now=datetime(2026, 7, 28, 15, 0),
    )
    view = TodayView(DARK)
    view.show_analysis(analysis, is_today=True)

    assert view.worked._value.text() == fmt.duration(analysis.worked)
    assert view.break_taken._value.text() == fmt.duration(analysis.break_taken)


def test_both_captions_name_their_target_and_then_the_verdict(qapp: QApplication) -> None:
    """The break card never named `break_target`, so its number had nothing to be
    measured against."""
    analysis = analyze_day(
        punches(("09:00", "in"), ("12:30", "out"), ("13:00", "in")),
        day=DAY,
        now=datetime(2026, 7, 28, 15, 0),
    )
    view = TodayView(DARK)
    view.show_analysis(analysis, is_today=True)

    for caption in (view.worked._caption.text(), view.break_taken._caption.text()):
        assert "target" in caption
        assert "left" in caption or "over" in caption or "met" in caption


def test_an_overrun_break_says_by_how_much(qapp: QApplication) -> None:
    """It used to read the bare word "None" — true of what was left, silent about the one
    number anyone actually wants."""
    analysis = analyze_day(
        punches(("09:00", "in"), ("12:00", "out"), ("14:00", "in"), ("18:00", "out")),
        day=DAY,
    )
    view = TodayView(DARK)
    view.show_analysis(analysis, is_today=False)

    assert analysis.break_over.minutes > 0, "two hours off against a one-hour allowance"
    assert "None" not in view.break_taken._value.text()
    assert f"{fmt.duration(analysis.break_over)} over" in view.break_taken._caption.text()


def test_a_break_inside_its_allowance_says_what_is_left(qapp: QApplication) -> None:
    analysis = analyze_day(
        punches(("09:00", "in"), ("12:30", "out"), ("13:00", "in")),
        day=DAY,
        now=datetime(2026, 7, 28, 15, 0),
    )
    view = TodayView(DARK)
    view.show_analysis(analysis, is_today=True)

    assert f"{fmt.duration(analysis.break_remaining)} left" in view.break_taken._caption.text()


def test_worked_carries_its_own_consequence(qapp: QApplication) -> None:
    """Worked, remaining and overtime were three cards reading one fact from three sides,
    and only ever one of the latter two was non-zero."""
    ongoing = analyze_day(punches(("09:00", "in")), day=DAY, now=datetime(2026, 7, 28, 13, 0))
    view = TodayView(DARK)
    view.show_analysis(ongoing, is_today=True)

    assert view.worked._value.text() == fmt.duration(ongoing.worked)
    assert "left" in view.worked._caption.text()

    over = analyze_day(
        punches(("09:00", "in"), ("13:00", "out"), ("14:00", "in"), ("19:30", "out")), day=DAY
    )
    view.show_analysis(over, is_today=False)
    assert "overtime" in view.worked._caption.text()


def test_the_progress_bar_says_what_leaving_now_would_cost(qapp: QApplication) -> None:
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=datetime(2026, 7, 28, 14, 0))
    view = TodayView(DARK)
    view.show_analysis(analysis, is_today=True)

    caption = view._progress._caption.text()
    assert "% of the" in caption
    assert "leave now" in caption


def test_picking_a_date_asks_for_it_once(qapp: QApplication) -> None:
    """Rendering a day points the picker at it, which must not re-request the same day."""
    analysis = analyze_day(punches(("09:00", "in"), ("18:00", "out")), day=DAY)
    view = TodayView(DARK)
    asked: list[date] = []
    view.date_selected.connect(asked.append)

    view.show_analysis(analysis, is_today=False)
    assert asked == []

    view._picker.setDate(QDate(2026, 7, 20))
    assert asked == [date(2026, 7, 20)]


def test_the_timeline_places_the_day_on_a_clock(qapp: QApplication) -> None:
    analysis = analyze_day(punches(("09:21", "in"), ("18:31", "out")), day=DAY)
    timeline = DayTimeline(DARK)
    timeline.set_day(analysis.segments, leave_at=analysis.leave_at)

    # The axis starts at the hour containing the first punch, not at the punch itself.
    assert timeline._start == datetime(2026, 7, 28, 9, 0)
    assert "9:21a" in timeline.toolTip()


def test_the_timeline_leaves_room_for_a_finish_line_not_yet_reached(
    qapp: QApplication,
) -> None:
    """A bar that ends at the last punch cannot show a time still in the future."""
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=datetime(2026, 7, 28, 10, 0))
    timeline = DayTimeline(DARK)
    timeline.set_day(
        analysis.segments, leave_at=analysis.leave_at, now=datetime(2026, 7, 28, 10, 0)
    )

    assert timeline._end is not None
    assert analysis.leave_at is not None
    assert timeline._end > analysis.leave_at


def test_the_timeline_accepts_an_empty_day(qapp: QApplication) -> None:
    timeline = DayTimeline(DARK)
    timeline.set_day(())
    assert "No punches" in timeline.toolTip()


# --- attendance view --------------------------------------------------------------------


def _month_view(*, attention_on: set[date] | None = None):  # type: ignore[no-untyped-def]
    """A short July with one flagged day."""
    from datetime import timedelta

    from cerepulse.intelligence.attention import Attention, AttentionKind
    from cerepulse.intelligence.month import analyze_month
    from cerepulse.models.attendance import AttendanceDay, AttendanceMonth, DayStatus
    from cerepulse.models.swipe import SwipeRequest, SwipeStatus
    from cerepulse.services.attendance import MonthView

    days = []
    for offset in range(10):
        when = date(2026, 7, 1) + timedelta(days=offset)
        off = when.weekday() >= 5
        days.append(
            AttendanceDay(
                day=when,
                weekday=when.strftime("%a"),
                status=DayStatus.WEEKLY_OFF if off else DayStatus.PRESENT,
                first_in=None if off else time(9, 0),
                last_out=None if off else time(18, 0),
                total_hours=Duration(0 if off else 9 * 60),
            )
        )

    flagged = attention_on if attention_on is not None else {date(2026, 7, 2)}
    month = AttendanceMonth(employee_code="X", year=2026, month=7, days=tuple(days))
    return MonthView(
        month=month,
        analysis=analyze_month(days, year=2026, month=7, today=date(2026, 7, 31)),
        last_synced=None,
        from_cache=True,
        pending_detail=0,
        swipes={
            date(2026, 7, 3): [
                SwipeRequest(
                    for_date=date(2026, 7, 3),
                    direction="In",
                    in_time=None,
                    out_time=None,
                    remark="Forgot to swipe",
                    status=SwipeStatus.APPROVED,
                )
            ]
        },
        attention={
            day: Attention(day, AttentionKind.SHORT_NO_REQUEST, "Short by 1:00.") for day in flagged
        },
    )


def test_the_swipe_column_reports_a_filed_request(qapp: QApplication) -> None:
    from cerepulse.ui.views.attendance import AttendanceView

    view = AttendanceView(DARK)
    view.show_month(_month_view())

    row = next(
        r
        for r in range(view.table.rowCount())
        if view.table.item(r, 0).data(Qt.ItemDataRole.UserRole) == date(2026, 7, 3)
    )
    assert view.table.item(row, 7).text() == "Approved"
    assert "Forgot to swipe" in view.table.item(row, 7).toolTip()


def test_days_with_nothing_filed_leave_the_swipe_column_blank(qapp: QApplication) -> None:
    """A column of dashes reads as thirty missing values, not an ordinary month."""
    from cerepulse.ui.views.attendance import AttendanceView

    view = AttendanceView(DARK)
    view.show_month(_month_view())
    assert view.table.item(0, 7).text() == ""


# --- the month picker -------------------------------------------------------------------
#
# These build their periods at runtime, on purpose. `QComboBox.findData` matches Python
# objects by *identity* rather than equality, and CPython folds two identical tuple literals
# into one object — so a test written with literals passes against code that is broken, which
# is exactly how this survived from 0.6 to 0.14.


def _picker(months: list[tuple[int, int]], current: tuple[int, int]):  # type: ignore[no-untyped-def]
    from cerepulse.ui.views.attendance import AttendanceView

    view = AttendanceView(DARK)
    # Rebuilt, not passed straight through, so nothing here shares an object with the code.
    built = [(year, month) for year, month in months]
    view.set_available_months(built, (current[0], current[1]), cached=set(built))
    return view


def test_the_picker_shows_the_month_it_was_given(qapp: QApplication) -> None:
    """It used to land on the newest month whatever it was asked for: findData returned −1,
    setCurrentIndex was skipped, and the freshly repopulated combo sat on index 0."""
    view = _picker([(2026, 8), (2026, 7), (2026, 6)], (2026, 7))
    assert view.current_period() == (2026, 7)


def test_every_month_can_be_selected_and_reports_itself(qapp: QApplication) -> None:
    view = _picker([(2026, 8), (2026, 7), (2026, 6)], (2026, 8))
    emitted: list[tuple[int, int]] = []
    view.month_changed.connect(lambda year, month: emitted.append((year, month)))

    for wanted in ((2026, 6), (2026, 7), (2026, 8)):
        view._period.setCurrentIndex(view._index_of(wanted))
        assert view.current_period() == wanted

    assert emitted == [(2026, 6), (2026, 7), (2026, 8)]


def test_repopulating_keeps_the_month_it_is_on(qapp: QApplication) -> None:
    """A sync repopulates the picker; doing so must not drag the user back to today."""
    view = _picker([(2026, 8), (2026, 7), (2026, 6)], (2026, 6))
    for _ in range(4):
        view.set_available_months([(2026, 8), (2026, 7), (2026, 6)], (2026, 6), cached={(2026, 8)})
    assert view.current_period() == (2026, 6)


def test_a_month_that_was_not_offered_is_added_rather_than_substituted(
    qapp: QApplication,
) -> None:
    """Showing a different month than the table would recreate the very disagreement this
    picker is supposed to prevent."""
    view = _picker([(2026, 8), (2026, 7)], (2026, 3))
    assert view.current_period() == (2026, 3)


def _select_filter(view, label: str) -> None:  # type: ignore[no-untyped-def]
    from cerepulse.ui.views.attendance import FILTERS

    view._filter.setCurrentIndex([name for name, _ in FILTERS].index(label))


def test_the_filter_hides_settled_rows(qapp: QApplication) -> None:
    from cerepulse.ui.views.attendance import AttendanceView

    view = AttendanceView(DARK)
    view.show_month(_month_view())
    _select_filter(view, "Needs attention")

    visible = [r for r in range(view.table.rowCount()) if not view.table.isRowHidden(r)]
    assert len(visible) == 1
    assert view.table.item(visible[0], 0).data(Qt.ItemDataRole.UserRole) == date(2026, 7, 2)


def test_an_empty_filter_result_says_so_rather_than_showing_a_blank_table(
    qapp: QApplication,
) -> None:
    from cerepulse.ui.views.attendance import AttendanceView

    view = AttendanceView(DARK)
    view.show_month(_month_view(attention_on=set()))
    _select_filter(view, "Needs attention")

    assert "No days match" in view.banner.text()


def test_going_back_to_all_days_brings_every_row_back(qapp: QApplication) -> None:
    from cerepulse.ui.views.attendance import AttendanceView

    view = AttendanceView(DARK)
    view.show_month(_month_view())
    _select_filter(view, "Needs attention")
    _select_filter(view, "All days")

    assert not any(view.table.isRowHidden(r) for r in range(view.table.rowCount()))


def test_every_filter_survives_a_month_it_finds_nothing_in(qapp: QApplication) -> None:
    """Each predicate reads a _Row; one that assumes a field is populated would crash here."""
    from cerepulse.ui.views.attendance import FILTERS, AttendanceView

    view = AttendanceView(DARK)
    view.show_month(_month_view(attention_on=set()))
    for label, _predicate in FILTERS:
        _select_filter(view, label)


def test_selecting_a_row_fills_the_drawer(qapp: QApplication) -> None:
    """The whole point: reading one day without leaving the month."""
    from cerepulse.ui.views.attendance import AttendanceView

    view = AttendanceView(DARK)
    view.show_month(_month_view())
    # isVisibleTo, not isVisible: nothing is on screen in an offscreen test, so isVisible
    # would be False either way and the assertion would pass without proving anything.
    assert not view.drawer.isVisibleTo(view)

    view.show_day(date(2026, 7, 2))

    assert view.drawer.isVisibleTo(view)
    assert "02 July" in view.drawer._date.text() or "2 July" in view.drawer._date.text()


def test_the_drawer_says_when_the_figures_came_from_the_grid(qapp: QApplication) -> None:
    """Otherwise an estimate and a measurement look identical, side by side."""
    from cerepulse.ui.views.attendance import AttendanceView

    view = AttendanceView(DARK)
    view.show_month(_month_view())
    view.show_day(date(2026, 7, 1))

    assert "not synced" in view.drawer._note.text()


def test_the_heatmap_measures_worked_time_not_the_gross_span(qapp: QApplication) -> None:
    """Tinting a nine-hour span against an eight-hour target turned the calendar green."""
    from cerepulse.intelligence.month import DayRollup
    from cerepulse.models.attendance import DayStatus
    from cerepulse.ui.widgets import _heat_colours

    def rollup(minutes: int) -> DayRollup:
        return DayRollup(
            day=date(2026, 7, 1),
            worked=Duration(minutes),
            status=DayStatus.PRESENT,
            estimated=False,
            is_working_day=True,
        )

    target = Duration(8 * 60)
    met, _ = _heat_colours(rollup(8 * 60), target, DARK)
    short, _ = _heat_colours(rollup(7 * 60), target, DARK)

    assert met.startswith(_rgb(DARK.good))
    assert short.startswith(_rgb(DARK.work))


def test_a_worked_weekend_is_not_greyed_out(qapp: QApplication) -> None:
    """It is one of the more interesting cells in the month, not one to hide."""
    from cerepulse.intelligence.month import DayRollup
    from cerepulse.models.attendance import DayStatus
    from cerepulse.ui.widgets import _heat_colours

    saturday = DayRollup(
        day=date(2026, 7, 18),
        worked=Duration(7 * 60),
        status=DayStatus.WEEKLY_OFF,
        estimated=True,
        is_working_day=False,
    )
    fill, _ = _heat_colours(saturday, Duration(8 * 60), DARK)
    assert fill != DARK.overlay


def test_the_heatmap_lays_the_month_out_as_a_calendar(qapp: QApplication) -> None:
    """1 July 2026 is a Wednesday, so the first cell belongs in the third column."""
    from cerepulse.ui.views.attendance import AttendanceView

    view = AttendanceView(DARK)
    view.show_month(_month_view())

    positions = {}
    grid = view.heatmap._grid
    for index in range(grid.count()):
        row, column, _, _ = grid.getItemPosition(index)
        widget = grid.itemAt(index).widget()
        positions[widget.text()] = (row, column)

    assert positions["1"] == (1, 2)  # row 0 is the weekday headings
    assert positions["6"] == (2, 0)  # the following Monday


# --- insights view ----------------------------------------------------------------------


def _trends_view(days: int, *, today: date):  # type: ignore[no-untyped-def]
    from datetime import timedelta

    from cerepulse.intelligence.trends import analyze_trends
    from cerepulse.models.attendance import AttendanceDay, DayStatus
    from cerepulse.services.attendance import TrendsView

    rows = []
    cursor = today - timedelta(days=days * 2)
    while len(rows) < days:
        if cursor.weekday() < 5:
            rows.append(
                AttendanceDay(
                    day=cursor,
                    weekday=cursor.strftime("%a"),
                    status=DayStatus.PRESENT,
                    first_in=time(9, 0),
                    last_out=time(18, 0),
                    total_hours=Duration(9 * 60),
                )
            )
        cursor += timedelta(days=1)

    return TrendsView(
        report=analyze_trends(rows, today=today, working_days_remaining=3),
        anomalies=[],
        months_cached=2,
        months_available=2,
        today=today,
    )


def test_insights_renders_a_full_history(qapp: QApplication) -> None:
    from cerepulse.ui.views.insights import InsightsView

    view = InsightsView(DARK)
    view.show_trends(_trends_view(30, today=date(2026, 7, 29)))

    assert view.months.rowCount() >= 1
    assert "9:00 AM" in view.typical_in._value.text()
    # Says what it is standing on before it says anything else.
    assert "30 working days" in view.footing.text()
    assert "estimated from the grid" in view.footing.text()
    # No punch detail cached, so the break stays blank rather than being back-solved.
    assert view.typical_break._value.text() == fmt.EMPTY


def test_insights_says_so_rather_than_inventing_a_habit_from_four_days(
    qapp: QApplication,
) -> None:
    """Drawing a habit from four days teaches the user to distrust the whole screen."""
    from cerepulse.ui.views.insights import InsightsView

    view = InsightsView(DARK)
    view.show_trends(_trends_view(4, today=date(2026, 7, 29)))

    assert "too few" in view.footing.text()
    assert view.footing.objectName() == "BannerWarning"


# --- task runner ----------------------------------------------------------------------


def test_a_task_result_reaches_the_callback(qapp: QApplication) -> None:
    runner = TaskRunner()
    seen: list[object] = []
    runner.submit("work", lambda: 42, on_success=seen.append)
    runner.wait(5000)
    qapp.processEvents()

    assert seen == [42]


def test_a_failing_task_reports_the_exception(qapp: QApplication) -> None:
    runner = TaskRunner()
    errors: list[BaseException] = []
    runner.submit(
        "work", lambda: (_ for _ in ()).throw(TransportError("offline")), on_error=errors.append
    )
    runner.wait(5000)
    qapp.processEvents()

    assert len(errors) == 1
    assert isinstance(errors[0], TransportError)


def test_a_crashing_task_does_not_kill_the_app(qapp: QApplication) -> None:
    """A bare exception in a worker must surface, not take the process down."""
    runner = TaskRunner()
    errors: list[BaseException] = []
    runner.submit("boom", lambda: 1 / 0, on_error=errors.append)
    runner.wait(5000)
    qapp.processEvents()

    assert isinstance(errors[0], ZeroDivisionError)


def test_activity_names_the_running_task(qapp: QApplication) -> None:
    """Ten task names used to collapse into "Syncing…", so a cache read and a two-minute
    history backfill looked identical."""
    runner = TaskRunner()
    seen: list[tuple[str, int]] = []
    runner.activity_changed.connect(lambda label, queued: seen.append((label, queued)))

    runner.submit("history", lambda: None)
    runner.wait(5000)
    qapp.processEvents()

    assert seen[0] == ("Fetching history", 0)


def test_activity_reports_the_queue_depth(qapp: QApplication) -> None:
    """The pool is single-slot, so work behind a backfill genuinely waits — say so."""
    runner = TaskRunner()
    depths: list[int] = []
    runner.activity_changed.connect(lambda _label, queued: depths.append(queued))

    for name in ("history", "trends", "leave"):
        runner.submit(name, lambda: None)
    runner.wait(5000)
    qapp.processEvents()

    assert max(depths) >= 2


def test_an_unknown_task_still_gets_a_label(qapp: QApplication) -> None:
    from cerepulse.ui.workers import describe

    assert describe("something-new") == "Working"
    assert describe("scope-swipe_requests") == "Fetching swipe requests"


def test_activity_clears_when_the_queue_drains(qapp: QApplication) -> None:
    runner = TaskRunner()
    runner.submit("leave", lambda: None)
    runner.wait(5000)
    qapp.processEvents()

    assert runner.activity == ""


def test_tasks_run_one_at_a_time(qapp: QApplication) -> None:
    """Concurrent postbacks would invalidate each other's page state."""
    runner = TaskRunner()
    order: list[str] = []

    def make(name: str):  # type: ignore[no-untyped-def]
        def work() -> None:
            order.append(f"start-{name}")
            order.append(f"end-{name}")

        return work

    for name in ("a", "b", "c"):
        runner.submit(name, make(name))
    runner.wait(5000)

    assert order == [
        "start-a", "end-a",
        "start-b", "end-b",
        "start-c", "end-c",
    ]  # fmt: skip


def test_busy_toggles_around_work(qapp: QApplication) -> None:
    runner = TaskRunner()
    states: list[bool] = []
    runner.busy_changed.connect(states.append)

    runner.submit("work", lambda: None)
    runner.wait(5000)
    qapp.processEvents()

    assert states[0] is True
    assert states[-1] is False
    assert not runner.busy


@pytest.mark.parametrize("theme", ["dark", "light", "system"])
def test_stylesheet_applies_cleanly(qapp: QApplication, theme: str) -> None:
    qapp.setStyleSheet(stylesheet(palette_for(theme)))
    assert qapp.styleSheet()
