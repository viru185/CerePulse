"""Trends: habits, records, month-over-month and the forecast.

The recurring theme in these tests is refusing to overstate. A median that a single late
night could move, a "record" drawn from three days, or a break figure inferred from a grid
that does not contain breaks would all be worse than showing nothing.
"""

from __future__ import annotations

from datetime import date, time, timedelta

from cerepulse.intelligence.day import analyze_day
from cerepulse.intelligence.policy import ShiftPolicy
from cerepulse.intelligence.trends import (
    MIN_SAMPLE,
    analyze_habits,
    analyze_records,
    analyze_trends,
    build_facts,
    forecast,
    summarize_months,
    working_days_left,
)
from cerepulse.models.attendance import AttendanceDay, DayStatus, Punch, PunchDirection
from cerepulse.models.values import Duration

POLICY = ShiftPolicy.default()


def workday(
    when: date,
    *,
    total: str = "9.00",
    first_in: str = "09:00",
    last_out: str | None = None,
    status: DayStatus = DayStatus.PRESENT,
    punches: tuple[Punch, ...] = (),
) -> AttendanceDay:
    return AttendanceDay(
        day=when,
        weekday=when.strftime("%a"),
        status=status,
        first_in=_time(first_in),
        last_out=_time(last_out) if last_out else None,
        total_hours=Duration.from_hhmm(total),
        punches=punches,
        detail_loaded=bool(punches),
    )


def _time(text: str) -> time:
    hour, minute = (int(part) for part in text.split(":"))
    return time(hour, minute)


def punch(clock: str, direction: str) -> Punch:
    return Punch(at=_time(clock), direction=PunchDirection.parse(direction))


def weekdays_from(start: date, count: int) -> list[date]:
    """``count`` consecutive Monday-to-Friday dates beginning on or after ``start``."""
    found: list[date] = []
    cursor = start
    while len(found) < count:
        if cursor.weekday() < 5:
            found.append(cursor)
        cursor += timedelta(days=1)
    return found


MONDAY = date(2026, 6, 1)


# --- facts ------------------------------------------------------------------------------


def test_grid_only_days_are_estimated_from_the_span() -> None:
    """Tot. Hrs. is gross, so the break allowance comes off to make it comparable."""
    facts = build_facts([workday(MONDAY, total="9.00")], policy=POLICY)

    assert len(facts) == 1
    assert facts[0].worked.as_clock() == "8:00"
    assert facts[0].estimated
    assert facts[0].break_taken is None


def test_a_day_with_punches_is_exact_and_carries_its_break() -> None:
    day = workday(
        MONDAY,
        punches=(
            punch("09:00", "in"),
            punch("13:00", "out"),
            punch("14:30", "in"),
            punch("18:30", "out"),
        ),
    )
    analyses = {MONDAY: analyze_day(list(day.punches), day=MONDAY, policy=POLICY)}
    facts = build_facts([day], policy=POLICY, analyses=analyses)

    assert not facts[0].estimated
    assert facts[0].worked.as_clock() == "8:00"
    assert facts[0].break_taken is not None
    assert facts[0].break_taken.as_clock() == "1:30"


def test_weekends_and_leave_are_not_facts() -> None:
    days = [
        workday(MONDAY, status=DayStatus.WEEKLY_OFF),
        workday(MONDAY + timedelta(days=1), status=DayStatus.LEAVE),
        workday(MONDAY + timedelta(days=2), status=DayStatus.HOLIDAY),
    ]
    assert build_facts(days, policy=POLICY) == []


def test_a_worked_day_the_portal_holds_nothing_for_is_dropped_not_scored_zero() -> None:
    """Averaging it in would drag every habit down and invent a deficit."""
    days = [workday(MONDAY, total="0.00", first_in="09:00")]
    assert build_facts(days, policy=POLICY) == []


# --- habits -----------------------------------------------------------------------------


