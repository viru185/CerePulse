"""Half days, days worked off site, and days off that were worked.

Three of the audit's high findings shared one cause: `portion` and the raw user-type codes
were parsed and stored from the first release and never read back, so every rollup treated
a half day as a whole one and a worked Saturday as nothing at all.
"""

from __future__ import annotations

from datetime import date, time

from cerepulse.intelligence.attention import find_attention
from cerepulse.intelligence.month import analyze_month, analyze_week
from cerepulse.intelligence.policy import ShiftPolicy
from cerepulse.models.attendance import AttendanceDay, DayStatus
from cerepulse.models.values import Duration

POLICY = ShiftPolicy()
END_OF_JULY = date(2026, 7, 31)


def day(
    when: date,
    *,
    status: DayStatus = DayStatus.PRESENT,
    gross: str = "9.00",
    portion: float = 1.0,
    ut1: str = "DP",
    ut2: str = "---",
) -> AttendanceDay:
    return AttendanceDay(
        day=when,
        weekday=when.strftime("%a"),
        status=status,
        first_in=time(9, 0),
        last_out=time(18, 0),
        user_type_1=ut1,
        user_type_2=ut2,
        portion=portion,
        total_hours=Duration.from_hhmm(gross),
    )


def month(*days: AttendanceDay):  # type: ignore[no-untyped-def]
    return analyze_month(
        list(days), year=2026, month=7, policy=POLICY, holidays=[], today=END_OF_JULY
    )


# --- H3: a half day owes half ---------------------------------------------------------------


def test_a_half_day_is_measured_against_half_a_target() -> None:
    """Four hours on a half day is the whole of what was owed, not four hours short. It had
    been booking a four-hour deficit into the bank, the week, the short-day count and the
    required daily average — an invented debt on every screen that adds days up."""
    half = day(date(2026, 7, 1), status=DayStatus.HALF_DAY, gross="5.00", portion=0.5)
    analysis = month(half)

    assert analysis.elapsed_target == Duration(240)
    assert analysis.bank_delta == Duration(0)
    assert analysis.short_days == 0


def test_the_week_target_is_the_sum_of_what_each_day_owed() -> None:
    days = [
        day(date(2026, 7, 6)),  # Monday, whole
        day(date(2026, 7, 7), status=DayStatus.HALF_DAY, gross="5.00", portion=0.5),
    ]
    week = analyze_week(days, week_start=date(2026, 7, 6), policy=POLICY, today=END_OF_JULY)
    assert week.target == Duration(480 + 240)


def test_the_policy_names_the_rule() -> None:
    assert POLICY.owed_for(0.5) == Duration(240)
    assert POLICY.owed_for(1.0) == Duration(480)
    assert POLICY.owed_for(0.0) == Duration(480), "no portion means a whole day"


# --- H4: the highlight bar ----------------------------------------------------------------


def test_a_half_day_with_its_half_worked_needs_no_attention() -> None:
    half = day(date(2026, 7, 1), status=DayStatus.HALF_DAY, gross="5.00", portion=0.5)
    assert find_attention([half], policy=POLICY, today=END_OF_JULY) == {}


def test_a_day_half_on_duty_needs_no_attention() -> None:
    """Half outdoor duty and half present resolves to a half day and reliably came up four
    hours short — a screen that highlights a third of the month teaches people to ignore
    the highlight."""
    mixed = day(
        date(2026, 7, 1), status=DayStatus.HALF_DAY, gross="4.00", portion=0.5, ut1="OD", ut2="DP"
    )
    assert find_attention([mixed], policy=POLICY, today=END_OF_JULY) == {}


def test_a_genuinely_short_day_is_still_flagged() -> None:
    short = day(date(2026, 7, 1), gross="6.00")
    assert date(2026, 7, 1) in find_attention([short], policy=POLICY, today=END_OF_JULY)


# --- H5: a day off that was worked -------------------------------------------------------


def test_a_worked_weekly_off_is_reported_beside_the_bank_not_inside_it() -> None:
    """The Saturday that earns a comp-off is `WO / CO+` with nine hours on it. The status
    stays weekly off — it owes nothing — but the hours are real and were being discarded."""
    saturday = day(date(2026, 7, 4), status=DayStatus.WEEKLY_OFF, ut1="WO", ut2="CO+")
    assert saturday.worked_on_off_day

    analysis = month(saturday)
    assert analysis.elapsed_target == Duration(0)
    assert analysis.bank_delta == Duration(0)
    assert analysis.off_day_worked == Duration(480)


def test_an_ordinary_weekend_has_nothing_to_report() -> None:
    sunday = day(date(2026, 7, 5), status=DayStatus.WEEKLY_OFF, gross="0.00", ut1="WO")
    assert not sunday.worked_on_off_day
    assert month(sunday).off_day_worked == Duration(0)
