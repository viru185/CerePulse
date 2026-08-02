"""The sandwich rule, which the app does not know to be anybody's rule.

The most important test here is the first one. Nothing in SpineHR states whether the
employer charges the gap between two leave days, so the shipped default has to be silence —
a warning that might not apply would have people leaving leave unbooked over a policy that
does not exist.
"""

from __future__ import annotations

from datetime import date

from cerepulse.intelligence.sandwich import SandwichRule, assess

# Fri 3 Jul 2026, Sat 4, Sun 5, Mon 6.
FRIDAY = date(2026, 7, 3)
SATURDAY = date(2026, 7, 4)
SUNDAY = date(2026, 7, 5)
MONDAY = date(2026, 7, 6)


def test_the_rule_is_off_by_default() -> None:
    """Off means silent, not zero: there is nothing for a screen to render or explain."""
    result = assess({FRIDAY, MONDAY})

    assert result.rule is SandwichRule.OFF
    assert result.sandwiches == ()
    assert not result.applies
    assert result.total_cost == 2


def test_both_sides_charges_the_weekend_between_two_leave_days() -> None:
    result = assess({FRIDAY, MONDAY}, rule=SandwichRule.BOTH_SIDES)

    assert result.applies
    assert result.extra_cost == 2
    assert result.total_cost == 4
    (sandwich,) = result.sandwiches
    assert sandwich.days == (SATURDAY, SUNDAY)
    assert sandwich.caused_by == (FRIDAY, MONDAY)


def test_both_sides_leaves_a_lone_friday_alone() -> None:
    """The weekend after the last leave day is just the weekend, not a sandwich.

    Charging it would turn a single Friday off into a three-day deduction.
    """
    assert assess({FRIDAY}, rule=SandwichRule.BOTH_SIDES).sandwiches == ()


def test_either_side_charges_a_weekend_touched_once() -> None:
    result = assess({FRIDAY}, rule=SandwichRule.EITHER_SIDE)
    # Still nothing: the run has to sit between leave dates, and one date has no between.
    assert result.sandwiches == ()

    both = assess({FRIDAY, MONDAY}, rule=SandwichRule.EITHER_SIDE)
    assert both.extra_cost == 2


def test_a_holiday_in_the_gap_is_charged_like_a_weekend() -> None:
    """Leave on Friday and Tuesday, with the Monday a company holiday."""
    tuesday = date(2026, 7, 7)
    result = assess({FRIDAY, tuesday}, rule=SandwichRule.BOTH_SIDES, holidays={MONDAY})

    (sandwich,) = result.sandwiches
    assert sandwich.days == (SATURDAY, SUNDAY, MONDAY)
    assert result.total_cost == 5


def test_consecutive_leave_has_no_gap_to_charge() -> None:
    result = assess({FRIDAY, date(2026, 7, 2)}, rule=SandwichRule.BOTH_SIDES)
    assert result.sandwiches == ()
    assert result.total_cost == 2


def test_a_working_day_in_the_gap_breaks_the_sandwich() -> None:
    """Coming in on the Monday means the weekend was not bracketed by leave.

    Leave on Friday and Tuesday, working the Monday: the run Sat–Sun ends at a day that
    was worked, so the Tuesday is not on the far side of it.
    """
    result = assess({FRIDAY, date(2026, 7, 7)}, rule=SandwichRule.BOTH_SIDES)

    assert result.sandwiches == ()
    assert result.total_cost == 2


def test_no_leave_costs_nothing_under_any_rule() -> None:
    for rule in SandwichRule:
        assert assess(set(), rule=rule).total_cost == 0
