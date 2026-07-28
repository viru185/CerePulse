"""Week and month rollups, and the hours bank."""

from __future__ import annotations

from datetime import date, time

from cerepulse.intelligence.day import analyze_day
from cerepulse.intelligence.month import analyze_month, analyze_week, week_start_for
from cerepulse.intelligence.policy import ShiftPolicy
from cerepulse.models.attendance import AttendanceDay, DayStatus
from cerepulse.models.leave import Holiday
from cerepulse.models.values import Duration
from tests.intelligence.conftest import punches


def day(
    when: date,
    *,
    status: DayStatus = DayStatus.PRESENT,
    gross: str = "9.00",
    first_in: time | None = time(9, 0),
) -> AttendanceDay:
    return AttendanceDay(
        day=when,
        weekday=when.strftime("%a"),
        status=status,
        first_in=first_in,
        last_out=time(18, 0) if first_in else None,
        total_hours=Duration.from_hhmm(gross),
    )


# --- estimation -----------------------------------------------------------------------


def test_grid_only_days_are_estimated_by_removing_the_break_allowance() -> None:
    """Tot. Hrs. is a gross span, so it is not directly comparable to a work target."""
    analysis = analyze_month([day(date(2026, 7, 1), gross="9.00")], year=2026, month=7)

    assert analysis.total_worked.as_clock() == "8:00"
    assert analysis.estimated_days == 1


def test_punch_detail_overrides_the_estimate() -> None:
    when = date(2026, 7, 1)
    detail = analyze_day(punches(("09:00", "in"), ("18:30", "out")), day=when)
    analysis = analyze_month([day(when, gross="9.30")], year=2026, month=7, analyses={when: detail})

    assert analysis.total_worked.as_clock() == "9:30"  # exact, not 9:30 - 1:00
    assert analysis.estimated_days == 0


def test_weekly_offs_do_not_count_toward_the_target() -> None:
    days = [
        day(date(2026, 7, 4), status=DayStatus.WEEKLY_OFF, gross="0.00", first_in=None),
        day(date(2026, 7, 6), gross="9.00"),
    ]
    analysis = analyze_month(days, year=2026, month=7)

    assert analysis.working_days_elapsed == 1
    assert analysis.total_worked.as_clock() == "8:00"


# --- hours bank -----------------------------------------------------------------------


def test_bank_is_flat_when_every_day_hits_target() -> None:
    days = [day(date(2026, 7, d), gross="9.00") for d in (1, 2, 3)]
    analysis = analyze_month(days, year=2026, month=7, today=date(2026, 7, 31))

    assert analysis.bank_delta.minutes == 0
    assert analysis.is_ahead
    assert analysis.required_daily_average is None


def test_a_deficit_produces_a_required_daily_average() -> None:
    """Two days an hour short, with two working days left to make it up."""
    days = [day(date(2026, 7, 29), gross="8.00"), day(date(2026, 7, 30), gross="8.00")]
    analysis = analyze_month(days, year=2026, month=7, today=date(2026, 7, 30))

    assert analysis.bank_delta.as_clock() == "-2:00"
    assert not analysis.is_ahead
    # One working day left (31 Jul, a Friday): 8h target + 2h owed.
    assert analysis.working_days_remaining == 1
    assert analysis.required_daily_average.as_clock() == "10:00"


def test_being_ahead_asks_for_nothing() -> None:
    days = [day(date(2026, 7, 1), gross="11.00")]
    analysis = analyze_month(days, year=2026, month=7, today=date(2026, 7, 31))

    assert analysis.bank_delta.as_clock() == "2:00"
    assert analysis.required_daily_average is None


def test_no_remaining_days_means_no_average_to_ask_for() -> None:
    days = [day(date(2026, 7, 31), gross="7.00")]
    analysis = analyze_month(days, year=2026, month=7, today=date(2026, 7, 31))

    assert analysis.working_days_remaining == 0
    assert analysis.required_daily_average is None


# --- working-day counting -------------------------------------------------------------


