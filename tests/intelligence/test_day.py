"""Day analysis: the numbers the Today screen shows."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from cerepulse.intelligence.day import DayState, analyze_day
from cerepulse.intelligence.insights import ActionKind, InsightKind, Severity
from cerepulse.intelligence.policy import ShiftPolicy
from cerepulse.intelligence.segments import DayEnvelope, IssueKind
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


# --- a past day whose Out never got punched ------------------------------------------------


def test_a_past_day_closes_at_the_portals_own_last_out() -> None:
    """The punch log ends on an In, but the grid still carries a last-out and a total — the
    portal closes the day even when the punch never landed. Reading only the log threw that
    away and left the day with no hours at all."""
    analysis = analyze_day(
        punches(("09:00", "in"), ("13:00", "out"), ("13:45", "in")),
        day=DAY,
        envelope=DayEnvelope(last_out=time(18, 30)),
    )

    assert analysis.state is DayState.COMPLETE
    assert not analysis.clocked_in
    assert analysis.last_out == datetime.combine(DAY, time(18, 30))
    # 09:00-13:00 is 240, 13:45-18:30 is 285.
    assert analysis.worked == Duration(240 + 285)


def test_the_closed_segment_is_marked_inferred_not_measured() -> None:
    """Every screen already renders an inferred end as repaired, and the voice engine
    refuses to be playful about one. Closing a day silently would launder a guess."""
    analysis = analyze_day(
        punches(("09:00", "in")), day=DAY, envelope=DayEnvelope(last_out=time(17, 30))
    )
    assert analysis.segments[-1].end_inferred
    assert any(issue.kind is IssueKind.INFERRED_OUT for issue in analysis.issues)


def test_a_closed_day_that_is_still_short_says_so() -> None:
    """The point of closing it: a day with a missing punch is usually a day needing one
    filed, and that cannot be offered while the day reads as ongoing."""
    analysis = analyze_day(
        punches(("09:00", "in")), day=DAY, envelope=DayEnvelope(last_out=time(13, 0))
    )

    assert analysis.state is DayState.COMPLETE
    assert analysis.early_exit
    assert analysis.swipe_request_needed


def test_today_is_never_closed_from_the_grid() -> None:
    """Today's dangling In is a live shift. Closing it would declare a departure in the
    middle of the afternoon — the mistake `analyze_day` already guards against elsewhere."""
    now = datetime.combine(DAY, time(14, 0))
    analysis = analyze_day(
        punches(("09:00", "in")), day=DAY, now=now, envelope=DayEnvelope(last_out=time(18, 30))
    )

    assert analysis.clocked_in
    assert analysis.state is DayState.INCOMPLETE
    assert analysis.last_out == now


def test_a_last_out_before_the_open_punch_is_refused() -> None:
    """A grid value that predates the dangling In cannot close it, and using it would
    produce a negative segment."""
    analysis = analyze_day(
        punches(("18:00", "in")), day=DAY, envelope=DayEnvelope(last_out=time(9, 0))
    )
    assert analysis.clocked_in


def test_a_clean_pair_is_still_extended_to_the_portals_last_out() -> None:
    """This test used to assert the opposite, and the opposite was the bug.

    A day that pairs cleanly looks finished, so nothing flagged it — but the portal counts
    to its own last-out, and where that is later the punch log is simply missing its tail.
    Believing the log over the portal is what reported a 15:21 departure for a day that
    ended at 18:10.
    """
    analysis = analyze_day(
        punches(("09:00", "in"), ("18:00", "out")),
        day=DAY,
        envelope=DayEnvelope(last_out=time(19, 0)),
    )
    assert analysis.last_out == datetime.combine(DAY, time(19, 0))
    assert analysis.segments[-1].end_inferred


# --- a break that was actually work --------------------------------------------------------


def test_a_flagged_gap_moves_minutes_from_break_to_worked() -> None:
    """A trip to another floor reads as a break to the punches and to the portal alike.
    Only the person who was there can say otherwise, and this is them saying it."""
    log = punches(("09:00", "in"), ("13:00", "out"), ("13:45", "in"), ("18:00", "out"))

    plain = analyze_day(log, day=DAY)
    adjusted = analyze_day(log, day=DAY, worked_gaps={time(13, 0): "helping downstairs"})

    assert plain.break_taken == Duration(45)
    assert adjusted.break_taken == Duration(0)
    # The same forty-five minutes, moved rather than invented.
    assert adjusted.worked == plain.worked + Duration(45)


def test_an_adjusted_day_says_it_was_adjusted() -> None:
    """No screen may present a corrected day with the confidence of a measured one."""
    log = punches(("09:00", "in"), ("13:00", "out"), ("13:45", "in"), ("18:00", "out"))
    adjusted = analyze_day(log, day=DAY, worked_gaps={time(13, 0): "helping downstairs"})

    assert adjusted.is_adjusted
    assert adjusted.adjusted_gaps == ((time(13, 0), "helping downstairs"),)
    assert not analyze_day(log, day=DAY).is_adjusted


def test_only_the_flagged_gap_is_reclassified() -> None:
    """No threshold, no heuristic: gaps the user has not spoken about stay breaks."""
    log = punches(
        ("09:00", "in"),
        ("11:00", "out"),
        ("11:20", "in"),  # the trip downstairs
        ("13:00", "out"),
        ("13:45", "in"),  # a real lunch
        ("18:00", "out"),
    )
    adjusted = analyze_day(log, day=DAY, worked_gaps={time(11, 0): "other office"})

    assert adjusted.break_taken == Duration(45)
    assert len(adjusted.segments) == 2


def test_flagging_a_gap_that_is_not_there_changes_nothing() -> None:
    """A re-sync rewrites the punches; a flag left pointing at a time that no longer begins
    a gap must be inert rather than wrong."""
    log = punches(("09:00", "in"), ("13:00", "out"), ("13:45", "in"), ("18:00", "out"))

    assert analyze_day(
        log, day=DAY, worked_gaps={time(15, 30): "stale flag"}
    ).break_taken == Duration(45)


def test_a_log_that_stops_short_of_the_portals_last_out_is_extended() -> None:
    """The silent one. The day pairs cleanly so nothing looks wrong, and the app reported a
    departure at 15:21 for a day the portal ended at 18:10 — nearly three hours missing from
    a figure that looked exact."""
    analysis = analyze_day(
        punches(("09:20", "in"), ("15:21", "out")),
        day=DAY,
        envelope=DayEnvelope(last_out=time(18, 10)),
    )

    assert analysis.last_out == datetime.combine(DAY, time(18, 10))
    assert analysis.worked == Duration(8 * 60 + 50)
    assert analysis.segments[-1].end_inferred


def test_the_extension_is_marked_so_no_screen_calls_it_measured() -> None:
    analysis = analyze_day(
        punches(("09:20", "in"), ("15:21", "out")),
        day=DAY,
        envelope=DayEnvelope(last_out=time(18, 10)),
    )
    issue = next(i for i in analysis.issues if i.kind is IssueKind.INFERRED_OUT)
    assert "attendance summary" in issue.message


def test_a_log_that_already_reaches_the_portals_last_out_is_untouched() -> None:
    analysis = analyze_day(
        punches(("09:20", "in"), ("18:10", "out")),
        day=DAY,
        envelope=DayEnvelope(last_out=time(18, 10)),
    )
    assert not analysis.segments[-1].end_inferred


def test_a_grid_last_out_earlier_than_the_log_never_shortens_the_day() -> None:
    """Trusting a stale grid over a real punch would delete work that was measured."""
    analysis = analyze_day(
        punches(("09:20", "in"), ("18:10", "out")),
        day=DAY,
        envelope=DayEnvelope(last_out=time(15, 0)),
    )
    assert analysis.last_out == datetime.combine(DAY, time(18, 10))


def test_today_is_never_extended_from_the_grid() -> None:
    """The grid's last-out for today is the latest swipe so far, not a clock-off."""
    now = datetime.combine(DAY, time(16, 0))
    analysis = analyze_day(
        punches(("09:20", "in"), ("15:21", "out")),
        day=DAY,
        now=now,
        envelope=DayEnvelope(last_out=time(18, 10)),
    )
    assert analysis.last_out == datetime.combine(DAY, time(15, 21))


