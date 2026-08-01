"""One instruction, derived from the day.

Today already reports a great many true things at once — worked, break, remaining,
overtime, and up to five insights. Not one of them is an instruction, so the screen answers
"what is the state of my day" and leaves "so what do I do" to the reader. This produces the
second answer: exactly one line, chosen by what the day is actually asking for.

Pure, like everything in this layer. It reads a finished :class:`DayAnalysis` and decides
nothing about time on its own — the analysis was already computed against an injected
``now``, so a day that was analysed at 2 PM keeps saying what it said at 2 PM.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING

from cerepulse.intelligence.insights import Action, ActionKind, Severity
from cerepulse.intelligence.segments import IssueKind

if TYPE_CHECKING:  # pragma: no cover — import cycle; day.py imports this module
    from cerepulse.intelligence.day import DayAnalysis


class Presence(Enum):
    """Where the person is right now, as far as the punches can tell.

    The distinction the app was missing is ``ON_BREAK``: a day whose last punch is an Out
    looked finished, so at one o'clock the screen announced an early exit and suggested
    filing a swipe request for a day still being worked.
    """

    NOT_STARTED = "not_started"
    WORKING = "working"
    ON_BREAK = "on_break"
    FINISHED = "finished"

    @property
    def label(self) -> str:
        return {
            Presence.NOT_STARTED: "Not started",
            Presence.WORKING: "Working",
            Presence.ON_BREAK: "On break",
            Presence.FINISHED: "Done",
        }[self]


class NextActionKind(Enum):
    """What the day is asking for."""

    CLOCK_IN = "clock_in"
    KEEP_WORKING = "keep_working"
    RETURN_FROM_BREAK = "return_from_break"
    FREE_TO_GO = "free_to_go"
    CHECK_PUNCHES = "check_punches"
    FILE_SWIPE_REQUEST = "file_swipe_request"
    NOTHING_TO_DO = "nothing_to_do"


@dataclass(frozen=True, slots=True)
class NextAction:
    """The one thing worth doing, and when."""

    kind: NextActionKind
    headline: str
    detail: str
    severity: Severity = Severity.INFO
    #: The moment the headline refers to, when it refers to one. Lets the UI run a
    #: countdown against it without re-deriving which time the sentence meant.
    at: datetime | None = None
    action: Action | None = None


def presence_of(analysis: DayAnalysis, *, is_today: bool) -> Presence:
    """Read the current state from the punches.

    ``is_today`` is what separates "on a break" from "went home": an identical punch log
    means one thing at 1 PM today and the other thing on a day already past.
    """
    from cerepulse.intelligence.day import DayState

    if analysis.state is DayState.EMPTY:
        return Presence.NOT_STARTED
    if analysis.clocked_in:
        return Presence.WORKING
    if is_today and analysis.state is DayState.INCOMPLETE:
        return Presence.ON_BREAK
    return Presence.FINISHED


def next_action(analysis: DayAnalysis, *, is_today: bool) -> NextAction:
    """Decide the single next step for this day."""
    presence = presence_of(analysis, is_today=is_today)

    if presence is Presence.NOT_STARTED:
        return _not_started(analysis, is_today=is_today)
    if presence is Presence.WORKING:
        return _working(analysis)
    if presence is Presence.ON_BREAK:
        return _on_break(analysis)
    return _finished(analysis)


# --- one branch per presence ------------------------------------------------------------


def _not_started(analysis: DayAnalysis, *, is_today: bool) -> NextAction:
    if is_today:
        return NextAction(
            NextActionKind.CLOCK_IN,
            "Clock in",
            "Nothing is recorded for today yet. The countdown starts at your first punch.",
        )
    if analysis.swipe_request_needed:
        return _swipe_request(analysis, "No punches were recorded for this day.")
    return NextAction(
        NextActionKind.NOTHING_TO_DO,
        "Nothing recorded",
        "This day carries no punches.",
    )


def _working(analysis: DayAnalysis) -> NextAction:
    if analysis.work_remaining:
        return NextAction(
            NextActionKind.KEEP_WORKING,
            f"Keep going until {_clock(analysis.leave_at)}",
            f"{analysis.work_remaining} left to work"
            + (
                f", and {analysis.break_remaining} of break you can still take without "
                f"moving that time."
                if analysis.break_remaining
                else "."
            ),
            at=analysis.leave_at,
        )
    return NextAction(
        NextActionKind.FREE_TO_GO,
        "You can leave now",
        f"{analysis.worked} worked. Everything from here is overtime.",
        severity=Severity.SUCCESS,
    )


def _on_break(analysis: DayAnalysis) -> NextAction:
    """A break in progress: the only question is when it stops being free.

    Up to the allowance a break costs nothing — the shift span already prices it in. Past
    that, the finish time moves out minute for minute, which is the one fact worth knowing
    before ordering another coffee.
    """
    if analysis.break_remaining and analysis.last_out is not None:
        back_by = analysis.last_out + timedelta(minutes=analysis.break_remaining.minutes)
        return NextAction(
            NextActionKind.RETURN_FROM_BREAK,
            f"Back by {_clock(back_by)}",
            f"{analysis.break_remaining} of break allowance is left. Returning by then "
            f"still leaves you free at {_clock(analysis.leave_at)}.",
            at=back_by,
        )
    return NextAction(
        NextActionKind.RETURN_FROM_BREAK,
        "Back when you can",
        f"The break allowance is used up, so each further minute pushes "
        f"{_clock(analysis.leave_at)} out by one.",
        severity=Severity.WARNING,
        at=analysis.leave_at,
    )


def _finished(analysis: DayAnalysis) -> NextAction:
    # A repaired punch log comes first: the worked figure it produced is a reconstruction,
    # so acting on a shortfall derived from it means acting on a guess.
    if any(
        issue.kind in (IssueKind.INFERRED_OUT, IssueKind.ORPHAN_OUT) for issue in analysis.issues
    ):
        return NextAction(
            NextActionKind.CHECK_PUNCHES,
            "Check this day's punches",
            "A punch is missing, so the hours below were reconstructed rather than measured.",
            severity=Severity.WARNING,
        )

    if analysis.swipe_request_needed:
        return _swipe_request(
            analysis, f"The day is {analysis.work_remaining} short and nothing has been filed."
        )

    if analysis.extra_worked:
        return NextAction(
            NextActionKind.NOTHING_TO_DO,
            "Nothing to do",
            f"{analysis.worked} worked, {analysis.extra_worked} beyond the target.",
            severity=Severity.SUCCESS,
        )
    return NextAction(
        NextActionKind.NOTHING_TO_DO,
        "Nothing to do",
        f"{analysis.worked} worked. The day is settled.",
        severity=Severity.SUCCESS,
    )


def _swipe_request(analysis: DayAnalysis, detail: str) -> NextAction:
    return NextAction(
        NextActionKind.FILE_SWIPE_REQUEST,
        "Raise a swipe request",
        detail,
        severity=Severity.WARNING,
        action=Action(ActionKind.OPEN_SWIPE_REQUEST, "Open SpineHR"),
    )


def _clock(moment: datetime | None) -> str:
    if moment is None:
        return "--:--"
    return moment.strftime("%I:%M %p").lstrip("0")


__all__ = ["NextAction", "NextActionKind", "Presence", "next_action", "presence_of"]
