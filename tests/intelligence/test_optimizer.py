"""Leave optimizer: the cheapest days to book for the longest break.

Dates here are real. August 2026 opens on a Saturday and 15 August (Independence Day) falls
on that Saturday, which is exactly the sort of thing the optimizer has to be honest about.
"""

from __future__ import annotations

from datetime import date

from cerepulse.intelligence.optimizer import BreakPlan, suggest_breaks

# December 2026: Christmas Day is a Friday, so the 24th and 28th–31st are the interesting
# days. 1 Jan 2027 is a Friday.
CHRISTMAS = date(2026, 12, 25)


def plans(**kwargs: object) -> list[BreakPlan]:
    defaults: dict[str, object] = {
        "start": date(2026, 12, 1),
        "end": date(2027, 1, 10),
        "holidays": {CHRISTMAS},
        "max_leave": 3,
    }
    return suggest_breaks(**{**defaults, **kwargs})  # type: ignore[arg-type]


# --- the core arithmetic ----------------------------------------------------------------


def test_a_thursday_holiday_makes_the_friday_worth_four_days() -> None:
    """The whole reason the feature exists."""
    thursday = date(2026, 12, 24)
    friday = date(2026, 12, 25)

    found = suggest_breaks(
        start=date(2026, 12, 21),
        end=date(2026, 12, 28),
        holidays={thursday},
        max_leave=1,
    )
    best = max(found, key=lambda plan: plan.efficiency)

    assert best.leave_days == (friday,)
    assert best.total_days == 4  # Thu holiday, Fri leave, Sat, Sun
    assert best.efficiency == 4.0


def test_the_same_holiday_midweek_is_worth_much_less() -> None:
    """A Wednesday holiday leaves a leave day stranded between two working days."""
    thursday = suggest_breaks(
        start=date(2026, 12, 21),
        end=date(2026, 12, 28),
        holidays={date(2026, 12, 24)},
        max_leave=1,
    )
    wednesday = suggest_breaks(
        start=date(2026, 12, 21),
        end=date(2026, 12, 28),
        holidays={date(2026, 12, 23)},
        max_leave=1,
    )

    assert max(p.efficiency for p in thursday) > max(p.efficiency for p in wednesday)


def test_taking_a_friday_to_reach_the_weekend_is_a_suggestion() -> None:
    """The single most common case. A break may begin on the leave day itself."""
    friday = date(2026, 12, 11)
    found = suggest_breaks(
        start=date(2026, 12, 7),  # a Monday
        end=date(2026, 12, 13),  # the Sunday
        holidays=set(),
        max_leave=1,
    )
    best = max(found, key=lambda plan: plan.efficiency)

    assert best.start == friday
    assert best.leave_days == (friday,)
    assert best.total_days == 3
    assert best.efficiency == 3.0


def test_the_longest_break_the_balance_buys_is_always_offered() -> None:
    """Otherwise a one-day suggestion claims the week and the fortnight never appears."""
    found = suggest_breaks(
        start=date(2026, 12, 5),  # Saturday
        end=date(2026, 12, 13),  # the Sunday after next
        holidays=set(),
        max_leave=5,
    )
    best = max(found, key=lambda plan: plan.total_days)
    assert best.total_days == 9
    assert best.cost == 5  # Mon-Fri
    assert min(plan.cost for plan in found) == 1  # and the cheap Friday is still there


# --- honesty about the balance ----------------------------------------------------------


def test_nothing_is_suggested_without_a_balance() -> None:
    """A plan costing more than the user has is not a suggestion, it is a fantasy."""
    assert plans(max_leave=0) == []


def test_suggestions_never_cost_more_than_the_balance() -> None:
    for budget in (1, 2, 3, 5):
        for plan in plans(max_leave=budget):
            assert plan.cost <= budget


def test_every_suggestion_costs_at_least_one_day() -> None:
    """A run of weekend needs no booking and is not a suggestion."""
    assert all(plan.cost >= 1 for plan in plans())


# --- shape of the results ---------------------------------------------------------------


def value_tiers(found: list[BreakPlan]) -> list[BreakPlan]:
    """Everything but the longest-break entry, which is exempt from both spread rules."""
    longest = max(found, key=lambda plan: plan.total_days)
    return [plan for plan in found if plan is not longest]


def test_there_is_one_suggestion_per_price() -> None:
    """The question is what a day buys, and what two buy — not the same answer twice."""
    costs = [plan.cost for plan in value_tiers(plans(max_leave=5))]
    assert len(costs) == len(set(costs))


def test_value_suggestions_are_spread_across_the_year() -> None:
    """A real Diwali cluster made every price tier pick the same fortnight in November."""
    diwali = {date(2026, 11, 8), date(2026, 11, 10), date(2026, 11, 11)}
    found = suggest_breaks(
        start=date(2026, 7, 29),
        end=date(2026, 12, 31),
        holidays=diwali | {date(2026, 10, 2), date(2026, 12, 25)},
        max_leave=6,
    )
    tiers = value_tiers(found)

    assert len(tiers) > 1
    for index, plan in enumerate(tiers):
        for other in tiers[index + 1 :]:
            assert not plan.overlaps(other)


def test_suggestions_come_back_in_date_order() -> None:
    """A list that jumps around the year is much harder to plan against."""
    found = plans(max_leave=5)
    assert found == sorted(found, key=lambda plan: plan.start)


def test_the_list_stays_short_enough_to_read() -> None:
    found = suggest_breaks(
        start=date(2026, 1, 1), end=date(2026, 12, 31), holidays=set(), max_leave=20
    )
    assert len(found) <= 6


def test_every_suggestion_is_worth_more_than_it_costs() -> None:
    """Booking a Wednesday mid-week buys one day for one day. True, and useless."""
    assert all(plan.total_days > plan.cost for plan in plans(max_leave=5))


def test_a_plan_reports_the_holidays_that_made_it_cheap() -> None:
    found = suggest_breaks(
        start=date(2026, 12, 21), end=date(2026, 12, 28), holidays={CHRISTMAS}, max_leave=2
    )
    assert any(CHRISTMAS in plan.holidays for plan in found)


# --- edges ------------------------------------------------------------------------------


def test_an_inverted_range_is_empty_not_an_error() -> None:
    assert plans(start=date(2026, 12, 31), end=date(2026, 12, 1)) == []


def test_a_horizon_of_pure_working_days_offers_nothing_free() -> None:
    """A seven-day working week has no days off to anchor a break to."""
    found = suggest_breaks(
        start=date(2026, 12, 1),
        end=date(2026, 12, 20),
        holidays=set(),
        off_weekdays=frozenset(),
        max_leave=5,
    )
    assert found == []


def test_a_label_reads_naturally_within_and_across_months() -> None:
    same = BreakPlan(date(2026, 12, 24), date(2026, 12, 27), (date(2026, 12, 24),), ())
    across = BreakPlan(date(2026, 12, 31), date(2027, 1, 3), (date(2026, 12, 31),), ())

    assert same.label == "24 – 27 Dec"
    assert across.label == "31 Dec – 3 Jan"