def test_the_typical_start_is_a_median_not_a_mean() -> None:
    """One 3 AM deployment night must not become "your typical start"."""
    dates = weekdays_from(MONDAY, 5)
    days = [workday(d, first_in="09:00") for d in dates[:4]]
    days.append(workday(dates[4], first_in="03:00"))

    habits = analyze_habits(build_facts(days, policy=POLICY))
    assert habits.typical_in == time(9, 0)


def test_the_break_figure_only_ever_comes_from_punch_logs() -> None:
    """The grid contains nothing to derive a break from, so guessing would be inventing."""
    days = [workday(d) for d in weekdays_from(MONDAY, 10)]
    habits = analyze_habits(build_facts(days, policy=POLICY))

    assert habits.typical_break is None
    assert habits.break_sample == 0


def test_break_sample_counts_only_the_days_it_could_use() -> None:
    dates = weekdays_from(MONDAY, 6)
    detailed = [
        workday(
            d,
            punches=(
                punch("09:00", "in"),
                punch("13:00", "out"),
                punch("13:40", "in"),
                punch("18:00", "out"),
            ),
        )
        for d in dates[:2]
    ]
    days = detailed + [workday(d) for d in dates[2:]]
    analyses = {
        day.day: analyze_day(list(day.punches), day=day.day, policy=POLICY) for day in detailed
    }

    habits = analyze_habits(build_facts(days, policy=POLICY, analyses=analyses))
    assert habits.break_sample == 2
    assert habits.typical_break is not None
    assert habits.typical_break.as_clock() == "0:40"


def test_each_weekday_gets_its_own_shape() -> None:
    """Mondays being consistently later than Fridays is exactly the sort of thing to know."""
    days = []
    for week in range(3):
        monday = MONDAY + timedelta(days=7 * week)
        days.append(workday(monday, first_in="10:00"))
        days.append(workday(monday + timedelta(days=4), first_in="08:30"))

    habits = analyze_habits(build_facts(days, policy=POLICY))
    by_weekday = {habit.weekday: habit for habit in habits.weekdays}

    assert by_weekday[0].typical_in == time(10, 0)
    assert by_weekday[0].sample == 3
    assert by_weekday[4].typical_in == time(8, 30)


def test_drift_compares_lately_against_the_long_run() -> None:
    dates = weekdays_from(MONDAY, 30)
    days = [workday(d, first_in="09:00") for d in dates[:20]]
    days += [workday(d, first_in="10:00") for d in dates[20:]]

    habits = analyze_habits(build_facts(days, policy=POLICY))
    assert habits.in_time_drift is not None
    assert habits.in_time_drift.minutes > 0
    assert habits.drifting


def test_a_steady_month_is_not_reported_as_drifting() -> None:
    days = [workday(d, first_in="09:00") for d in weekdays_from(MONDAY, 30)]
    habits = analyze_habits(build_facts(days, policy=POLICY))

    assert habits.in_time_drift is not None
    assert habits.in_time_drift.minutes == 0
    assert not habits.drifting


def test_a_handful_of_days_is_not_enough_to_claim_a_habit() -> None:
    days = [workday(d) for d in weekdays_from(MONDAY, MIN_SAMPLE - 1)]
    assert not analyze_habits(build_facts(days, policy=POLICY)).has_enough


# --- records ----------------------------------------------------------------------------


def test_the_current_streak_counts_back_from_the_latest_day() -> None:
    dates = weekdays_from(MONDAY, 6)
    days = [workday(dates[0], total="7.00")]  # short
    days += [workday(d, total="9.00") for d in dates[1:]]

    records = analyze_records(build_facts(days, policy=POLICY), policy=POLICY)
    assert records.current_streak == 5
    assert records.days_since_short == 5


def test_a_short_day_today_ends_the_streak() -> None:
    dates = weekdays_from(MONDAY, 6)
    days = [workday(d, total="9.00") for d in dates[:5]]
    days.append(workday(dates[5], total="7.00"))

    records = analyze_records(build_facts(days, policy=POLICY), policy=POLICY)
    assert records.current_streak == 0
    assert records.days_since_short == 0
    assert records.best_streak == 5


