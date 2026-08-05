"""Turning a travel time into an answer: when do I actually get home?

Pure. No network, no clock of its own — every entry point takes the times it needs, which is
what makes an evening commute testable at eleven in the morning.

The app has always answered "when can I leave?". This is the other half of the same question,
and the arithmetic between them is small enough that the value here is entirely in what it
refuses to claim: a journey nobody has measured is not reported as a time, and a delay the
provider did not mention is not invented.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from cerepulse.commute.models import TravelEstimate
from cerepulse.models.values import Duration

#: No buffer. A module-level singleton because ``Duration`` is frozen, and constructing one
#: in a default argument is a function call at import time.
NO_BUFFER = Duration(0)


@dataclass(frozen=True, slots=True)
class Arrival:
    """When the front door opens, and the sentence that says so."""

    #: When they would get home, leaving at :attr:`leave_at`.
    at: datetime
    #: The exit time this was computed from — the app's own prediction, not a guess made
    #: here. Carried so the caption can name it: an arrival time with no departure beside it
    #: is unfalsifiable, and the user is the only one who can tell whether the exit is right.
    leave_at: datetime
    estimate: TravelEstimate
    #: Time added for reaching the vehicle, parking, the walk at either end. Configurable
    #: because it is a fact about a person's building, not about the road.
    buffer: Duration

    @property
    def total(self) -> Duration:
        return Duration(self.estimate.duration.minutes + self.buffer.minutes)

    @property
    def clock(self) -> time:
        return self.at.time()


def arrival_at(
    leave_at: datetime,
    estimate: TravelEstimate,
    *,
    buffer: Duration = NO_BUFFER,
) -> Arrival:
    """Add the journey to the departure. That is the whole calculation.

    Kept as a named function rather than inlined at the call site because the *inputs* are
    the interesting part — which departure, which estimate, plus a buffer that is nobody's
    business but the user's — and a screen assembling that itself would be a screen deciding
    it.
    """
    total = estimate.duration.minutes + buffer.minutes
    return Arrival(
        at=leave_at + timedelta(minutes=total),
        leave_at=leave_at,
        estimate=estimate,
        buffer=buffer,
    )


def describe(arrival: Arrival, *, now: datetime | None = None) -> str:
    """The caption under the arrival time: how long, from when, and how stale.

    Every figure it can be wrong about is named. "43 min" alone invites the reader to assume
    it was measured this second; saying when it was fetched lets them decide whether to
    believe it, which is the only honest thing to do with a number that ages.
    """
    parts = [
        _span(arrival.total),
        f"{arrival.estimate.distance_km:.1f} km",
        f"leaving {_clock(arrival.leave_at.time())}",
    ]
    # The traffic share is stated in both directions. Saying nothing on a clear road left
    # the reader unable to tell "no congestion" from "congestion not measured" — and the
    # answer was fetched with live traffic either way, so the card knows which it is.
    if arrival.estimate.is_congested:
        parts.append(f"{_span(arrival.estimate.delay)} of it traffic")
    else:
        parts.append("no traffic delay")
    if arrival.estimate.fetched_at is not None:
        parts.append(f"checked {_age(arrival.estimate.fetched_at, now=now)}")
    return " · ".join(parts)


def _age(fetched_at: datetime, *, now: datetime | None = None) -> str:
    elapsed = (now or datetime.now()) - fetched_at
    minutes = int(elapsed.total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes == 1:
        return "a minute ago"
    if minutes < 60:
        return f"{minutes} min ago"
    return f"at {_clock(fetched_at.time())}"


def _span(duration: Duration) -> str:
    """``1h 12m``, or ``43 min`` when it is under the hour."""
    hours, minutes = divmod(max(0, duration.minutes), 60)
    if not hours:
        return f"{minutes} min"
    return f"{hours}h {minutes:02d}m" if minutes else f"{hours}h"


def _clock(when: time) -> str:
    """``6:24 PM``. Windows has no ``%-I``, so the leading zero comes off by hand."""
    return when.strftime("%I:%M %p").lstrip("0")


__all__ = ["Arrival", "arrival_at", "describe"]
