"""Day analysis — the product's core calculation.

Pure: no network, no database, no Qt. Takes punches, a policy and an injected ``now``, and
returns everything the Today screen shows. ``now`` is a parameter rather than a clock read
so every in-progress case is deterministically testable.

Two deliberate upgrades over ninetofive's flat ``first_in + 9h``:

* :attr:`DayAnalysis.expected_out_break_adjusted` accounts for an over-long break
  (``first_in + work_target + max(break_target, break_taken)``). The flat span under-reports
  when someone takes a ninety-minute lunch, telling them to leave before they have actually
  worked eight hours.
* Every metric carries an :class:`Explanation` so the UI can answer "why this number?",
  including which punches were inferred or discarded.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import Enum

from cerepulse.intelligence.insights import (
    Action,
    ActionKind,
    Insight,
    InsightKind,
    Severity,
)
from cerepulse.intelligence.policy import ShiftPolicy
from cerepulse.intelligence.segments import (
    IssueKind,
    Pairing,
    PunchIssue,
    WorkSegment,
    pair_punches,
)
from cerepulse.models.attendance import Punch
from cerepulse.models.swipe import SwipeRequest, SwipeStatus
from cerepulse.models.values import Duration


class DayState(Enum):
    """Whether the day is finished."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"  # still clocked in
    EMPTY = "empty"  # no punches at all


@dataclass(frozen=True, slots=True)
class Explanation:
    """The arithmetic behind one metric, for the "why this number?" popover."""

    metric: str
    value: str
    formula: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DayAnalysis:
    """Everything derived from one day's punches."""

    day: date
    state: DayState
    first_in: datetime | None
    last_out: datetime | None
    expected_out: datetime | None
    expected_out_break_adjusted: datetime | None
    worked: Duration
    break_taken: Duration
    work_remaining: Duration
    break_remaining: Duration
    extra_worked: Duration
    gross_span: Duration
    early_exit: bool
    swipe_request_needed: bool
    segments: tuple[WorkSegment, ...] = ()
    issues: tuple[PunchIssue, ...] = ()
    insights: tuple[Insight, ...] = ()
    explanations: dict[str, Explanation] = field(default_factory=dict)

    @property
    def is_ongoing(self) -> bool:
        return self.state is DayState.INCOMPLETE

    @property
    def target_met(self) -> bool:
        return self.work_remaining.minutes == 0 and self.state is not DayState.EMPTY

    @property
    def leave_at(self) -> datetime | None:
        """When the work target is satisfied — the single most useful number on Today."""
        return self.expected_out_break_adjusted


def analyze_day(
    punches: list[Punch],
    *,
    day: date,
    policy: ShiftPolicy | None = None,
    now: datetime | None = None,
    swipe_requests: list[SwipeRequest] | None = None,
) -> DayAnalysis:
    """Analyze one day. ``swipe_requests`` lets an existing request suppress the suggestion."""
    policy = policy or ShiftPolicy.default()
    pairing = pair_punches(punches, day=day, now=now)

    if not pairing.segments:
        return _empty_day(day, pairing)

    worked = pairing.worked
    break_taken = pairing.break_taken
    first_in = pairing.first_in
    last_out = pairing.last_out
    assert first_in is not None and last_out is not None  # guaranteed by the segments check

    work_remaining = _clamp(policy.work_target - worked)
    break_remaining = _clamp(policy.break_target - break_taken)
    extra_worked = _clamp(worked - policy.work_target)

    expected_out = first_in + _to_delta(policy.shift_span)
    # An over-long break pushes the finish line out; a short one never pulls it in, because
    # the shift span already assumes the full break allowance.
    effective_break = max(policy.break_target, break_taken)
    expected_out_break_adjusted = first_in + _to_delta(policy.work_target + effective_break)

    state = DayState.INCOMPLETE if pairing.ongoing else DayState.COMPLETE
    early_exit = state is DayState.COMPLETE and worked < policy.work_target

    filed = _matching_request(swipe_requests, day)
    swipe_request_needed = early_exit and filed is None

    analysis = DayAnalysis(
        day=day,
        state=state,
        first_in=first_in,
        last_out=last_out,
        expected_out=expected_out,
        expected_out_break_adjusted=expected_out_break_adjusted,
        worked=worked,
        break_taken=break_taken,
        work_remaining=work_remaining,
        break_remaining=break_remaining,
        extra_worked=extra_worked,
        gross_span=pairing.gross_span,
        early_exit=early_exit,
        swipe_request_needed=swipe_request_needed,
        segments=pairing.segments,
        issues=pairing.issues,
        explanations=_explain(pairing, policy, worked, break_taken, first_in),
    )
    return _with_insights(analysis, policy, filed)


