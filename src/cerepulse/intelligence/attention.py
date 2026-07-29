"""Which days need the user to do something about them.

Three parts of the Attendance screen ask this question — the row highlight, the "needs
attention" filter, and the heatmap ring — so it is answered once, here, rather than three
times in the view with three slightly different answers.

The bar is deliberately high. A screen that highlights a third of the month teaches people
to ignore the highlight, so a day qualifies only when there is a concrete thing to do about
it: file a request, correct a punch, or chase one that was turned down. A day that is merely
below target but already has a request filed is *handled*, not outstanding.

Today is never flagged. It is not short, it is unfinished.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from cerepulse.intelligence.day import DayAnalysis, DayState
from cerepulse.intelligence.policy import ShiftPolicy
from cerepulse.intelligence.segments import IssueKind
from cerepulse.models.attendance import AttendanceDay
from cerepulse.models.swipe import SwipeRequest, SwipeStatus
from cerepulse.models.values import Duration

#: A shortfall under this is not worth acting on. Live data threw up a day one minute below
#: target; nobody files a swipe request for a minute, and flagging it red trains the eye to
#: skip the flag.
SHORTFALL_TOLERANCE = Duration(15)


class AttentionKind(Enum):
    """Why a day is outstanding. Ordered by how much it wants doing."""

    REQUEST_REJECTED = "request_rejected"
    UNMEASURED = "unmeasured"
    MISSING_PUNCH = "missing_punch"
    SHORT_NO_REQUEST = "short_no_request"


@dataclass(frozen=True, slots=True)
class Attention:
    """One day that wants something done about it."""

    day: date
    kind: AttentionKind
    reason: str


def find_attention(
    days: list[AttendanceDay],
    *,
    policy: ShiftPolicy | None = None,
    analyses: dict[date, DayAnalysis] | None = None,
    swipes: dict[date, list[SwipeRequest]] | None = None,
    today: date | None = None,
) -> dict[date, Attention]:
    """Return the outstanding days, keyed by date.

    At most one entry per day, taking the most pressing reason. A day with both a missing
    punch and a shortfall has one thing wrong with it, and listing it twice would overstate
    how much of the month is broken.
    """
    policy = policy or ShiftPolicy.default()
    analyses = analyses or {}
    swipes = swipes or {}
    found: dict[date, Attention] = {}

    for day in days:
        if today is not None and day.day >= today:
            # Today is unfinished, not short; tomorrow has not happened.
            continue
        attention = _classify(day, policy, analyses.get(day.day), swipes.get(day.day, []))
        if attention is not None:
            found[day.day] = attention
    return found


def _classify(
    day: AttendanceDay,
    policy: ShiftPolicy,
    analysis: DayAnalysis | None,
    requests: list[SwipeRequest],
) -> Attention | None:
    if any(request.status is SwipeStatus.REJECTED for request in requests):
        return Attention(
            day.day,
            AttentionKind.REQUEST_REJECTED,
            "A swipe request for this day was rejected.",
        )

    if not day.status.counts_as_worked:
        return None

    if _is_unmeasured(day, analysis):
        return Attention(
            day.day,
            AttentionKind.UNMEASURED,
            "Marked as worked, but the portal holds no punches or hours for it.",
        )

    if analysis is not None and any(
        issue.kind in {IssueKind.INFERRED_OUT, IssueKind.ORPHAN_OUT} for issue in analysis.issues
    ):
        return Attention(
            day.day,
            AttentionKind.MISSING_PUNCH,
            "A punch is missing; the hours shown were inferred.",
        )

    shortfall = policy.work_target - _worked(day, policy, analysis)
    if shortfall > SHORTFALL_TOLERANCE and not _covered(requests):
        return Attention(
            day.day,
            AttentionKind.SHORT_NO_REQUEST,
            f"Short by {shortfall} with no request filed.",
        )
    return None


def _covered(requests: list[SwipeRequest]) -> bool:
    """Whether a live request already accounts for the day.

    A cancelled or rejected one does not: the shortfall is back to being the user's problem.
    """
    return any(
        request.status in {SwipeStatus.IN_PROCESS, SwipeStatus.APPROVED} for request in requests
    )


def _worked(day: AttendanceDay, policy: ShiftPolicy, analysis: DayAnalysis | None) -> Duration:
    if analysis is not None and analysis.state is DayState.COMPLETE:
        return analysis.worked
    # Grid-only: Tot. Hrs. is the gross span, so the break allowance comes off to make it
    # comparable with the target.
    span = day.total_hours - policy.break_target
    return span if span.minutes > 0 else Duration(0)


def _is_unmeasured(day: AttendanceDay, analysis: DayAnalysis | None) -> bool:
    if analysis is not None and analysis.state is not DayState.EMPTY:
        return False
    return (
        day.total_hours.minutes == 0
        and day.first_in is None
        and day.last_out is None
        and not day.punches
    )


def swipe_index(requests: list[SwipeRequest]) -> dict[date, list[SwipeRequest]]:
    """Group requests by the day they are for. A day can carry both an In and an Out."""
    grouped: dict[date, list[SwipeRequest]] = {}
    for request in requests:
        grouped.setdefault(request.for_date, []).append(request)
    return grouped


def swipe_state(requests: list[SwipeRequest]) -> SwipeStatus | None:
    """The one status worth showing for a day, when it carries several requests.

    A rejected request is what the user has to act on, so it outranks an approved one filed
    for the other punch; a pending one outranks an approved one for the same reason.
    """
    if not requests:
        return None
    for status in (SwipeStatus.REJECTED, SwipeStatus.IN_PROCESS, SwipeStatus.APPROVED):
        if any(request.status is status for request in requests):
            return status
    return requests[0].status


__all__ = [
    "Attention",
    "AttentionKind",
    "find_attention",
    "swipe_index",
    "swipe_state",
]
