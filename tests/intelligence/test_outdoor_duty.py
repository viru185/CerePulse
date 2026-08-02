"""Outdoor duty, and a month that has barely started.

Both were found in live data. Six days of "EAE Training in Bengaluru" (14–19 Jun 2026) came
back correctly parsed as on-duty and then vanished from every rollup — not short, not
flagged, not even counted among the days excluded for having nothing to measure. And on the
second of August the Worked card read "0m of 168h", presenting a whole month's work as a
debt on a day when nothing had been worked.
"""

from __future__ import annotations

from datetime import date

from cerepulse.intelligence.month import analyze_month
from cerepulse.intelligence.policy import ShiftPolicy
from cerepulse.models.attendance import AttendanceDay, DayStatus
from cerepulse.models.values import Duration

TRAINING = "EAE (EcoStruxure Automation Expert) Training in Bengaluru."


def day(
    when: date,
    *,
    status: DayStatus = DayStatus.PRESENT,
    total: str = "9.00",
    ut1: str = "DP",
    ut2: str = "---",
    remarks: str = "Attendance Muster",
) -> AttendanceDay:
    return AttendanceDay(
        day=when,
        weekday=when.strftime("%a"),
        status=status,
        user_type_1=ut1,
        user_type_2=ut2,
        total_hours=Duration.from_hhmm(total),
        remarks=remarks,
    )


def on_duty(when: date, *, ut1: str = "OD", ut2: str = "---") -> AttendanceDay:
    return day(
        when,
        status=DayStatus.ON_DUTY,
        total="0.00",
        ut1=ut1,
        ut2=ut2,
        remarks=TRAINING,
    )


# --- recognising it -----------------------------------------------------------------------


def test_an_on_duty_day_is_recognised() -> None:
    assert on_duty(date(2026, 6, 15)).has_outdoor_duty


def test_the_marker_survives_a_day_that_was_also_present() -> None:
    """Half on duty and half at a desk resolves to HALF_DAY — correctly, since half of it
    really was measured. Reading the status alone would then lose the on-duty half."""
    mixed = day(date(2026, 6, 15), status=DayStatus.HALF_DAY, ut1="OD", ut2="DP")
    assert mixed.has_outdoor_duty


def test_an_ordinary_day_is_not_outdoor_duty() -> None:
    assert not day(date(2026, 6, 22)).has_outdoor_duty


# --- counting it --------------------------------------------------------------------------


def test_on_duty_days_are_counted_rather_than_vanishing() -> None:
    days = [day(date(2026, 6, 22)), *(on_duty(date(2026, 6, d)) for d in (15, 16, 17))]
    analysis = analyze_month(days, year=2026, month=6, today=date(2026, 6, 30))

    assert analysis.on_duty_days == 3


def test_on_duty_days_are_still_not_a_deficit() -> None:
    """They are working days with nothing to measure, not days worked badly.

    Scoring them zero would invent a 24-hour shortfall the employee can neither verify nor
    act on, which is why they stay out of the bank.
    """
    days = [day(date(2026, 6, 22)), *(on_duty(date(2026, 6, d)) for d in (15, 16, 17))]
    analysis = analyze_month(days, year=2026, month=6, today=date(2026, 6, 30))

    assert analysis.short_days == 0
    assert analysis.working_days_elapsed == 1
    assert analysis.bank_delta.minutes >= 0


def test_the_reason_travels_with_the_day() -> None:
    """The remark is the entire content of an on-duty day — there are no hours to show."""
    analysis = analyze_month(
        [on_duty(date(2026, 6, 15))], year=2026, month=6, today=date(2026, 6, 30)
    )
    (rollup,) = analysis.days

    assert rollup.on_duty
    assert rollup.note == TRAINING


def test_the_routine_remark_is_not_carried_as_a_note() -> None:
    """"Attendance Muster" is on every ordinary day and says nothing; carrying it would
    bury the remarks that do say something."""
    analysis = analyze_month(
        [day(date(2026, 6, 22))], year=2026, month=6, today=date(2026, 6, 30)
    )
    assert analysis.days[0].note == ""


# --- a month that has barely started ------------------------------------------------------


def test_a_month_with_no_completed_day_owes_nothing() -> None:
    """August's first two days are a weekend. 168 hours were being presented as owed."""
    weekend = [
        day(date(2026, 8, d), status=DayStatus.WEEKLY_OFF, total="0.00", ut1="WO")
        for d in (1, 2)
    ]
    analysis = analyze_month(weekend, year=2026, month=8, today=date(2026, 8, 2))

    assert not analysis.has_started
    assert analysis.working_days_elapsed == 0
    assert analysis.elapsed_target.minutes == 0
    # The full-month figure still exists — it is a forecast, and it is labelled as one.
    assert analysis.month_target.minutes > 0


def test_the_elapsed_target_grows_with_the_days_actually_worked() -> None:
    days = [day(date(2026, 8, d)) for d in (3, 4, 5)]
    analysis = analyze_month(
        days, year=2026, month=8, policy=ShiftPolicy.default(), today=date(2026, 8, 5)
    )

    assert analysis.working_days_elapsed == 3
    assert analysis.elapsed_target == Duration(3 * 8 * 60)


def test_future_grid_rows_do_not_zero_the_projection() -> None:
    """A muster re-rendered for a selected period can carry the whole month.

    Taking the last row unconditionally put the cursor on the 31st, so 'days remaining'
    became zero and every projection silently collapsed.
    """
    whole_month = [day(date(2026, 8, d)) for d in range(1, 32)]
    analysis = analyze_month(whole_month, year=2026, month=8, today=date(2026, 8, 5))

    assert analysis.working_days_remaining > 0