def test_the_note_travels_with_the_flag() -> None:
    """ "A break I marked as work" read back three weeks later is a fact with its reason
    missing, and the reason is the part that survives."""
    log = punches(("09:00", "in"), ("13:00", "out"), ("13:45", "in"), ("18:00", "out"))
    adjusted = analyze_day(log, day=DAY, worked_gaps={time(13, 0): "second-floor handover"})

    assert adjusted.adjusted_gaps == ((time(13, 0), "second-floor handover"),)


def test_a_flag_with_no_note_is_still_a_flag() -> None:
    log = punches(("09:00", "in"), ("13:00", "out"), ("13:45", "in"), ("18:00", "out"))
    adjusted = analyze_day(log, day=DAY, worked_gaps={time(13, 0): ""})

    assert adjusted.is_adjusted
    assert adjusted.break_taken == Duration(0)


# --- the day's extent, against three real days -----------------------------------------
#
# These three are not invented. They are the punch logs and grid rows that were sitting in
# the user's own cache while the app reported the wrong figure for each, taken verbatim.
# Every one of them reconciles to the portal's own Tot. Hrs. and that is the assertion:
# agreeing with the portal is the whole requirement, and a span that adds up is the only
# way to know the agreement is real rather than coincidental.


def gross(analysis) -> int:  # type: ignore[no-untyped-def]
    return analysis.gross_span.minutes