# --- insights -------------------------------------------------------------------------


def _with_insights(
    analysis: DayAnalysis, policy: ShiftPolicy, filed: SwipeRequest | None
) -> DayAnalysis:
    insights: list[Insight] = []

    for issue in analysis.issues:
        if issue.kind is IssueKind.INFERRED_OUT:
            insights.append(
                Insight(
                    InsightKind.MISSING_PUNCH,
                    Severity.WARNING,
                    "Missing punch detected",
                    issue.message,
                )
            )
        elif issue.kind is IssueKind.ORPHAN_OUT:
            insights.append(
                Insight(
                    InsightKind.MISSING_PUNCH,
                    Severity.WARNING,
                    "Unmatched punch",
                    issue.message,
                )
            )

    if analysis.state is DayState.INCOMPLETE:
        if analysis.work_remaining:
            insights.append(
                Insight(
                    InsightKind.STILL_WORKING,
                    Severity.INFO,
                    f"{analysis.work_remaining} left to work",
                    f"You can leave at {_clock(analysis.leave_at)}.",
                )
            )
        else:
            insights.append(
                Insight(
                    InsightKind.ON_TRACK,
                    Severity.SUCCESS,
                    "Target met — you're free to go",
                    f"{analysis.worked} worked against {_article(policy.work_target)} target.",
                )
            )

    if analysis.early_exit:
        insights.append(
            Insight(
                InsightKind.EARLY_EXIT,
                Severity.WARNING,
                f"Short by {policy.work_target - analysis.worked}",
                f"You worked {analysis.worked} against {_article(policy.work_target)} target.",
            )
        )

    if filed is not None:
        insights.append(
            Insight(
                InsightKind.SWIPE_FILED,
                Severity.INFO if filed.is_open else Severity.SUCCESS,
                f"Swipe request {_status_text(filed.status)}",
                f"Filed for {filed.for_date:%d %b} ({filed.direction})"
                + (f": {filed.remark}" if filed.remark else ""),
            )
        )
    elif analysis.swipe_request_needed:
        insights.append(
            Insight(
                InsightKind.SWIPE_NEEDED,
                Severity.WARNING,
                "Consider a swipe request",
                "This day is short of the target and no request has been filed.",
                Action(ActionKind.OPEN_SWIPE_REQUEST, "Raise swipe request"),
            )
        )

    if analysis.extra_worked and analysis.state is DayState.COMPLETE:
        insights.append(
            Insight(
                InsightKind.OVERTIME,
                Severity.SUCCESS,
                f"{analysis.extra_worked} extra worked",
                f"Beyond the {policy.work_target} target.",
            )
        )

    if analysis.break_taken > policy.break_target:
        over = analysis.break_taken - policy.break_target
        insights.append(
            Insight(
                InsightKind.LONG_BREAK,
                Severity.INFO,
                f"Break ran {over} over",
                f"Leave at {_clock(analysis.expected_out_break_adjusted)} to still make "
                f"{policy.work_target}.",
            )
        )
    elif (
        analysis.state is DayState.INCOMPLETE
        and analysis.break_remaining
        and analysis.work_remaining
    ):
        # The shift span already prices in the full break allowance, so break taken up to
        # that point is free — it does not move the finish line. Past it, every minute does,
        # which is the part worth knowing before deciding on a second coffee.
        #
        # Only while there is work left. Once the target is met the finish line is behind
        # you, and "taking it will not move 6:14 PM" is advice about a decision that no
        # longer exists.
        insights.append(
            Insight(
                InsightKind.BREAK_HEADROOM,
                Severity.INFO,
                f"{analysis.break_remaining} of break still free",
                f"Taking it will not move {_clock(analysis.leave_at)}. Anything beyond "
                f"pushes that later minute for minute.",
            )
        )

    return replace(analysis, insights=tuple(sorted(insights, key=_display_order)))


# --- explanations ---------------------------------------------------------------------