def test_records_report_the_day_they_happened_on() -> None:
    dates = weekdays_from(MONDAY, 5)
    days = [workday(d) for d in dates]
    days[2] = workday(dates[2], total="12.00", first_in="07:15")

    records = analyze_records(build_facts(days, policy=POLICY), policy=POLICY)
    assert records.longest_day is not None
    assert records.longest_day.day == dates[2]
    assert records.earliest_start is not None
    assert records.earliest_start.day == dates[2]
    assert "7:15 AM" in records.earliest_start.value


def test_three_days_do_not_make_a_personal_record() -> None:
    """It is the maximum of three numbers, not a record."""
    days = [workday(d) for d in weekdays_from(MONDAY, 3)]
    assert not analyze_records(build_facts(days, policy=POLICY), policy=POLICY).has_enough


def test_the_best_week_is_keyed_on_its_monday() -> None:
    days = [workday(d, total="9.00") for d in weekdays_from(MONDAY, 5)]
    days += [workday(d, total="11.00") for d in weekdays_from(MONDAY + timedelta(days=7), 5)]

    records = analyze_records(build_facts(days, policy=POLICY), policy=POLICY)
    assert records.best_week is not None
    assert records.best_week.day == MONDAY + timedelta(days=7)


# --- month over month -------------------------------------------------------------------


def test_months_come_back_oldest_first_with_their_own_targets() -> None:
    may = [workday(d, total="9.00") for d in weekdays_from(date(2026, 5, 4), 5)]
    june = [workday(d, total="10.00") for d in weekdays_from(date(2026, 6, 1), 5)]

    summaries = summarize_months(build_facts(may + june, policy=POLICY), policy=POLICY)
    assert [(s.year, s.month) for s in summaries] == [(2026, 5), (2026, 6)]
    assert summaries[0].delta.minutes == 0
    assert summaries[1].delta.as_clock() == "5:00"
    assert summaries[1].label == "Jun 2026"


def test_a_month_reports_how_much_of_it_was_estimated() -> None:
    dates = weekdays_from(MONDAY, 4)
    detailed = workday(dates[0], punches=(punch("09:00", "in"), punch("18:00", "out")))
    days = [detailed] + [workday(d) for d in dates[1:]]
    analyses = {dates[0]: analyze_day(list(detailed.punches), day=dates[0], policy=POLICY)}

    summary = summarize_months(build_facts(days, policy=POLICY, analyses=analyses), policy=POLICY)[
        0
    ]
    assert summary.working_days == 4
    assert summary.estimated_days == 3


# --- forecast ---------------------------------------------------------------------------


def test_the_projection_assumes_the_recent_pace_and_says_so() -> None:
    today = date(2026, 6, 15)
    days = [workday(d, total="10.00") for d in weekdays_from(MONDAY, 10)]

    result = forecast(
        build_facts(days, policy=POLICY, analyses=None),
        policy=POLICY,
        today=today,
        working_days_remaining=5,
    )
    assert result is not None
    assert result.assumed_daily is not None
    assert result.assumed_daily.as_clock() == "9:00"  # 10:00 span minus the break allowance
    # Ten days at +1:00, plus five more projected at +1:00.
    assert result.projected_delta is not None
    assert result.projected_delta.as_clock() == "15:00"


def test_a_deficit_says_what_each_remaining_day_must_be() -> None:
    today = date(2026, 6, 15)
    days = [workday(d, total="8.00") for d in weekdays_from(MONDAY, 10)]  # 7h worked each

    result = forecast(
        build_facts(days, policy=POLICY), policy=POLICY, today=today, working_days_remaining=5
    )
    assert result is not None
    assert result.headroom.minutes == -600  # ten hours down
    assert result.required_daily is not None
    assert result.required_daily.as_clock() == "10:00"


