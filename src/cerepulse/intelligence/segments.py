"""Pair raw punches into work segments.

Real punch logs are messy. The live capture for 28-Jul reads ``In, Out, In, In, Out, In,
Out`` — a missed Out punch in the middle. Discarding such days would hide exactly the
information the user needs, so the pairing repairs what it can and records every repair as
an issue the UI can surface.

Rules, ported from ninetofive:

* ``In`` while a segment is already open  -> infer an ``Out`` at the newer ``In``, warn.
* ``Out`` with no open segment            -> skip it, warn.
* an ``In`` still open at the end         -> the shift is ongoing; close it at ``now``.

Those three describe the log in isolation, which is not enough. For a day that is over the
portal's own grid row — see :class:`DayEnvelope` — is the authority on how far the day
reached, and these punches only detail what happened inside it.

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
    #: The log began after the portal's own first-in, so the arrival was repaired from it.
    INFERRED_IN = "inferred_in"
    ORPHAN_OUT = "orphan_out"
    ONGOING = "ongoing"
    NO_PUNCHES = "no_punches"
    #: The times came from the monthly grid, not from a punch log.
    GRID_ONLY = "grid_only"
    #: Repaired as far as the log allows and the span still disagrees with the portal's
    #: own total. Reported rather than hidden: a shape nobody anticipated must never be
    #: presented as a measurement.
    SPAN_MISMATCH = "span_mismatch"


#: The punch log itself needed fixing, so the day is worth a human look and any shortfall
#: derived from it is a reconstruction rather than a measurement. ``GRID_ONLY`` is absent
#: deliberately: a day with no log fetched is not a day with a broken one.
LOG_REPAIRS = frozenset(
    {
        IssueKind.INFERRED_OUT,
        IssueKind.INFERRED_IN,
        IssueKind.ORPHAN_OUT,
        IssueKind.SPAN_MISMATCH,
    }
)

#: Any figure came from somewhere other than a punch. Defined once and shared, because the
#: three places that ask "was this day measured?" must never drift apart on the answer.
REPAIRED = LOG_REPAIRS | {IssueKind.GRID_ONLY}


@dataclass(frozen=True, slots=True)
class PunchIssue:
    """One repair or notable condition found while pairing."""

    kind: IssueKind
    message: str
    at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DayEnvelope:
    """What the portal's own grid row says the day's extent was.

    For a day that is over this is the authority and the punch log is merely the detail
    inside it. The log routinely stops short of the grid at either end — an approved swipe
    request corrects the grid but never the log, and trailing punches go missing outright —
    so a day measured from the log alone under-reports, silently and by hours.

    ``total`` is the portal's ``Tot. Hrs.``, kept so the repaired span can be checked against
    it rather than trusted.
    """

    first_in: time | None = None
    last_out: time | None = None
    total: Duration | None = None


@dataclass(frozen=True, slots=True)
class WorkSegment:
    """A continuous stretch of work between an In and an Out."""

    start: datetime
    end: datetime
    #: True when the closing Out was not a real punch — either inferred from a following In
    #: or, for an ongoing shift, the current time.
    end_inferred: bool = False
    #: True when the opening In was not a real punch, i.e. the arrival came from the grid.
    start_inferred: bool = False

    @property
    def duration(self) -> Duration:
        minutes = int((self.end - self.start).total_seconds() // 60)
        return Duration(max(0, minutes))


@dataclass(frozen=True, slots=True)
class WorkedGap:
    """A gap the user told us was really work, kept with its own extent.

    Applying the flag joins the segments either side into one — right for the arithmetic, and
    it destroys the only record of where the gap was, which is why the timeline could draw
    nothing but a line at its start. Carried alongside so a screen can show the stretch it
    covers and what the user said they were doing.
    """

    start: datetime
    end: datetime
    note: str


@dataclass(frozen=True, slots=True)
class Pairing:
    """The result of pairing one day's punches."""

    segments: tuple[WorkSegment, ...] = ()
    issues: tuple[PunchIssue, ...] = ()
    #: True when the day ended with an open In, i.e. the person is still clocked in.
    ongoing: bool = False
    #: Gaps merged into the segments above because the user marked them as work.
    worked_spans: tuple[WorkedGap, ...] = ()

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
    envelope: DayEnvelope | None = None,
    worked_gaps: Mapping[time, str] | None = None,
) -> Pairing:
    """Pair a day's punches into work segments, bounded by what the portal recorded.

    ``now`` closes an ongoing shift and is injected rather than read from the clock, so every
    in-progress case is deterministically testable.

    ``envelope`` is the portal's own grid row. For a day that is over it is the authority on
    the day's extent and these punches are only the detail inside it — the log stops short of
    the grid routinely, at both ends, and a day measured from the log alone loses hours
    without ever looking wrong. Four shapes, every one of them found in live data:

    * The log **starts late**. An approved swipe request corrects the grid and never the log,
      so a 09:00 arrival the user had signed off still read as the 09:40 they actually
      swiped. That is the costly one: the finish line is measured from the arrival, so it
      moves *when can I leave*.
    * The log ends on an **In**. The portal closes the day anyway, so the open segment closes
      at its last-out — otherwise a day three weeks past still reports "still clocked in".
    * The log ends on an **Out earlier than the portal's last-out**. Silent, because the day
      pairs cleanly: 15:21 reported for a day the portal ended at 18:10.
    * A **second consecutive Out**. Not noise. The In between never landed, the work happened,
      and discarding the punch is what lost it.

    Every repair is marked inferred, so no screen presents it as measured, and anything the
    log still cannot account for is reported rather than quietly absorbed.

    The closing repairs are for a finished day only — today's last-out is the latest swipe so
    far rather than a clock-off, so ``now`` disables them. The arrival repair still applies,
    because an arrival is settled the moment the day starts.
    """
    if not punches:
        return Pairing(
            issues=(PunchIssue(IssueKind.NO_PUNCHES, "No punches recorded for this day."),)
        )

    stamps = _anchor_to_day(punches, day)
    opens_at, closes_at = _anchor_envelope(envelope, day)
    if now is not None:
        closes_at = None

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
        elif open_at is None:
            if _continues(segments, moment, closes_at):
                last = segments[-1]
                segments[-1] = WorkSegment(
                    last.start,
                    moment,
                    end_inferred=True,
                    start_inferred=last.start_inferred,
                )
                issues.append(
                    PunchIssue(
                        IssueKind.INFERRED_OUT,
                        f"Missing In punch before {_clock(moment)}; counted the work "
                        f"through to it.",
                        moment,
                    )
                )
            else:
                issues.append(
                    PunchIssue(
                        IssueKind.ORPHAN_OUT,
                        f"Out punch at {_clock(moment)} has no matching In; ignored.",
                        moment,
                    )
                )
        else:
            segments.append(WorkSegment(open_at, moment))
            open_at = None

    ongoing = open_at is not None
    if open_at is not None and closes_at is not None and closes_at >= open_at:
        # A finished day the portal closed for us. Not ongoing: the day is over, and saying
        # "still clocked in" about last Tuesday is plainly wrong.
        ongoing = False
        if closes_at > open_at:
            segments.append(WorkSegment(open_at, closes_at, end_inferred=True))
            issues.append(
                PunchIssue(
                    IssueKind.INFERRED_OUT,
                    f"No Out punch was recorded; closed at {_clock(closes_at)} from the "
                    f"attendance summary.",
                    closes_at,
                )
            )
        else:
            # The In landed exactly where the portal ended the day: a swipe on the way out,
            # not the start of more work. The day already reaches that far.
            issues.append(
                PunchIssue(
                    IssueKind.ORPHAN_OUT,
                    f"In punch at {_clock(open_at)} is where the attendance summary ends "
                    f"the day; ignored.",
                    open_at,
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
    elif closes_at is not None and segments and closes_at > segments[-1].end:
        # The log paired cleanly but stopped short of where the portal ended the day. The
        # trailing Out punches simply are not in the log; the grid counted them anyway.
        last = segments[-1]
        issues.append(
            PunchIssue(
                IssueKind.INFERRED_OUT,
                f"The punch log ends at {_clock(last.end)}, but the attendance summary "
                f"records {_clock(closes_at)}; the day was measured to the summary.",
                closes_at,
            )
        )
        segments[-1] = WorkSegment(
            last.start, closes_at, end_inferred=True, start_inferred=last.start_inferred
        )

    if segments and opens_at is not None and opens_at < segments[0].start:
        # The arrival the portal recorded predates the first punch in the log — most often an
        # approved swipe request, which the grid honours and the log never shows.
        first = segments[0]
        issues.append(
            PunchIssue(
                IssueKind.INFERRED_IN,
                f"The punch log starts at {_clock(first.start)}, but the attendance summary "
                f"records {_clock(opens_at)}; the day was measured from the summary.",
                opens_at,
            )
        )
        segments[0] = WorkSegment(
            opens_at, first.end, end_inferred=first.end_inferred, start_inferred=True
        )

    worked_spans: tuple[WorkedGap, ...] = ()
    if worked_gaps:
        segments, worked_spans = _merge_worked_gaps(segments, worked_gaps)

    issues.extend(_span_check(segments, envelope, settled=closes_at is not None and not ongoing))

    return Pairing(
        segments=tuple(segments),
        issues=tuple(issues),
        ongoing=ongoing,
        worked_spans=worked_spans,
    )


def _continues(segments: list[WorkSegment], moment: datetime, closes_at: datetime | None) -> bool:
    """Whether an unmatched Out is the missing tail of the segment before it.

    Two Outs in a row mean the In between them never landed. The portal counts that work
    regardless, so discarding the punch — which is all this module used to do with it — threw
    away real hours. Only for a finished day, and only as far as the portal itself went: an
    Out beyond the grid's own last-out is not evidence of anything.
    """
    if closes_at is None or not segments:
        return False
    return segments[-1].end < moment <= closes_at


def _anchor_envelope(
    envelope: DayEnvelope | None, day: date
) -> tuple[datetime | None, datetime | None]:
    """Give the grid's own times a date, rolling the close forward when the clock goes back.

    The same rule :func:`_anchor_to_day` applies to punches has to apply here. A shift ending
    at 02:44 ends on the *following* day; combining that time with this day's date puts the
    end of the shift seventeen hours before its start, the repair then declines because it
    would move the day backwards, and a night shift silently loses everything past midnight.
    """
    if envelope is None:
        return None, None

    opens_at = datetime.combine(day, envelope.first_in) if envelope.first_in else None
    if envelope.last_out is None:
        return opens_at, None

    closes_at = datetime.combine(day, envelope.last_out)
    if closes_at < (opens_at or datetime.combine(day, time.min)):
        closes_at += timedelta(days=1)
    return opens_at, closes_at


def _span_check(
    segments: list[WorkSegment], envelope: DayEnvelope | None, *, settled: bool
) -> list[PunchIssue]:
    """Check the repaired span against the portal's own total, and say so when it disagrees.

    Every way this module has been wrong — a lost arrival, a night shift cut at midnight, a
    discarded trailing Out — showed up right here as a span that did not reconcile, and
    nothing was looking. Reporting the remainder is what turns the next unanticipated shape
    into a visible discrepancy instead of a confident wrong number.
    """
    if not settled or envelope is None or envelope.total is None or not segments:
        return []

    measured = int((segments[-1].end - segments[0].start).total_seconds() // 60)
    drift = measured - envelope.total.minutes
    if abs(drift) <= 1:
        return []
    return [
        PunchIssue(
            IssueKind.SPAN_MISMATCH,
            f"This day spans {Duration(max(0, measured))}, but the attendance summary "
            f"records {envelope.total}; {Duration(abs(drift))} is unaccounted for.",
        )
    ]


def _merge_worked_gaps(
    segments: list[WorkSegment], worked_gaps: Mapping[time, str]
) -> tuple[list[WorkSegment], tuple[WorkedGap, ...]]:
    """Join two segments across a gap the user has told us was work.

    The punches cannot tell a lunch from a trip to another floor — both are an Out followed
    by an In — and neither can the portal. Only the person who was there knows, so this
    honours what they said and nothing more: no heuristic, no duration threshold, no
    inference about gaps they have not spoken about.

    Matched on the clock time of the Out that began the gap, which is what the user clicked
    and what the flag is stored under. Each merged gap is returned with its own extent,
    because joining the segments is precisely what destroys the record of where it was.
    """
    if not segments:
        return segments, ()

    merged = [segments[0]]
    spans: list[WorkedGap] = []
    for segment in segments[1:]:
        previous = merged[-1]
        note = worked_gaps.get(previous.end.time())
        if note is not None:
            # One continuous stretch of work: the gap between them was never a break.
            spans.append(WorkedGap(previous.end, segment.start, note))
            merged[-1] = WorkSegment(
                previous.start,
                segment.end,
                end_inferred=segment.end_inferred,
                start_inferred=previous.start_inferred,
            )
            continue
        merged.append(segment)
    return merged, tuple(spans)


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
