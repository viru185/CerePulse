"""The sandwich-leave rule, which this app does not know to be the company's rule.

Many Indian employers count the *gap* against your balance when you take leave on both
sides of it: leave on the Friday and the Monday can be charged as four days rather than
two, because the weekend is "sandwiched". Some count it only when both sides are leave,
some when either side is, and some not at all.

Nothing in SpineHR states which — or whether — so this ships **off**. Turning it on is the
user asserting their employer's policy, exactly as ``LeavePolicy``'s leave-year end and
comp-off window are configured defaults rather than confirmed facts. When it is off the app
says nothing about sandwiching at all; a warning that might not apply is worse than silence,
because it would have people leaving leave unbooked over a rule that does not exist.

Pure, like the rest of the layer: dates in, assessment out, no clock and no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

#: Saturday and Sunday. Overridden from the portal's own weekly-off markings where known,
#: for the same reason the month rollup does it: a shift worker's week is not Mon–Fri.
DEFAULT_OFF_WEEKDAYS = frozenset({5, 6})


class SandwichRule(Enum):
    """Which variant of the rule the employer applies."""

    #: Not applied. The shipped default, because nothing in the portal states otherwise.
    OFF = "off"
    #: The gap is charged only when leave is taken on *both* sides of it.
    BOTH_SIDES = "both_sides"
    #: The gap is charged when leave abuts it on either side. The harshest reading.
    EITHER_SIDE = "either_side"

    @property
    def label(self) -> str:
        return {
            SandwichRule.OFF: "Not applied",
            SandwichRule.BOTH_SIDES: "Only when leave falls on both sides",
            SandwichRule.EITHER_SIDE: "Whenever leave touches the gap",
        }[self]


@dataclass(frozen=True, slots=True)
class Sandwich:
    """A run of non-working days that the rule would charge to the balance."""

    #: The non-working days themselves — the weekend or holiday being charged.
    days: tuple[date, ...]
    #: The leave days that caused the charge.
    caused_by: tuple[date, ...]

    @property
    def cost(self) -> int:
        return len(self.days)

    @property
    def label(self) -> str:
        first, last = self.days[0], self.days[-1]
        if first == last:
            return f"{first:%a %d %b}".replace(" 0", " ")
        return f"{first:%a %d %b} – {last:%a %d %b}".replace(" 0", " ")


@dataclass(frozen=True, slots=True)
class SandwichAssessment:
    """What a set of leave dates costs once the rule is applied."""

    rule: SandwichRule
    booked_days: int
    sandwiches: tuple[Sandwich, ...]

    @property
    def extra_cost(self) -> int:
        return sum(sandwich.cost for sandwich in self.sandwiches)

    @property
    def total_cost(self) -> int:
        return self.booked_days + self.extra_cost

    @property
    def applies(self) -> bool:
        return self.rule is not SandwichRule.OFF and bool(self.sandwiches)


def assess(
    leave_days: set[date],
    *,
    rule: SandwichRule = SandwichRule.OFF,
    holidays: set[date] | None = None,
    off_weekdays: frozenset[int] | set[int] = DEFAULT_OFF_WEEKDAYS,
) -> SandwichAssessment:
    """What ``leave_days`` actually costs under ``rule``.

    With the rule off this reports the booked days and nothing else, which is what makes
    "off" mean silent rather than merely zero — the UI has no sandwich to render, so it
    shows no claim about a policy nobody has confirmed.
    """
    booked = len(leave_days)
    if rule is SandwichRule.OFF or not leave_days:
        return SandwichAssessment(rule, booked, ())

    holidays = holidays or set()
    found: list[Sandwich] = []
    for run in _free_runs(leave_days, holidays=holidays, off_weekdays=off_weekdays):
        before = run[0] - timedelta(days=1)
        after = run[-1] + timedelta(days=1)
        touching = tuple(day for day in (before, after) if day in leave_days)

        needed = 2 if rule is SandwichRule.BOTH_SIDES else 1
        if len(touching) >= needed:
            found.append(Sandwich(run, touching))

    return SandwichAssessment(rule, booked, tuple(found))


def _free_runs(
    leave_days: set[date],
    *,
    holidays: set[date],
    off_weekdays: frozenset[int] | set[int],
) -> list[tuple[date, ...]]:
    """Every unbroken stretch of non-working days sitting between the leave dates.

    Only the gaps *inside* the span are considered. A weekend after the last leave day is
    not sandwiched by anything — it is just the weekend — and charging it would turn a
    single Friday off into a three-day deduction.
    """
    if not leave_days:
        return []

    start, end = min(leave_days), max(leave_days)
    runs: list[tuple[date, ...]] = []
    current: list[date] = []

    cursor = start
    while cursor <= end:
        free = cursor in holidays or cursor.weekday() in off_weekdays
        if free and cursor not in leave_days:
            current.append(cursor)
        else:
            if current:
                runs.append(tuple(current))
            current = []
        cursor += timedelta(days=1)

    if current:
        runs.append(tuple(current))
    return runs


__all__ = [
    "DEFAULT_OFF_WEEKDAYS",
    "Sandwich",
    "SandwichAssessment",
    "SandwichRule",
    "assess",
]
