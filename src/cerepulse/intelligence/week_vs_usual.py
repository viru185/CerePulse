"""This week, measured against what this person usually does.

The Insights screen's long-run medians move by minutes a month, so the page read the same
every day and stopped being opened. This is the part that cannot go stale: each day of the
current week compared with the baseline for *that weekday* — started 22 minutes later than
a usual Tuesday, worked 10 minutes over it, break as usual. It changes daily because the
week does.

Pure, like the rest of the intelligence layer: facts and habits in, comparisons out, time
injected. The baseline comes from :mod:`cerepulse.intelligence.trends`, which already
carries the discipline that matters here — medians rather than means, a minimum sample
before claiming a habit, and break figures only from punch logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta

from cerepulse.intelligence.trends import DayFact, Habits, WeekdayHabit
from cerepulse.models.values import Duration

#: Deltas below this are reported as "as usual". Every start time wobbles by a few minutes,
#: and reading each wobble back as news would teach the reader to skim past the section —
#: the exact fate the old page met.
NOTABLE = Duration(15)

_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(frozen=True, slots=True)
class DayComparison:
    """One day of this week beside its own weekday's baseline."""

    day: date
    started: time | None
    #: Against the usual start *for this weekday*. Positive means later than usual. None
    #: when either side is unknown — an absent baseline is not a zero-minute delta.
    start_delta: Duration | None
    worked: Duration
    worked_delta: Duration | None
    break_taken: Duration | None
    break_delta: Duration | None
    #: Figures reconstructed from the grid rather than punches. Carried so the rendering
    #: can stay plain about them — the same rule the voice engine follows.
    estimated: bool

    @property
    def weekday_name(self) -> str:
        return _WEEKDAY_NAMES[self.day.weekday()]

    @property
    def is_notable(self) -> bool:
        return any(
            delta is not None and abs(delta.minutes) >= NOTABLE.minutes
            for delta in (self.start_delta, self.worked_delta, self.break_delta)
        )


@dataclass(frozen=True, slots=True)
class WeekComparison:
    """The week so far, and how much history the baseline behind it rests on."""

    week_start: date
    days: tuple[DayComparison, ...]
    #: Measured days behind the baseline — :attr:`Habits.measured_days`, carried so the
    #: screen can say what the comparison stands on rather than presenting it as fact.
    baseline_days: int

    @property
    def has_baseline(self) -> bool:
        """Whether the habits behind the deltas have earned the right to be a yardstick."""
        return self.baseline_days > 0 and any(
            comparison.start_delta is not None or comparison.worked_delta is not None
            for comparison in self.days
        )

    @property
    def notable_days(self) -> int:
        return sum(1 for comparison in self.days if comparison.is_notable)


def compare_week(
    facts: list[DayFact],
    habits: Habits,
    *,
    week_start: date,
    today: date,
) -> WeekComparison:
    """Each day of the current week against its weekday's own baseline.

    Only days up to ``today`` — a comparison for Friday on a Wednesday would be a row of
    blanks pretending to be data. Days inside the week that carry no fact (leave, a holiday,
    an unmeasured day) are simply absent rather than scored zero, the same rule the trends
    themselves follow.
    """
    by_weekday: dict[int, WeekdayHabit] = {
        habit.weekday: habit for habit in habits.weekdays if habit.sample
    }
    week_end = min(today, week_start + timedelta(days=6))

    comparisons = []
    for fact in facts:
        if not (week_start <= fact.day <= week_end):
            continue
        habit = by_weekday.get(fact.day.weekday())
        comparisons.append(
            DayComparison(
                day=fact.day,
                started=fact.first_in,
                start_delta=_time_delta(fact.first_in, habit.typical_in if habit else None),
                worked=fact.worked,
                worked_delta=(
                    Duration(fact.worked.minutes - habit.typical_worked.minutes) if habit else None
                ),
                break_taken=fact.break_taken,
                break_delta=(
                    Duration(fact.break_taken.minutes - habits.typical_break.minutes)
                    if fact.break_taken is not None and habits.typical_break is not None
                    else None
                ),
                estimated=fact.estimated,
            )
        )

    return WeekComparison(
        week_start=week_start,
        days=tuple(comparisons),
        baseline_days=habits.measured_days if habits.has_enough else 0,
    )


def describe(comparison: DayComparison) -> str:
    """One line for one day: what differed, and "a usual day" when nothing did.

    Deltas under :data:`NOTABLE` read as "as usual" rather than as ±4m noise. Saying the
    quiet part matters too — a day with no line at all reads as missing, not as normal.
    """
    parts: list[str] = []

    if comparison.start_delta is not None:
        minutes = comparison.start_delta.minutes
        if abs(minutes) >= NOTABLE.minutes:
            direction = "later" if minutes > 0 else "earlier"
            parts.append(f"started {_span(abs(minutes))} {direction} than usual")
        else:
            parts.append("started as usual")
    elif comparison.started is not None:
        parts.append(f"in at {_clock(comparison.started)}")

    if comparison.worked_delta is not None:
        minutes = comparison.worked_delta.minutes
        if abs(minutes) >= NOTABLE.minutes:
            sign = "+" if minutes > 0 else "−"
            parts.append(
                f"worked {_span(comparison.worked.minutes)} ({sign}{_span(abs(minutes))} vs usual)"
            )
        else:
            parts.append(f"worked {_span(comparison.worked.minutes)}, as usual")
    else:
        parts.append(f"worked {_span(comparison.worked.minutes)}")

    if (
        comparison.break_delta is not None
        and abs(comparison.break_delta.minutes) >= NOTABLE.minutes
    ):
        minutes = comparison.break_delta.minutes
        direction = "over" if minutes > 0 else "under"
        parts.append(f"break {_span(abs(minutes))} {direction} your median")

    if comparison.estimated:
        parts.append("estimated from the grid")

    return " · ".join(parts)


def _span(minutes: int) -> str:
    hours, rest = divmod(max(0, minutes), 60)
    if not hours:
        return f"{rest}m"
    return f"{hours}h {rest:02d}m" if rest else f"{hours}h"


def _clock(when: time) -> str:
    return when.strftime("%I:%M %p").lstrip("0")


def _time_delta(actual: time | None, usual: time | None) -> Duration | None:
    if actual is None or usual is None:
        return None
    return Duration((actual.hour * 60 + actual.minute) - (usual.hour * 60 + usual.minute))


__all__ = ["NOTABLE", "DayComparison", "WeekComparison", "compare_week", "describe"]
