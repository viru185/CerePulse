"""Leave optimizer — the cheapest days to book for the longest continuous break.

Everyone works this out by hand once a year, badly, staring at a wall calendar. The
arithmetic is small but fiddly: a holiday falling on a Thursday makes the Friday worth four
days off, and the same holiday on a Wednesday is worth almost nothing.

A break is any run of consecutive dates whose working days are all taken as leave. Its
**cost** is the number of leave days that takes and its **gain** is the length of the run,
so the ratio is what makes one Friday worth booking and another not.

Three constraints shape the search.

**Windows must not be extendable for free.** If the day either side is already a day off, it
belongs inside the break — including it costs nothing and makes the break longer. So a
candidate is bounded by working days it did *not* buy, or by the edge of the horizon. Note
what this does not say: a break may perfectly well *begin* on a leave day. Taking a Friday
to reach the weekend is the single most common case there is.

**A break must be worth more than it costs.** Booking a Wednesday in the middle of a full
week buys exactly one day off for one day of leave. That is true, useless, and would drown
the real suggestions, so anything at parity is dropped.

**One suggestion per price, and no two in the same week.** Ranking the whole field by
efficiency sounds right and is wrong: a single Friday for three days off beats five days for
nine every time, so the app would never once suggest taking an actual holiday. The field is
therefore grouped by cost, and the best of each price is taken — what can I get for one day,
for two, for a week.

That alone is not enough. Run against a real holiday calendar with a Diwali cluster in it,
every price tier picked the same fortnight and the list became six ways of describing one
week in November, saying nothing about October or December. So a tier that would overlap a
suggestion already taken falls through to its next-best option elsewhere in the year.

Nothing here books anything. CerePulse is read-only against SpineHR; this says which days
are worth asking for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

#: Never suggest more than this many at once. A list long enough to need scrolling is a
#: list nobody reads to the end of.
MAX_SUGGESTIONS = 6

#: Saturday and Sunday, when the roster cannot be inferred from attendance data.
DEFAULT_OFF_WEEKDAYS = frozenset({5, 6})


@dataclass(frozen=True, slots=True)
class BreakPlan:
    """One stretch of time off, and what it costs to book."""

    start: date
    end: date
    #: Working days inside the run, which are the ones to apply for.
    leave_days: tuple[date, ...]
    #: Public holidays inside the run — the reason it is cheap.
    holidays: tuple[date, ...]

    @property
    def total_days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def cost(self) -> int:
        return len(self.leave_days)

    @property
    def efficiency(self) -> float:
        """Days off per day of leave spent. The whole point of the exercise."""
        return self.total_days / self.cost if self.cost else float(self.total_days)

    @property
    def label(self) -> str:
        if self.start.month == self.end.month:
            return f"{self.start:%d} – {self.end:%d %b}".replace(" 0", " ")
        return f"{self.start:%d %b} – {self.end:%d %b}".replace(" 0", " ")

    def overlaps(self, other: BreakPlan) -> bool:
        return self.start <= other.end and other.start <= self.end


def suggest_breaks(
    *,
    start: date,
    end: date,
    holidays: set[date],
    off_weekdays: frozenset[int] | set[int] = DEFAULT_OFF_WEEKDAYS,
    max_leave: int,
    limit: int = MAX_SUGGESTIONS,
) -> list[BreakPlan]:
    """The best breaks bookable between ``start`` and ``end``, best value first.

    ``max_leave`` is the balance available; a plan costing more than the user has is not a
    suggestion, it is a fantasy.
    """
    if max_leave <= 0 or end < start:
        return []

    horizon = _dates(start, end)
    free = [day for day in horizon if _is_free(day, holidays, off_weekdays)]
    if not free:
        return []

    candidates = _candidates(horizon, free=set(free), holidays=holidays, max_leave=max_leave)
    return _best_per_cost(candidates, limit=limit)


def _candidates(
    horizon: list[date], *, free: set[date], holidays: set[date], max_leave: int
) -> list[BreakPlan]:
    """Every maximal window costing at most ``max_leave`` and worth more than it costs.

    Quadratic in the horizon, which sounds worse than it is: the inner loop stops the moment
    the budget is spent, so in practice each start examines a fortnight or so.
    """
    plans: list[BreakPlan] = []
    last_index = len(horizon) - 1

    for index, first in enumerate(horizon):
        # A free day before the start means this window should have begun earlier — free
        # days extend a break at no cost, so a window that omits one is never the best.
        if index and horizon[index - 1] in free:
            continue

        spent: list[date] = []
        for offset, last in enumerate(horizon[index:], start=index):
            if last not in free:
                spent.append(last)
                if len(spent) > max_leave:
                    break
            if offset < last_index and horizon[offset + 1] in free:
                continue  # extendable for free, so not yet maximal
            total = offset - index + 1
            if spent and total > len(spent):
                plans.append(
                    BreakPlan(
                        start=first,
                        end=last,
                        leave_days=tuple(spent),
                        holidays=tuple(day for day in _dates(first, last) if day in holidays),
                    )
                )
    return plans


def _best_per_cost(candidates: list[BreakPlan], *, limit: int) -> list[BreakPlan]:
    """The best break at each price, each in a different part of the year."""
    by_cost: dict[int, list[BreakPlan]] = {}
    for plan in candidates:
        by_cost.setdefault(plan.cost, []).append(plan)
    for options in by_cost.values():
        options.sort(key=lambda plan: (-plan.total_days, plan.start))

    # Cheapest-per-day-spent tiers first, so a poor tier is the one that goes without when
    # the good periods are already taken.
    tiers = sorted(by_cost, key=lambda cost: (-by_cost[cost][0].efficiency, cost))

    chosen: list[BreakPlan] = []
    for cost in tiers:
        for plan in by_cost[cost]:
            if any(plan.overlaps(taken) for taken in chosen):
                continue
            chosen.append(plan)
            break
        if len(chosen) == limit:
            break

    chosen = _with_longest(chosen, candidates, limit=limit)
    # Ranked by value for selection, then presented in date order: a list of dates that
    # jumps around the year is much harder to plan against than one that reads forwards.
    return sorted(chosen, key=lambda plan: (plan.start, plan.cost))


def _with_longest(
    chosen: list[BreakPlan], candidates: list[BreakPlan], *, limit: int
) -> list[BreakPlan]:
    """Make sure the single longest break the balance can buy is always on the list.

    The spread rule is right for "where are the cheap wins" and wrong for "I want an actual
    fortnight off": a one-day suggestion claims the good period first and the long break
    never appears. Someone deciding whether to book leave is asking both questions, so the
    longest option earns a place even when it overlaps a cheaper one.
    """
    if not candidates:
        return chosen

    longest = max(candidates, key=lambda plan: (plan.total_days, -plan.cost))
    if any(plan.total_days >= longest.total_days for plan in chosen):
        return chosen
    if len(chosen) < limit:
        return [*chosen, longest]
    # At capacity: the least valuable tier gives up its place.
    weakest = min(chosen, key=lambda plan: plan.efficiency)
    return [plan for plan in chosen if plan is not weakest] + [longest]


def _is_free(day: date, holidays: set[date], off_weekdays: frozenset[int] | set[int]) -> bool:
    return day.weekday() in off_weekdays or day in holidays


def _dates(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


__all__ = ["DEFAULT_OFF_WEEKDAYS", "MAX_SUGGESTIONS", "BreakPlan", "suggest_breaks"]
