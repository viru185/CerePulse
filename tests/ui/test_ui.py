"""UI unit tests — formatting, theming, widgets, and the background task runner.

Run headless via ``QT_QPA_PLATFORM=offscreen`` (set in conftest), so these work in CI with
no display attached.
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from PySide6.QtWidgets import QApplication

from cerepulse.core.errors import TransportError
from cerepulse.intelligence.day import analyze_day
from cerepulse.intelligence.insights import Insight, InsightKind, Severity
from cerepulse.models.attendance import Punch, PunchDirection
from cerepulse.models.values import Duration
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import DARK, LIGHT, palette_for, stylesheet
from cerepulse.ui.views.today import summary_text
from cerepulse.ui.widgets import Banner, Card, InsightStrip, SegmentBar
from cerepulse.ui.workers import TaskRunner

DAY = date(2026, 7, 28)


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
    assert fmt.duration(Duration(63)) == "1:03"
    assert fmt.duration(Duration(63), signed=True) == "+1:03"
    assert fmt.duration(Duration(-63), signed=True) == "-1:03"
    assert fmt.duration(Duration(0), signed=True) == "0:00"


def test_duration_words_reads_as_prose() -> None:
    assert fmt.duration_words(Duration(543)) == "9h 03m"
    assert fmt.duration_words(Duration(480)) == "8h"
    assert fmt.duration_words(Duration(7)) == "7m"


def test_countdown_says_now_rather_than_a_negative() -> None:
    target = datetime(2026, 7, 28, 18, 0)
    assert fmt.countdown(target, now=datetime(2026, 7, 28, 17, 0)) == "1:00"
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