def test_a_dangling_in_at_the_portals_own_last_out_does_not_leave_the_day_open() -> None:
    """5 August. The log ends on an In at 19:05 — exactly where the grid ends the day — so
    the close repair declined (it wanted *later*) and a day three weeks past reported
    "Still clocked in since 7:05 PM". A swipe on the way out, read as an open shift."""
    analysis = analyze_day(
        punches(
            ("09:13", "in"),
            ("13:23", "in"),
            ("18:47", "out"),
            ("19:05", "out"),
            ("19:05", "in"),
        ),
        day=DAY,
        envelope=DayEnvelope(time(9, 13), time(19, 5), Duration(592)),
    )

    assert not analysis.is_ongoing
    assert not analysis.clocked_in
    assert analysis.state is DayState.COMPLETE
    assert analysis.last_out == datetime.combine(DAY, time(19, 5))
    assert gross(analysis) == 592


def test_a_night_shift_is_closed_past_midnight() -> None:
    """11 August. The grid ends the day at 02:44 — the next morning. Combining that time
    with *this* day's date put the close thirteen hours before the dangling In, the guard
    refused it, and 2h54m of a night shift vanished while the day read as still open."""
    analysis = analyze_day(
        punches(("07:45", "in"), ("18:11", "out"), ("18:12", "in"), ("23:50", "in")),
        day=DAY,
        envelope=DayEnvelope(time(7, 45), time(2, 44), Duration(1139)),
    )

    assert not analysis.is_ongoing
    assert analysis.last_out == datetime.combine(DAY + timedelta(days=1), time(2, 44))
    assert gross(analysis) == 1139


def test_an_approved_swipe_request_moves_the_arrival() -> None:
    """14 August. An approved request set the grid's first-in to 09:00; the punch log still
    shows the 09:40 actually swiped, and nothing bounded the log by the grid. The app threw
    away a correction the user had got signed off — and since the finish line is measured
    from the arrival, it moved when they were told they could leave."""
    analysis = analyze_day(
        punches(("09:40", "in"), ("13:27", "out"), ("14:09", "in"), ("15:21", "out")),
        day=DAY,
        envelope=DayEnvelope(time(9, 0), time(18, 10), Duration(550)),
    )

    assert analysis.first_in == datetime.combine(DAY, time(9, 0))
    assert analysis.segments[0].start_inferred
    assert analysis.expected_out == datetime.combine(DAY, time(18, 0))
    assert gross(analysis) == 550


def test_a_second_consecutive_out_is_work_not_noise() -> None:
    """Also 14 August: 15:21 Out then 18:10 Out. The In between never landed. Discarding the
    second punch as an orphan is what left nearly three hours off a day that looked exact."""
    analysis = analyze_day(
        punches(
            ("09:40", "in"),
            ("13:27", "out"),
            ("14:09", "in"),
            ("15:21", "out"),
            ("18:10", "out"),
        ),
        day=DAY,
        envelope=DayEnvelope(time(9, 0), time(18, 10), Duration(550)),
    )

    assert analysis.last_out == datetime.combine(DAY, time(18, 10))
    assert gross(analysis) == 550
    assert not any(issue.kind is IssueKind.ORPHAN_OUT for issue in analysis.issues)


def test_an_out_beyond_the_portals_own_day_is_still_an_orphan() -> None:
    """The rule has a limit. A stray Out after the grid closed the day is not evidence of
    work; extending to it would invent hours the portal never counted."""
    analysis = analyze_day(
        punches(("09:00", "in"), ("17:00", "out"), ("21:00", "out")),
        day=DAY,
        envelope=DayEnvelope(time(9, 0), time(17, 0), Duration(480)),
    )

    assert analysis.last_out == datetime.combine(DAY, time(17, 0))
    assert any(issue.kind is IssueKind.ORPHAN_OUT for issue in analysis.issues)


