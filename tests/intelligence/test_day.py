"""Day analysis: the numbers the Today screen shows."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from cerepulse.intelligence.day import DayState, analyze_day
from cerepulse.intelligence.insights import ActionKind, InsightKind, Severity
from cerepulse.intelligence.policy import ShiftPolicy
from cerepulse.models.swipe import SwipeRequest, SwipeStatus
from cerepulse.models.values import Duration
from tests.intelligence.conftest import DAY, at, punches

FULL_DAY = (("09:00", "in"), ("13:00", "out"), ("14:00", "in"), ("18:00", "out"))


def kinds(analysis) -> set[InsightKind]:  # type: ignore[no-untyped-def]
    return {insight.kind for insight in analysis.insights}


# --- core arithmetic ------------------------------------------------------------------


def test_a_standard_day_meets_the_target() -> None:
    analysis = analyze_day(punches(*FULL_DAY), day=DAY)

    assert analysis.state is DayState.COMPLETE
    assert analysis.worked.as_clock() == "8:00"
    assert analysis.break_taken.as_clock() == "1:00"
    assert analysis.work_remaining.minutes == 0
    assert analysis.break_remaining.minutes == 0
    assert analysis.extra_worked.minutes == 0
    assert analysis.target_met
    assert not analysis.early_exit


def test_expected_out_is_first_in_plus_shift_span() -> None:
    analysis = analyze_day(punches(*FULL_DAY), day=DAY)
    assert analysis.expected_out is not None
    assert analysis.expected_out.time() == time(18, 0)


def test_break_adjusted_out_moves_when_the_break_overruns() -> None:
    """A ninety-minute lunch means leaving at +9h would be half an hour short of eight."""
    long_break = (("09:00", "in"), ("13:00", "out"), ("14:30", "in"), ("18:00", "out"))
    analysis = analyze_day(punches(*long_break), day=DAY)

    assert analysis.break_taken.as_clock() == "1:30"
    assert analysis.expected_out.time() == time(18, 0)  # the flat, under-reporting figure
    assert analysis.expected_out_break_adjusted.time() == time(18, 30)
    assert analysis.leave_at == analysis.expected_out_break_adjusted


def test_a_short_break_never_pulls_the_finish_time_in() -> None:
    """The shift span already assumes the full allowance, so finishing early isn't offered."""
    short_break = (("09:00", "in"), ("13:00", "out"), ("13:15", "in"), ("18:00", "out"))
    analysis = analyze_day(punches(*short_break), day=DAY)

    assert analysis.break_taken.as_clock() == "0:15"
    assert analysis.expected_out_break_adjusted.time() == time(18, 0)


def test_break_remaining_counts_down_from_the_allowance() -> None:
    analysis = analyze_day(
        punches(("09:00", "in"), ("13:00", "out"), ("13:20", "in"), ("18:00", "out")), day=DAY
    )
    assert analysis.break_remaining.as_clock() == "0:40"


# --- early exit and swipe requests ----------------------------------------------------


def test_short_complete_day_is_an_early_exit_needing_a_swipe_request() -> None:
    analysis = analyze_day(punches(("09:00", "in"), ("16:00", "out")), day=DAY)

    assert analysis.early_exit
    assert analysis.swipe_request_needed
    assert analysis.work_remaining.as_clock() == "1:00"
    assert {InsightKind.EARLY_EXIT, InsightKind.SWIPE_NEEDED} <= kinds(analysis)


def test_the_swipe_suggestion_carries_an_action() -> None:
    analysis = analyze_day(punches(("09:00", "in"), ("16:00", "out")), day=DAY)
    suggestion = next(i for i in analysis.insights if i.kind is InsightKind.SWIPE_NEEDED)

    assert suggestion.action is not None
    assert suggestion.action.kind is ActionKind.OPEN_SWIPE_REQUEST


def test_an_existing_request_suppresses_the_suggestion_and_reports_status() -> None:
    """The live data had seven short days and seven filed requests — the same seven days."""
    filed = SwipeRequest(
        for_date=DAY,
        direction="In",
        in_time=time(9, 0),
        out_time=None,
        remark="Extra night work",
        status=SwipeStatus.IN_PROCESS,
    )
    analysis = analyze_day(
        punches(("09:00", "in"), ("16:00", "out")), day=DAY, swipe_requests=[filed]
    )

    assert analysis.early_exit
    assert not analysis.swipe_request_needed
    assert InsightKind.SWIPE_NEEDED not in kinds(analysis)
    assert InsightKind.SWIPE_FILED in kinds(analysis)


def test_a_rejected_request_does_not_suppress_the_suggestion() -> None:
    rejected = SwipeRequest(
        for_date=DAY,
        direction="In",
        in_time=None,
        out_time=None,
        remark="",
        status=SwipeStatus.REJECTED,
    )
    analysis = analyze_day(
        punches(("09:00", "in"), ("16:00", "out")), day=DAY, swipe_requests=[rejected]
    )
    assert analysis.swipe_request_needed