def test_a_short_day_is_only_affordable_once_it_is_actually_paid_for() -> None:
    today = date(2026, 6, 15)
    thin = [workday(d, total="9.30") for d in weekdays_from(MONDAY, 4)]  # +0:30 each
    fat = [workday(d, total="11.00") for d in weekdays_from(MONDAY, 4)]  # +2:00 each

    assert not forecast(
        build_facts(thin, policy=POLICY), policy=POLICY, today=today, working_days_remaining=5
    ).short_day_affordable
    assert forecast(
        build_facts(fat, policy=POLICY), policy=POLICY, today=today, working_days_remaining=5
    ).short_day_affordable


def test_no_days_this_month_means_no_forecast() -> None:
    days = [workday(d) for d in weekdays_from(date(2026, 4, 1), 5)]
    assert (
        forecast(
            build_facts(days, policy=POLICY),
            policy=POLICY,
            today=date(2026, 6, 15),
            working_days_remaining=5,
        )
        is None
    )


# --- the month rollup agrees with the forecast ------------------------------------------


def test_a_day_still_being_worked_is_not_scored_against_a_full_target() -> None:
    """It shrinks by itself as the afternoon passes, which is the signature of a fake number.

    Both screens read the same month, so a partial day counted as short in one and skipped
    in the other made Attendance and Insights disagree about the bank by exactly eight hours.
    """
    from cerepulse.intelligence.month import analyze_month

    today = date(2026, 6, 10)  # a Wednesday
    settled = [workday(d, total="8.00") for d in weekdays_from(MONDAY, 7) if d < today]
    live = AttendanceDay(
        day=today,
        weekday="Wed",
        status=DayStatus.PRESENT,
        first_in=time(9, 0),
        last_out=None,
        total_hours=Duration(0),
    )

    analysis = analyze_month(settled + [live], year=2026, month=6, today=today)
    rollup = next(r for r in analysis.days if r.day == today)

    assert rollup.in_progress
    assert analysis.working_days_elapsed == len(settled)
    # Excluded from the bank, and owed as a day still to come rather than lost from both.
    assert analysis.bank_delta.minutes == -60 * len(settled)


def test_a_finished_day_today_still_counts() -> None:
    today = date(2026, 6, 10)
    days = [workday(d, total="9.00") for d in weekdays_from(MONDAY, 7) if d <= today]

    from cerepulse.intelligence.month import analyze_month

    analysis = analyze_month(days, year=2026, month=6, today=today)
    assert not any(r.in_progress for r in analysis.days)
    assert analysis.working_days_elapsed == len(days)


# --- working days left ------------------------------------------------------------------


def test_working_days_left_skips_the_roster_and_holidays() -> None:
    # Wednesday 24 June 2026; the rest of the month is 25, 26, 29, 30 minus one holiday.
    left = working_days_left(date(2026, 6, 24), off_weekdays={5, 6}, holidays={date(2026, 6, 26)})
    assert left == 3


def test_an_unfinished_today_is_counted_as_a_day_still_to_come() -> None:
    """It contributed no measured hours, so leaving it out of both sides erases it."""
    left = working_days_left(
        date(2026, 6, 24), off_weekdays={5, 6}, holidays=set(), including_today=True
    )
    assert left == 5


# --- the whole report -------------------------------------------------------------------


def test_the_report_states_how_much_of_it_is_exact() -> None:
    dates = weekdays_from(MONDAY, 6)
    detailed = workday(dates[0], punches=(punch("09:00", "in"), punch("18:00", "out")))
    days = [detailed] + [workday(d) for d in dates[1:]]
    analyses = {dates[0]: analyze_day(list(detailed.punches), day=dates[0], policy=POLICY)}

    report = analyze_trends(days, policy=POLICY, analyses=analyses, today=date(2026, 6, 15))
    assert report.measured_days == 6
    assert report.estimated_days == 5
    assert report.exact_days == 1
    assert report.span == (dates[0], dates[-1])


def test_an_empty_history_reports_nothing_rather_than_zeroes() -> None:
    report = analyze_trends([], policy=POLICY, today=date(2026, 6, 15))

    assert report.measured_days == 0
    assert report.span is None
    assert report.forecast is None
    assert not report.habits.has_enough
    assert not report.records.has_enough
    assert report.months == ()