def test_todays_arrival_is_repaired_but_todays_departure_is_not() -> None:
    """The two ends are not symmetrical. An arrival is settled the moment the day starts, so
    an approved request applies to today as much as to last week — but today's last-out is
    merely the latest swipe so far, and closing the day on it declares an early exit at
    lunchtime."""
    now = datetime.combine(DAY, time(14, 0))
    analysis = analyze_day(
        punches(("09:40", "in")),
        day=DAY,
        now=now,
        envelope=DayEnvelope(time(9, 0), time(9, 40), Duration(40)),
    )

    assert analysis.first_in == datetime.combine(DAY, time(9, 0))
    assert analysis.clocked_in
    assert analysis.last_out == now


def test_a_span_that_still_does_not_reconcile_is_reported() -> None:
    """The backstop. Every way this has been wrong showed up as a span that disagreed with
    the portal's own total and nothing was looking, so each defect took a user report to
    find. A shape nobody anticipated is now a visible discrepancy, not a confident number."""
    analysis = analyze_day(
        punches(("09:00", "in"), ("17:00", "out")),
        day=DAY,
        envelope=DayEnvelope(time(9, 0), time(17, 0), Duration(600)),
    )

    issue = next(i for i in analysis.issues if i.kind is IssueKind.SPAN_MISMATCH)
    assert "2h is unaccounted for" in issue.message
    assert analysis.is_repaired


def test_a_day_that_reconciles_says_nothing() -> None:
    """The check has to be quiet on the ordinary case or it is just noise."""
    analysis = analyze_day(
        punches(*FULL_DAY), day=DAY, envelope=DayEnvelope(time(9, 0), time(18, 0), Duration(540))
    )

    assert not any(i.kind is IssueKind.SPAN_MISMATCH for i in analysis.issues)
    assert not analysis.is_repaired


# --- the two notifications that could never fire ----------------------------------------


def test_leaving_early_today_is_an_early_exit_once_the_day_is_spent() -> None:
    """The bug behind "I only ever get two notifications".

    ``early_exit`` asked for ``DayState.COMPLETE``, and today is forced INCOMPLETE whenever
    work is owed — so for today the condition was unsatisfiable, and the short-hours warning
    and the swipe-request nudge were Settings toggles wired to nothing. Notifications are
    only ever evaluated against today, so neither could reach anyone.
    """
    analysis = analyze_day(
        punches(("09:00", "in"), ("13:00", "out"), ("14:00", "in"), ("17:00", "out")),
        day=DAY,
        now=at("18:30"),
    )

    assert analysis.early_exit
    assert analysis.swipe_request_needed
    assert {InsightKind.EARLY_EXIT, InsightKind.SWIPE_NEEDED} <= kinds(analysis)


def test_lunchtime_today_is_still_not_an_early_exit() -> None:
    """The rule this replaces was right about the thing it was guarding. Clocked out at one
    o'clock with hours owed is a lunch break, and calling it a departure asked people to file
    a swipe request in the middle of a day they were still working."""
    analysis = analyze_day(punches(("09:00", "in"), ("13:00", "out")), day=DAY, now=at("13:30"))

    assert not analysis.early_exit
    assert not analysis.swipe_request_needed
    assert InsightKind.STILL_WORKING in kinds(analysis)


def test_someone_still_clocked_in_has_not_left_early() -> None:
    """Late in the day and short of the target, but the shift is open — they are working."""
    analysis = analyze_day(punches(("09:00", "in")), day=DAY, now=at("18:30"))
    assert not analysis.early_exit


def test_an_early_exit_is_not_also_told_how_long_it_has_left() -> None:
    """Telling someone who went home at five how much longer they have to work answers a
    question they stopped asking."""
    analysis = analyze_day(punches(("09:00", "in"), ("16:00", "out")), day=DAY, now=at("18:30"))

    assert InsightKind.EARLY_EXIT in kinds(analysis)
    assert InsightKind.STILL_WORKING not in kinds(analysis)


def test_a_long_lunch_pushes_back_when_leaving_counts_as_early() -> None:
    """The threshold is the break-adjusted finish, not the flat one — an over-long lunch
    moves the end of the day, so it moves the point where going home is leaving early."""
    log = punches(("09:00", "in"), ("12:00", "out"), ("14:00", "in"), ("17:30", "out"))

    assert not analyze_day(log, day=DAY, now=at("18:15")).early_exit
    assert analyze_day(log, day=DAY, now=at("19:15")).early_exit


def test_a_grid_only_today_is_never_an_early_exit() -> None:
    """Its last-out is the latest swipe so far, never evidence anybody went home."""
    analysis = analyze_day(
        punches(("09:00", "in"), ("16:00", "out")), day=DAY, now=at("18:30"), grid_only=True
    )
    assert not analysis.early_exit