def test_a_request_for_another_day_is_ignored() -> None:
    other = SwipeRequest(
        for_date=date(2026, 7, 1),
        direction="In",
        in_time=None,
        out_time=None,
        remark="",
        status=SwipeStatus.IN_PROCESS,
    )
    analysis = analyze_day(
        punches(("09:00", "in"), ("16:00", "out")), day=DAY, swipe_requests=[other]
    )
    assert analysis.swipe_request_needed


def test_an_ongoing_short_day_is_not_an_early_exit() -> None:
    """Nobody has left early at two in the afternoon."""
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=at("14:00"))

    assert analysis.state is DayState.INCOMPLETE
    assert not analysis.early_exit
    assert not analysis.swipe_request_needed


# --- overtime and ongoing -------------------------------------------------------------


def test_extra_hours_are_reported() -> None:
    analysis = analyze_day(
        punches(("09:00", "in"), ("13:00", "out"), ("14:00", "in"), ("19:30", "out")), day=DAY
    )
    assert analysis.extra_worked.as_clock() == "1:30"
    assert InsightKind.OVERTIME in kinds(analysis)


def test_ongoing_day_reports_time_left() -> None:
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=at("13:00"))

    assert analysis.work_remaining.as_clock() == "4:00"
    assert InsightKind.STILL_WORKING in kinds(analysis)
    assert InsightKind.OVERTIME not in kinds(analysis)


def test_ongoing_day_past_target_says_you_can_go() -> None:
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=at("17:30"))

    assert analysis.work_remaining.minutes == 0
    assert InsightKind.ON_TRACK in kinds(analysis)


def test_long_break_produces_an_advisory() -> None:
    analysis = analyze_day(
        punches(("09:00", "in"), ("12:00", "out"), ("14:00", "in"), ("19:00", "out")), day=DAY
    )
    assert InsightKind.LONG_BREAK in kinds(analysis)


def test_missing_punch_surfaces_as_a_warning_insight() -> None:
    analysis = analyze_day(punches(("09:00", "in"), ("12:00", "in"), ("18:00", "out")), day=DAY)
    warning = next(i for i in analysis.insights if i.kind is InsightKind.MISSING_PUNCH)
    assert warning.severity is Severity.WARNING


# --- grid-only days ---------------------------------------------------------------------


def test_a_grid_only_day_says_where_its_numbers_came_from() -> None:
    analysis = analyze_day(punches(("09:20", "in"), ("18:30", "out")), day=DAY, grid_only=True)
    note = next(i for i in analysis.insights if i.kind is InsightKind.GRID_ONLY)

    assert "not counted" in note.detail
    assert analysis.worked.as_clock() == "9:10"


def test_a_grid_only_today_is_unfinished_not_short() -> None:
    """The grid's last-out is the latest swipe so far, not a clock-off."""
    analysis = analyze_day(
        punches(("09:20", "in"), ("11:30", "out")),
        day=DAY,
        now=at("12:20"),
        grid_only=True,
    )

    assert analysis.state is DayState.INCOMPLETE
    assert not analysis.early_exit
    assert not analysis.swipe_request_needed


def test_a_grid_only_day_in_the_past_is_read_as_finished() -> None:
    """Yesterday's last-out really was the end of the day."""
    analysis = analyze_day(
        punches(("09:20", "in"), ("11:30", "out")),
        day=DAY,
        now=datetime.combine(DAY + timedelta(days=1), time(9, 0)),
        grid_only=True,
    )

    assert analysis.state is DayState.COMPLETE
    assert analysis.early_exit


def test_grid_only_on_an_empty_day_adds_nothing() -> None:
    """There is nothing to caveat when there are no times at all."""
    analysis = analyze_day([], day=DAY, grid_only=True)
    assert kinds(analysis) == {InsightKind.NO_PUNCHES}


# --- empty days -----------------------------------------------------------------------


def test_a_day_with_no_punches_is_empty_not_short() -> None:
    """Weekends and holidays must not register as early exits."""
    analysis = analyze_day([], day=DAY)

    assert analysis.state is DayState.EMPTY
    assert not analysis.early_exit
    assert not analysis.swipe_request_needed
    assert not analysis.target_met
    assert kinds(analysis) == {InsightKind.NO_PUNCHES}
    assert analysis.leave_at is None


# --- explanations ---------------------------------------------------------------------


def test_every_headline_metric_can_explain_itself() -> None:
    analysis = analyze_day(punches(*FULL_DAY), day=DAY)
    assert {
        "worked",
        "break_taken",
        "expected_out",
        "expected_out_break_adjusted",
        "gross_span",
    } <= set(analysis.explanations)


