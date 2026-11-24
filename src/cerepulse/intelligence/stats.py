"""The one place a typical value is computed.

Medians throughout: one 3 AM deployment night must not become "your typical start". The
month rollup had its own *mean* over every day with a first-in — weekly offs that were
swiped included — while the trends used a median over working days, so the same month's
"average in" read differently on two screens.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from datetime import time

from cerepulse.models.values import Duration


def median_time(values: Iterable[time | None]) -> time | None:
    minutes = [value.hour * 60 + value.minute for value in values if value is not None]
    if not minutes:
        return None
    middle = round(statistics.median(minutes))
    return time(middle // 60 % 24, middle % 60)


def middle_half(values: Iterable[time | None]) -> tuple[time, time] | None:
    """The first and third quartiles: where the middle half of the values fall.

    The spread a median hides. "Usually 9:40" says nothing about whether that means 9:38 to
    9:42 or 9:00 to 10:30, and only the second is worth planning around. Needs four values,
    because a quartile of three is just the values themselves renamed.
    """
    minutes = [value.hour * 60 + value.minute for value in values if value is not None]
    if len(minutes) < 4:
        return None
    low, _, high = statistics.quantiles(minutes, n=4, method="inclusive")
    return _clock(round(low)), _clock(round(high))


def _clock(minutes: int) -> time:
    return time(minutes // 60 % 24, minutes % 60)


def median_duration(values: Iterable[Duration]) -> Duration | None:
    minutes = [value.minutes for value in values]
    if not minutes:
        return None
    return Duration(round(statistics.median(minutes)))


__all__ = ["median_duration", "median_time", "middle_half"]