def test_weekly_off_pattern_is_inferred_from_the_roster() -> None:
    """Read from how the portal marked days, not by assuming Saturday and Sunday."""
    days = [
        day(date(2026, 7, 27), gross="9.00"),  # Mon
        day(date(2026, 7, 28), gross="9.00"),  # Tue
        day(date(2026, 7, 29), status=DayStatus.WEEKLY_OFF, gross="0.00", first_in=None),  # Wed
    ]
    analysis = analyze_month(days, year=2026, month=7, today=date(2026, 7, 29))

    # 30 and 31 July remain; neither is a Wednesday, so both count.
    assert analysis.working_days_remaining == 2


def test_holidays_are_excluded_from_remaining_days() -> None:
    days = [day(date(2026, 7, 29), gross="9.00")]
    holidays = [Holiday(day=date(2026, 7, 30), weekday="Thursday", name="Company day")]
    analysis = analyze_month(days, year=2026, month=7, holidays=holidays, today=date(2026, 7, 29))
    assert analysis.working_days_remaining == 1  # only 31 July


def test_an_empty_month_is_handled() -> None:
    analysis = analyze_month([], year=2026, month=7)

    assert analysis.working_days_elapsed == 0
    assert analysis.working_days_remaining == 0
    assert analysis.total_worked.minutes == 0
    assert analysis.average_in_time is None


# --- summary statistics ---------------------------------------------------------------


def test_short_days_and_overtime_are_counted() -> None:
    days = [
        day(date(2026, 7, 1), gross="7.00"),  # 6h worked -> short
        day(date(2026, 7, 2), gross="11.00"),  # 10h worked -> overtime
    ]
    analysis = analyze_month(days, year=2026, month=7)

    assert analysis.short_days == 1
    assert analysis.total_overtime.as_clock() == "2:00"


def test_average_in_time_ignores_days_without_a_punch() -> None:
    days = [
        day(date(2026, 7, 1), first_in=time(9, 0)),
        day(date(2026, 7, 2), first_in=time(10, 0)),
        day(date(2026, 7, 4), status=DayStatus.WEEKLY_OFF, first_in=None),
    ]
    assert analyze_month(days, year=2026, month=7).average_in_time == time(9, 30)


# --- weeks ----------------------------------------------------------------------------


def test_week_totals_and_delta() -> None:
    monday = date(2026, 7, 27)
    days = [day(date(2026, 7, d), gross="9.00") for d in (27, 28, 29)]
    week = analyze_week(days, week_start=monday)

    assert week.working_days == 3
    assert week.total_worked.as_clock() == "24:00"
    assert week.target.as_clock() == "24:00"
    assert week.delta.minutes == 0


def test_week_excludes_days_outside_the_range() -> None:
    monday = date(2026, 7, 27)
    days = [day(date(2026, 7, 26), gross="9.00"), day(date(2026, 7, 27), gross="9.00")]
    assert analyze_week(days, week_start=monday).working_days == 1


def test_week_deficit_is_negative() -> None:
    week = analyze_week([day(date(2026, 7, 27), gross="7.00")], week_start=date(2026, 7, 27))
    assert week.delta.as_clock() == "-2:00"


def test_week_start_helper() -> None:
    assert week_start_for(date(2026, 7, 29)) == date(2026, 7, 27)  # Wed -> Mon
    assert week_start_for(date(2026, 7, 27)) == date(2026, 7, 27)
    assert week_start_for(date(2026, 7, 29), starts_on=6) == date(2026, 7, 26)  # Sun start


def test_a_custom_policy_changes_the_target() -> None:
    policy = ShiftPolicy(
        work_target=Duration(7 * 60), break_target=Duration(30), shift_span=Duration(450)
    )
    week = analyze_week(
        [day(date(2026, 7, 27), gross="7.30")], week_start=date(2026, 7, 27), policy=policy
    )
    assert week.total_worked.as_clock() == "7:00"
    assert week.delta.minutes == 0