def test_worked_explanation_shows_the_segments() -> None:
    analysis = analyze_day(punches(*FULL_DAY), day=DAY)
    assert analysis.explanations["worked"].formula == "4h + 4h = 8h"


def test_explanations_carry_repair_notes() -> None:
    analysis = analyze_day(punches(("09:00", "in"), ("12:00", "in"), ("18:00", "out")), day=DAY)
    assert any("Missing Out punch" in note for note in analysis.explanations["worked"].notes)


def test_break_adjusted_explanation_says_why_it_moved() -> None:
    analysis = analyze_day(
        punches(("09:00", "in"), ("13:00", "out"), ("14:30", "in"), ("18:30", "out")), day=DAY
    )
    notes = analysis.explanations["expected_out_break_adjusted"].notes
    assert any("exceeded the allowance" in note for note in notes)


# --- policy ---------------------------------------------------------------------------


def test_the_target_gets_the_right_article() -> None:
    """Writing "a 8:00 target" is the sort of thing that makes an app feel unfinished."""
    eight = analyze_day(punches(("09:00", "in"), ("16:00", "out")), day=DAY)
    seven = analyze_day(
        punches(("09:00", "in"), ("14:00", "out")),
        day=DAY,
        policy=ShiftPolicy(
            work_target=Duration(7 * 60), break_target=Duration(60), shift_span=Duration(480)
        ),
    )

    assert (
        "an 8h target" in next(i for i in eight.insights if i.kind is InsightKind.EARLY_EXIT).detail
    )
    assert (
        "a 7h target" in next(i for i in seven.insights if i.kind is InsightKind.EARLY_EXIT).detail
    )


def test_a_custom_policy_is_honoured() -> None:
    policy = ShiftPolicy(
        work_target=Duration(7 * 60), break_target=Duration(30), shift_span=Duration(450)
    )
    analysis = analyze_day(punches(("09:00", "in"), ("16:30", "out")), day=DAY, policy=policy)

    assert not analysis.early_exit
    assert analysis.extra_worked.as_clock() == "0:30"
    assert analysis.expected_out.time() == time(16, 30)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"work_target": Duration(0)},
        {"shift_span": Duration(0)},
        {"break_target": Duration(-1)},
    ],
)
def test_nonsensical_policies_are_rejected(kwargs: dict[str, Duration]) -> None:
    with pytest.raises(ValueError):
        ShiftPolicy(**kwargs)


# --- break headroom -------------------------------------------------------------------


def test_an_in_progress_day_reports_free_break_headroom() -> None:
    """The shift span already prices in the full allowance, so break up to it is free."""
    analysis = analyze_day(
        punches(("09:00", "in"), ("12:00", "out"), ("12:20", "in")),
        day=DAY,
        now=at("14:00"),
    )
    headroom = next(i for i in analysis.insights if i.kind is InsightKind.BREAK_HEADROOM)

    assert "40m" in headroom.title
    assert "6:00 PM" in headroom.detail


def test_headroom_is_replaced_by_the_overrun_message_once_exceeded() -> None:
    analysis = analyze_day(
        punches(("09:00", "in"), ("12:00", "out"), ("13:30", "in")),
        day=DAY,
        now=at("15:00"),
    )
    kinds = {i.kind for i in analysis.insights}

    assert InsightKind.LONG_BREAK in kinds
    assert InsightKind.BREAK_HEADROOM not in kinds


def test_headroom_stops_once_the_target_is_met() -> None:
    """The finish line is behind you; advice about moving it is advice about nothing."""
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=at("18:30"))

    assert InsightKind.ON_TRACK in kinds(analysis)
    assert InsightKind.BREAK_HEADROOM not in kinds(analysis)


def test_a_finished_day_does_not_offer_headroom() -> None:
    """It is advice about a decision that is no longer open."""
    analysis = analyze_day(
        punches(("09:00", "in"), ("13:00", "out"), ("13:20", "in"), ("18:00", "out")), day=DAY
    )
    assert InsightKind.BREAK_HEADROOM not in {i.kind for i in analysis.insights}


def test_insights_lead_with_what_needs_attention() -> None:
    """Append order is an implementation detail; the strip should read by importance."""
    analysis = analyze_day(punches(("09:00", "in"), ("12:00", "in"), ("15:00", "out")), day=DAY)
    severities = [i.severity for i in analysis.insights]

    assert severities == sorted(
        severities, key=lambda s: {"CRITICAL": 0, "WARNING": 1, "SUCCESS": 2, "INFO": 3}[s.name]
    )
    assert analysis.insights[0].severity is Severity.WARNING


def test_the_answer_outranks_the_footnote_within_a_severity() -> None:
    """Sorting the tie by kind name alphabetically put break headroom above time left."""
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=at("14:00"))
    order = [i.kind for i in analysis.insights]

    assert order.index(InsightKind.STILL_WORKING) < order.index(InsightKind.BREAK_HEADROOM)
