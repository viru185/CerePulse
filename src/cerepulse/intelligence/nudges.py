"""Observations about habits, rather than about a day's arithmetic.

Every other insight in this app answers a question about *today*: how much is worked, what
is left, whether a request is needed. That leaves the app almost silent, because most of
those either never notify or need a fifteen-minute tick to land inside a narrow window — a
whole week can pass without a word.

These answer a different question: how long has something been true? Four hours without a
break. Two months without a day off. Nobody notices those about themselves, which is the only
honest reason for a computer to mention them.

Pure, like the rest of the intelligence layer: every entry point takes ``now`` or ``today``,
so a nudge that fires at half past three is testable at any hour of any day.

**These are the one place the app is allowed to be pointed**, and the boundary is worth
stating. :mod:`cerepulse.intelligence.voice` may never add personality to a warning about
work already done — a short day, a missing punch, a repaired figure — because softening or
decorating a number is how a tool stops being trusted. A nudge is not that. It reports no
figure that could be softened, and it is about a pattern the user can still do something
about, so it can be dry, or warm, or a little pointed, without ever putting a gloss on a
fact.
"""

from __future__ import annotations

from datetime import date, datetime

from cerepulse.intelligence.day import DayAnalysis, DayState
from cerepulse.intelligence.insights import Insight, InsightKind, Severity
from cerepulse.models.attendance import AttendanceDay, DayStatus
from cerepulse.models.values import Duration

#: How long clocked in without a break before it is worth mentioning. Four hours is the
#: point at which "not got round to it" has become "not going to", and it is short enough to
#: leave time to act on.
BREAK_OVERDUE = Duration(4 * 60)

#: A break shorter than this is a walk to the kettle, not a break. Without a floor, a day
#: with one two-minute gap in it would count as having stopped for lunch.
REAL_BREAK = Duration(10)

#: Days since the last leave before the app says anything. Deliberately long: this is a
#: remark about a season, not a nag, and one that arrived monthly would be noise.
LEAVE_STALE_DAYS = 75

#: How far back to look for the last day off. Long enough to outrun the threshold above, so
#: the window never causes the silence it is meant to detect.
LEAVE_LOOKBACK_DAYS = 240


def day_nudges(analysis: DayAnalysis, *, now: datetime | None = None) -> list[Insight]:
    """What is worth saying about the shape of today, beyond its numbers."""
    if analysis.state is not DayState.INCOMPLETE or not analysis.clocked_in:
        # Only while the day is still live and the user is at work. Telling somebody who
        # went home four hours ago that they should take a break is advice about a decision
        # already made, and the fastest way to have notifications switched off.
        return []

    moment = now or datetime.now()
    if analysis.first_in is None:
        return []

    elapsed = Duration(int((moment - analysis.first_in).total_seconds() // 60))
    if analysis.break_taken >= REAL_BREAK or elapsed < BREAK_OVERDUE:
        return []

    return [
        Insight(
            InsightKind.NO_BREAK_YET,
            Severity.INFO,
            f"{elapsed} in, no break yet",
            f"You clocked in at {analysis.first_in:%H:%M} and have not stopped since.",
        )
    ]


def leave_nudges(days: list[AttendanceDay], *, today: date | None = None) -> list[Insight]:
    """Whether it has been a long time since a day off.

    Read from the **muster**, not the leave ledger. The obvious source is the ledger's
    ``consumed_days``, and it does not work: every row this portal writes is a credit, and
    ``consumed_days`` is ``0.0`` on all of them — for this account, across every leave type,
    for the whole year. A nudge built on it would have been silent forever and looked like a
    threshold that was merely set too high.

    What the muster records is what actually happened: a day marked leave or half-day is a
    day off however it was filed. It is also the source that needs no other sync to be
    working.

    No days off in the window at all says nothing rather than "never". The history the app
    holds is finite, and the difference between "you have not taken leave in a year" and
    "the cache only goes back three months" is not one this can tell.
    """
    when = today or date.today()
    off = [
        day.day
        for day in days
        if day.status in (DayStatus.LEAVE, DayStatus.HALF_DAY) and day.day <= when
    ]
    if not off:
        return []

    last = max(off)
    since = (when - last).days
    if since < LEAVE_STALE_DAYS:
        return []

    return [
        Insight(
            InsightKind.LEAVE_UNUSED,
            Severity.INFO,
            f"{since} days since your last day off",
            f"The last one was {last:%d %b}. Leave does not keep forever.",
        )
    ]


__all__ = [
    "BREAK_OVERDUE",
    "LEAVE_STALE_DAYS",
    "REAL_BREAK",
    "day_nudges",
    "leave_nudges",
]
