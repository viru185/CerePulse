"""Pair raw punches into work segments.

Real punch logs are messy. The live capture for 28-Jul reads ``In, Out, In, In, Out, In,
Out`` — a missed Out punch in the middle. Discarding such days would hide exactly the
information the user needs, so the pairing repairs what it can and records every repair as
an issue the UI can surface.

Rules, ported from ninetofive:

* ``In`` while a segment is already open  -> infer an ``Out`` at the newer ``In``, warn.
* ``Out`` with no open segment            -> skip it, warn.
* an ``In`` still open at the end         -> the shift is ongoing; close it at ``now``.

Punch times carry no date, so they are anchored to the day being analyzed. A time that goes
backwards is treated as having crossed midnight, which keeps overnight shifts monotonic
instead of producing negative segments.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum

from cerepulse.models.attendance import Punch, PunchDirection
from cerepulse.models.values import Duration


class IssueKind(Enum):
    """Why a punch log needed repair."""

    INFERRED_OUT = "inferred_out"
    ORPHAN_OUT = "orphan_out"
    ONGOING = "ongoing"
    NO_PUNCHES = "no_punches"
    #: The times came from the monthly grid, not from a punch log.
    GRID_ONLY = "grid_only"


@dataclass(frozen=True, slots=True)
class PunchIssue:
    """One repair or notable condition found while pairing."""

    kind: IssueKind
    message: str
    at: datetime | None = None


@dataclass(frozen=True, slots=True)
class WorkSegment:
    """A continuous stretch of work between an In and an Out."""

    start: datetime
    end: datetime
    #: True when the closing Out was not a real punch — either inferred from a following In
    #: or, for an ongoing shift, the current time.
    end_inferred: bool = False

    @property
    def duration(self) -> Duration:
        minutes = int((self.end - self.start).total_seconds() // 60)
        return Duration(max(0, minutes))


@dataclass(frozen=True, slots=True)
class Pairing:
    """The result of pairing one day's punches."""

    segments: tuple[WorkSegment, ...] = ()
    issues: tuple[PunchIssue, ...] = ()
    #: True when the day ended with an open In, i.e. the person is still clocked in.
    ongoing: bool = False

    @property
    def worked(self) -> Duration:
        total = Duration(0)
        for segment in self.segments:
            total = total + segment.duration
        return total

    @property
    def break_taken(self) -> Duration:
        """Sum of the gaps between consecutive segments."""
        total = Duration(0)
        for previous, following in zip(self.segments, self.segments[1:], strict=False):
            minutes = int((following.start - previous.end).total_seconds() // 60)
            if minutes > 0:
                total = total + Duration(minutes)
        return total

    @property
    def first_in(self) -> datetime | None:
        return self.segments[0].start if self.segments else None

    @property
    def last_out(self) -> datetime | None:
        return self.segments[-1].end if self.segments else None

    @property
    def gross_span(self) -> Duration:
        """First in to last out, i.e. worked plus breaks. Matches the portal's Tot. Hrs."""
        if not self.segments:
            return Duration(0)
        first, last = self.segments[0].start, self.segments[-1].end
        return Duration(max(0, int((last - first).total_seconds() // 60)))


def pair_punches(
    punches: list[Punch],
    *,
    day: date,
    now: datetime | None = None,
    close_at: time | None = None,
    worked_gaps: Mapping[time, str] | None = None,
) -> Pairing:
    """Pair a day's punches into work segments.

    ``now`` closes an ongoing shift and is injected rather than read from the clock, so every
    in-progress case is deterministically testable. It is only consulted when the last punch
    is an unmatched ``In``.

    ``close_at`` is the portal's own last-out for a day that is **over**, and it repairs two
    different shapes of incomplete punch log:

    * The log ends on an **In**. The portal still carries a last-out and a total — it closes
      the day even when the punch never landed — so the open segment is closed at it and the
      day is settled rather than ongoing.
    * The log ends on an **Out that is earlier than the portal's last-out**. This is the
      commoner one and it is silent: the day pairs cleanly, so nothing looks wrong, and the
      app confidently reported a departure at 15:21 for a day the portal ended at 18:10 —
      nearly three hours of work missing from a figure that looked exact.

    Either way the end is marked inferred, so no screen presents it as measured.

    Never pass it for today: today's last punch is a shift still being worked, and the grid's
    last-out there is merely the latest swipe so far rather than a clock-off.
    """
    if not punches:
        return Pairing(
            issues=(PunchIssue(IssueKind.NO_PUNCHES, "No punches recorded for this day."),)
        )

    stamps = _anchor_to_day(punches, day)
    segments: list[WorkSegment] = []
    issues: list[PunchIssue] = []
    open_at: datetime | None = None

    for punch, moment in stamps:
        if punch.direction is PunchDirection.IN:
            if open_at is not None:
                # Two Ins in a row: the Out between them was never recorded. Close the open
                # segment at this In so the work before it is not lost.
                segments.append(WorkSegment(open_at, moment, end_inferred=True))
                issues.append(
                    PunchIssue(
                        IssueKind.INFERRED_OUT,
                        f"Missing Out punch before {_clock(moment)}; "
                        f"assumed you left at {_clock(moment)}.",
                        moment,
                    )
                )
            open_at = moment
        else:
            if open_at is None:
                issues.append(
                    PunchIssue(
                        IssueKind.ORPHAN_OUT,
                        f"Out punch at {_clock(moment)} has no matching In; ignored.",
                        moment,
                    )
                )
                continue
            segments.append(WorkSegment(open_at, moment))
            open_at = None

    ongoing = open_at is not None
    if open_at is not None and now is None and close_at is not None:
        # A finished day the portal closed for us. Not ongoing: the day is over, and saying
        # "still clocked in" about last Tuesday is plainly wrong.
        closing = datetime.combine(open_at.date(), close_at)
        if closing > open_at:
            ongoing = False
            segments.append(WorkSegment(open_at, closing, end_inferred=True))
            issues.append(
                PunchIssue(
                    IssueKind.INFERRED_OUT,
                    f"No Out punch was recorded; closed at {_clock(closing)} from the "
                    f"attendance summary.",
                    closing,
                )
            )
            open_at = None

    if open_at is not None:
        current = now or datetime.combine(day, open_at.time())
        if current < open_at:
            current = open_at
        segments.append(WorkSegment(open_at, current, end_inferred=True))
        issues.append(
            PunchIssue(
                IssueKind.ONGOING,
                f"Still clocked in since {_clock(open_at)}.",
                open_at,
            )
        )
    elif now is None and close_at is not None and segments:
        # The log paired cleanly but stopped short of where the portal ended the day. The
        # trailing Out punches simply are not in the log; the grid counted them anyway.
        last = segments[-1]
        closing = datetime.combine(last.end.date(), close_at)
        if closing > last.end:
            segments[-1] = WorkSegment(last.start, closing, end_inferred=True)
            issues.append(
                PunchIssue(
                    IssueKind.INFERRED_OUT,
                    f"The punch log ends at {_clock(last.end)}, but the attendance summary "
                    f"records {_clock(closing)}; the day was measured to the summary.",
                    closing,
                )
            )

    if worked_gaps:
        segments = _merge_worked_gaps(segments, worked_gaps)

    return Pairing(segments=tuple(segments), issues=tuple(issues), ongoing=ongoing)


def _merge_worked_gaps(
    segments: list[WorkSegment], worked_gaps: Mapping[time, str]
) -> list[WorkSegment]:
    """Join two segments across a gap the user has told us was work.

    The punches cannot tell a lunch from a trip to another floor — both are an Out followed
    by an In — and neither can the portal. Only the person who was there knows, so this
    honours what they said and nothing more: no heuristic, no duration threshold, no
    inference about gaps they have not spoken about.

    Matched on the clock time of the Out that began the gap, which is what the user clicked
    and what the flag is stored under.
    """
    if not segments:
        return segments

    merged = [segments[0]]
    for segment in segments[1:]:
        previous = merged[-1]
        if previous.end.time() in worked_gaps:
            # One continuous stretch of work: the gap between them was never a break.
            merged[-1] = WorkSegment(previous.start, segment.end, end_inferred=segment.end_inferred)
            continue
        merged.append(segment)
    return merged


def _anchor_to_day(punches: list[Punch], day: date) -> list[tuple[Punch, datetime]]:
    """Attach a date to each punch time, rolling forward when the clock goes backwards."""
    anchored: list[tuple[Punch, datetime]] = []
    offset = 0
    previous: datetime | None = None

    for punch in punches:
        moment = datetime.combine(day + timedelta(days=offset), punch.at)
        if previous is not None and moment < previous:
            offset += 1
            moment = datetime.combine(day + timedelta(days=offset), punch.at)
        anchored.append((punch, moment))
        previous = moment
    return anchored


def _clock(moment: datetime) -> str:
    return moment.strftime("%I:%M %p").lstrip("0")
