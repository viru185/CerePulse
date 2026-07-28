"""Punch pairing, including the repairs real logs require."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from cerepulse.intelligence.segments import IssueKind, pair_punches
from cerepulse.models.attendance import Punch, PunchDirection
from tests.intelligence.conftest import DAY, at, punches


def test_clean_day_pairs_into_segments() -> None:
    result = pair_punches(
        punches(("09:00", "in"), ("13:00", "out"), ("14:00", "in"), ("18:00", "out")),
        day=DAY,
    )
    assert len(result.segments) == 2
    assert result.worked.as_clock() == "8:00"
    assert result.break_taken.as_clock() == "1:00"
    assert result.gross_span.as_clock() == "9:00"
    assert not result.ongoing
    assert result.issues == ()


def test_missing_out_is_inferred_from_the_next_in() -> None:
    """Two Ins in a row means an Out was never recorded; the work before it must survive."""
    result = pair_punches(punches(("09:00", "in"), ("12:00", "in"), ("18:00", "out")), day=DAY)

    assert len(result.segments) == 2
    assert result.segments[0].end == datetime.combine(DAY, time(12, 0))
    assert result.segments[0].end_inferred
    assert result.worked.as_clock() == "9:00"
    assert [issue.kind for issue in result.issues] == [IssueKind.INFERRED_OUT]


def test_inferred_out_creates_no_phantom_break() -> None:
    result = pair_punches(punches(("09:00", "in"), ("12:00", "in"), ("18:00", "out")), day=DAY)
    assert result.break_taken.minutes == 0


def test_orphan_out_is_skipped() -> None:
    result = pair_punches(punches(("09:00", "out"), ("10:00", "in"), ("18:00", "out")), day=DAY)

    assert len(result.segments) == 1
    assert result.worked.as_clock() == "8:00"
    assert [issue.kind for issue in result.issues] == [IssueKind.ORPHAN_OUT]


def test_ongoing_shift_closes_at_now() -> None:
    """now is injected, so an in-progress day is deterministic rather than clock-dependent."""
    result = pair_punches(punches(("09:00", "in")), day=DAY, now=at("14:30"))

    assert result.ongoing
    assert result.worked.as_clock() == "5:30"
    assert result.segments[-1].end_inferred
    assert [issue.kind for issue in result.issues] == [IssueKind.ONGOING]


def test_ongoing_after_a_break_accumulates_both_segments() -> None:
    result = pair_punches(
        punches(("09:00", "in"), ("13:00", "out"), ("13:30", "in")),
        day=DAY,
        now=at("17:00"),
    )
    assert result.worked.as_clock() == "7:30"
    assert result.break_taken.as_clock() == "0:30"
    assert result.ongoing


def test_now_before_the_open_punch_does_not_go_negative() -> None:
    result = pair_punches(punches(("09:00", "in")), day=DAY, now=at("08:00"))
    assert result.worked.minutes == 0


def test_no_punches_reports_an_issue() -> None:
    result = pair_punches([], day=DAY)
    assert result.segments == ()
    assert [issue.kind for issue in result.issues] == [IssueKind.NO_PUNCHES]
    assert result.worked.minutes == 0
    assert result.first_in is None


def test_overnight_shift_rolls_to_the_next_day() -> None:
    """A time that goes backwards has crossed midnight; segments must stay positive."""
    result = pair_punches(punches(("22:00", "in"), ("06:00", "out")), day=DAY)

    assert result.worked.as_clock() == "8:00"
    assert result.segments[0].start.date() == DAY
    assert result.segments[0].end.date() == DAY + timedelta(days=1)


def test_the_real_captured_anomaly() -> None:
    """28-Jul from the live portal: In, Out, In, In, Out, In, Out — a missed Out punch.

    The portal reported Tot. Hrs. 9.10 for this day, so the gross span must come to 9:10.
    """
    result = pair_punches(
        punches(
            ("09:21", "in"),
            ("09:48", "out"),
            ("09:54", "in"),
            ("13:53", "in"),
            ("17:05", "out"),
            ("17:06", "in"),
            ("18:31", "out"),
        ),
        day=DAY,
    )

    assert result.gross_span.as_clock() == "9:10"
    assert result.worked.as_clock() == "9:03"
    assert result.break_taken.as_clock() == "0:07"
    assert len(result.segments) == 4
    assert [issue.kind for issue in result.issues] == [IssueKind.INFERRED_OUT]


def test_gross_span_equals_worked_plus_break() -> None:
    result = pair_punches(
        punches(("09:00", "in"), ("12:00", "out"), ("12:45", "in"), ("18:00", "out")),
        day=DAY,
    )
    assert result.gross_span == result.worked + result.break_taken


def test_zero_length_segment_is_harmless() -> None:
    result = pair_punches(punches(("09:00", "in"), ("09:00", "out")), day=DAY)
    assert result.worked.minutes == 0


def test_punch_direction_parsing() -> None:
    assert PunchDirection.parse(" IN ") is PunchDirection.IN
    assert PunchDirection.parse("out") is PunchDirection.OUT


def test_segments_are_anchored_to_the_requested_day() -> None:
    other = date(2026, 1, 5)
    result = pair_punches(
        [Punch(at=time(9, 0), direction=PunchDirection.IN)],
        day=other,
        now=datetime.combine(other, time(10, 0)),
    )
    assert result.segments[0].start.date() == other
