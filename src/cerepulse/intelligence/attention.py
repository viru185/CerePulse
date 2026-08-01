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
from cerepulse.intelligence.insights import Insight, InsightKind, Severity
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


def duplicate_requests(
    requests: list[SwipeRequest],
) -> dict[tuple[date, str], list[SwipeRequest]]:
    """Live requests filed more than once for the same day and the same punch.

    The portal exposes no request id, no filed date and no approver, so a duplicate can only
    be recognised by what it is *for*. That is enough for the case that actually happens:
    a second request filed because the first appeared not to go through, which then sits in
    the approver's queue as two identical asks.

    Cancelled and rejected ones are excluded — a re-filed request after a rejection is the
    correct response to it, not a duplicate of it.
    """
    grouped: dict[tuple[date, str], list[SwipeRequest]] = {}
    for request in requests:
        if request.status in {SwipeStatus.CANCELLED, SwipeStatus.REJECTED}:
            continue
        key = (request.for_date, request.direction.strip().lower())
        grouped.setdefault(key, []).append(request)
    return {key: found for key, found in grouped.items() if len(found) > 1}


@dataclass(frozen=True, slots=True)
class StatusChange:
    """A filed request that has been decided since the last sync."""

    request: SwipeRequest
    was: SwipeStatus

    @property
    def verb(self) -> str:
        return {
            SwipeStatus.APPROVED: "approved",
            SwipeStatus.REJECTED: "rejected",
            SwipeStatus.CANCELLED: "cancelled",
        }.get(self.request.status, "updated")


#: Statuses that mean the request is settled. Arriving at one of these is the news.
_DECIDED = {SwipeStatus.APPROVED, SwipeStatus.REJECTED, SwipeStatus.CANCELLED}


def status_changes(before: list[SwipeRequest], after: list[SwipeRequest]) -> list[StatusChange]:
    """Requests that were pending last time and are settled now.

    Diffing two fetches is the only way to know: the portal's grid carries no filed date, no
    request id and no approver, so there is nothing in a single snapshot that says when — or
    whether — anything moved.

    Identity is what the request is *for*, since there is no id to key on. A day carrying two
    requests for the same punch is genuinely ambiguous, and both are then treated as the same
    request; that mistake is harmless here, because the only consequence is one notification
    instead of two about the same day.

    A newly appeared request is not a change. The user filed it, so they already know.
    """
    seen = {_identity(request): request for request in before}
    changes: list[StatusChange] = []
    for request in after:
        previous = seen.get(_identity(request))
        if previous is None or previous.status == request.status:
            continue
        if request.status in _DECIDED and previous.status not in _DECIDED:
            changes.append(StatusChange(request, previous.status))
    return changes


def _identity(request: SwipeRequest) -> tuple[date, str, str, str]:
    return (
        request.for_date,
        request.direction.strip().lower(),
        str(request.in_time or ""),
        str(request.out_time or ""),
    )


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


def decision_insight(changes: list[StatusChange]) -> Insight | None:
    """One insight for the whole batch, rather than one per request.

    An approver clearing a queue settles several at once, and three toasts about three
    requests is three interruptions for one piece of news. Batching also sidesteps the
    once-a-day dedup, which keys on the kind and would otherwise silently drop the second
    and third anyway.
    """
    if not changes:
        return None

    approved = sum(1 for change in changes if change.request.status is SwipeStatus.APPROVED)
    rejected = sum(1 for change in changes if change.request.status is SwipeStatus.REJECTED)

    if len(changes) == 1:
        change = changes[0]
        return Insight(
            InsightKind.SWIPE_DECIDED,
            Severity.WARNING if rejected else Severity.SUCCESS,
            f"Swipe request {change.verb}",
            f"{change.request.for_date:%d %b} ({change.request.direction}) was "
            f"{change.verb}." + (f" {change.request.remark}" if change.request.remark else ""),
        )

    parts = [
        f"{count} {word}"
        for count, word in ((approved, "approved"), (rejected, "rejected"))
        if count
    ]
    settled = ", ".join(parts) or "settled"
    return Insight(
        InsightKind.SWIPE_DECIDED,
        Severity.WARNING if rejected else Severity.SUCCESS,
        f"{len(changes)} swipe requests decided",
        f"{settled} since the last sync.",
    )


__all__ = [
    "Attention",
    "AttentionKind",
    "StatusChange",
    "decision_insight",
    "duplicate_requests",
    "find_attention",
    "status_changes",
    "swipe_index",
    "swipe_state",
]