def _explain(
    pairing: Pairing,
    policy: ShiftPolicy,
    worked: Duration,
    break_taken: Duration,
    first_in: datetime,
) -> dict[str, Explanation]:
    parts = " + ".join(str(segment.duration) for segment in pairing.segments)
    notes = tuple(issue.message for issue in pairing.issues)

    return {
        "worked": Explanation(
            metric="worked",
            value=str(worked),
            formula=f"{parts} = {worked}",
            notes=notes,
        ),
        "break_taken": Explanation(
            metric="break_taken",
            value=str(break_taken),
            formula=f"gaps between {len(pairing.segments)} work segments = {break_taken}",
            notes=notes,
        ),
        "expected_out": Explanation(
            metric="expected_out",
            value=_clock(first_in + _to_delta(policy.shift_span)),
            formula=f"first in {_clock(first_in)} + shift span {policy.shift_span}",
        ),
        "expected_out_break_adjusted": Explanation(
            metric="expected_out_break_adjusted",
            value=_clock(
                first_in + _to_delta(policy.work_target + max(policy.break_target, break_taken))
            ),
            formula=(
                f"first in {_clock(first_in)} + work target {policy.work_target} "
                f"+ break {max(policy.break_target, break_taken)}"
            ),
            notes=(
                ("Break exceeded the allowance, so the finish time moved out.",)
                if break_taken > policy.break_target
                else ()
            ),
        ),
        "gross_span": Explanation(
            metric="gross_span",
            value=str(pairing.gross_span),
            formula=(
                f"last out {_clock(pairing.last_out)} - first in {_clock(first_in)} "
                f"= {pairing.gross_span} (matches the portal's Tot. Hrs.)"
            ),
        ),
    }


# --- helpers --------------------------------------------------------------------------


def _empty_day(day: date, pairing: Pairing) -> DayAnalysis:
    return DayAnalysis(
        day=day,
        state=DayState.EMPTY,
        first_in=None,
        last_out=None,
        expected_out=None,
        expected_out_break_adjusted=None,
        worked=Duration(0),
        break_taken=Duration(0),
        work_remaining=Duration(0),
        break_remaining=Duration(0),
        extra_worked=Duration(0),
        gross_span=Duration(0),
        early_exit=False,
        swipe_request_needed=False,
        issues=pairing.issues,
        insights=(
            Insight(
                InsightKind.NO_PUNCHES,
                Severity.INFO,
                "No punches recorded",
                "Nothing was logged for this day.",
            ),
        ),
    )


#: Reading order for the insight strip. Things needing action lead, then good news, then
#: neutral context — appending order is an implementation detail and makes a poor sort.
_SEVERITY_ORDER = {
    Severity.CRITICAL: 0,
    Severity.WARNING: 1,
    Severity.SUCCESS: 2,
    Severity.INFO: 3,
}

#: Tiebreak within a severity band. Sorting by ``kind.value`` would order these
#: alphabetically, which put "break headroom" above "time left to work" — a footnote above
#: the answer. The sequence below is the order someone would actually want to read them in.
_KIND_ORDER = (
    InsightKind.EARLY_EXIT,
    InsightKind.SHORT_HOURS,
    InsightKind.SWIPE_NEEDED,
    InsightKind.MISSING_PUNCH,
    InsightKind.ON_TRACK,
    InsightKind.STILL_WORKING,
    InsightKind.OVERTIME,
    InsightKind.LONG_BREAK,
    InsightKind.BREAK_HEADROOM,
    InsightKind.SWIPE_FILED,
    InsightKind.NO_PUNCHES,
)
_KIND_RANK = {kind: rank for rank, kind in enumerate(_KIND_ORDER)}


def _display_order(insight: Insight) -> tuple[int, int]:
    return (
        _SEVERITY_ORDER.get(insight.severity, 9),
        _KIND_RANK.get(insight.kind, len(_KIND_RANK)),
    )


def _matching_request(requests: list[SwipeRequest] | None, day: date) -> SwipeRequest | None:
    for request in requests or ():
        if request.for_date == day and request.status is not SwipeStatus.REJECTED:
            return request
    return None


def _status_text(status: SwipeStatus) -> str:
    return {
        SwipeStatus.IN_PROCESS: "pending",
        SwipeStatus.APPROVED: "approved",
        SwipeStatus.REJECTED: "rejected",
        SwipeStatus.CANCELLED: "cancelled",
    }.get(status, "filed")


#: Hour counts read aloud with a leading vowel — eight, eleven, eighteen.
_VOWEL_HOURS = {8, 11, 18}


def _article(duration: Duration) -> str:
    """Prefix a duration with the right indefinite article.

    An eight-hour target takes "an", not "a" — the vowel is in how the number is said, not
    in how it is written, so this cannot be decided from the first character.
    """
    article = "an" if duration.minutes // 60 in _VOWEL_HOURS else "a"
    return f"{article} {duration}"


def _clamp(duration: Duration) -> Duration:
    return duration if duration.minutes > 0 else Duration(0)


def _to_delta(duration: Duration) -> timedelta:
    return timedelta(minutes=duration.minutes)


def _clock(moment: datetime | None) -> str:
    if moment is None:
        return "--:--"
    return moment.strftime("%I:%M %p").lstrip("0")
