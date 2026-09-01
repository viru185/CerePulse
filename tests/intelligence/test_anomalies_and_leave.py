"""Anomaly detection and leave expiry intelligence."""

from __future__ import annotations

from datetime import date, time

from cerepulse.intelligence.anomalies import AnomalyKind, detect_anomalies
from cerepulse.intelligence.day import analyze_day
from cerepulse.intelligence.insights import InsightKind, Severity
from cerepulse.intelligence.leave import (
    ExpiryBasis,
    LeavePolicy,
    analyze_leave,
    leave_insights,
)
from cerepulse.models.attendance import AttendanceDay, DayStatus
from cerepulse.models.leave import LeaveBalance, LeaveTransaction
from cerepulse.models.values import Duration
from tests.intelligence.conftest import punches

TODAY = date(2026, 7, 29)


def day(
    when: date,
    *,
    status: DayStatus = DayStatus.PRESENT,
    first_in: time | None = time(9, 0),
    last_out: time | None = time(18, 0),
    gross: str = "9.00",
) -> AttendanceDay:
    return AttendanceDay(
        day=when,
        weekday=when.strftime("%a"),
        status=status,
        first_in=first_in,
        last_out=last_out,
        total_hours=Duration.from_hhmm(gross),
    )


def kinds(anomalies) -> set[AnomalyKind]:  # type: ignore[no-untyped-def]
    return {anomaly.kind for anomaly in anomalies}


# --- anomalies ------------------------------------------------------------------------


def test_a_normal_day_is_not_an_anomaly() -> None:
    assert detect_anomalies([day(date(2026, 7, 1))]) == []


def test_weekly_offs_are_never_anomalies() -> None:
    """Otherwise every weekend would register as a zero-hours problem."""
    off = day(
        date(2026, 7, 4),
        status=DayStatus.WEEKLY_OFF,
        first_in=None,
        last_out=None,
        gross="0.00",
    )
    assert detect_anomalies([off]) == []


def test_a_working_day_with_nothing_logged_is_flagged() -> None:
    blank = day(date(2026, 7, 1), first_in=None, last_out=None, gross="0.00")
    assert kinds(detect_anomalies([blank])) == {AnomalyKind.NO_PUNCHES_ON_WORKING_DAY}


def test_clocking_in_without_out_is_flagged() -> None:
    single = day(date(2026, 7, 1), last_out=None, gross="0.00")
    assert kinds(detect_anomalies([single])) == {AnomalyKind.SINGLE_PUNCH}


def test_an_inferred_punch_is_flagged() -> None:
    when = date(2026, 7, 1)
    analysis = analyze_day(punches(("09:00", "in"), ("12:00", "in"), ("18:00", "out")), day=when)
    found = detect_anomalies([day(when)], analyses={when: analysis})
    assert AnomalyKind.MISSING_PUNCH in kinds(found)


def test_a_very_long_break_is_flagged() -> None:
    when = date(2026, 7, 1)
    analysis = analyze_day(
        punches(("09:00", "in"), ("12:00", "out"), ("14:30", "in"), ("20:00", "out")), day=when
    )
    found = detect_anomalies([day(when)], analyses={when: analysis})
    assert AnomalyKind.LONG_BREAK in kinds(found)


def test_in_time_drift_needs_a_baseline() -> None:
    """Fewer than five samples is not a personal norm, so nothing is claimed."""
    days = [day(date(2026, 7, d), first_in=time(9, 0)) for d in (1, 2)]
    days.append(day(date(2026, 7, 3), first_in=time(13, 0)))
    assert AnomalyKind.IN_TIME_DRIFT not in kinds(detect_anomalies(days))


def test_in_time_drift_is_flagged_against_the_norm() -> None:
    days = [day(date(2026, 7, d), first_in=time(9, 0)) for d in range(1, 7)]
    days.append(day(date(2026, 7, 7), first_in=time(12, 30)))

    found = [a for a in detect_anomalies(days) if a.kind is AnomalyKind.IN_TIME_DRIFT]
    assert len(found) == 1
    assert found[0].day == date(2026, 7, 7)
    assert "later" in found[0].detail


def test_drift_uses_a_median_so_outliers_do_not_hide_others() -> None:
    """A mean would be dragged by the late days and mask them."""
    days = [day(date(2026, 7, d), first_in=time(9, 0)) for d in range(1, 6)]
    days += [day(date(2026, 7, d), first_in=time(13, 0)) for d in (6, 7)]

    drifts = [a for a in detect_anomalies(days) if a.kind is AnomalyKind.IN_TIME_DRIFT]
    assert {a.day for a in drifts} == {date(2026, 7, 6), date(2026, 7, 7)}


def test_anomalies_come_back_in_date_order() -> None:
    days = [day(date(2026, 7, d), first_in=time(9, 0)) for d in range(1, 7)]
    days.append(day(date(2026, 7, 7), first_in=time(12, 30)))
    days.append(day(date(2026, 7, 8), first_in=None, last_out=None, gross="0.00"))

    found = detect_anomalies(days)
    assert [a.day for a in found] == sorted(a.day for a in found)


# --- leave expiry ---------------------------------------------------------------------


def test_carry_forward_expires_at_the_leave_year_end() -> None:
    balance = LeaveBalance(leave_type="CF", available_balance=3.0)
    outlook = analyze_leave([balance], today=TODAY)[0]

    assert outlook.expires_on == date(2026, 12, 31)
    assert outlook.basis is ExpiryBasis.LEAVE_YEAR_END
    assert not outlook.is_at_risk  # still months away


def test_carry_forward_close_to_year_end_is_at_risk() -> None:
    balance = LeaveBalance(leave_type="CF", available_balance=3.0)
    outlook = analyze_leave([balance], today=date(2026, 12, 1))[0]

    assert outlook.days_remaining == 30
    assert outlook.is_at_risk


def test_a_zero_balance_is_never_at_risk() -> None:
    balance = LeaveBalance(leave_type="CF", available_balance=0.0)
    assert not analyze_leave([balance], today=date(2026, 12, 1))[0].is_at_risk


def test_year_end_rolls_forward_once_it_has_passed() -> None:
    balance = LeaveBalance(leave_type="CF", available_balance=1.0)
    outlook = analyze_leave([balance], today=date(2026, 12, 31))[0]
    assert outlook.expires_on == date(2026, 12, 31)


def test_comp_off_expires_a_window_after_it_was_earned() -> None:
    balance = LeaveBalance(leave_type="CO- / CO+", available_balance=2.0, as_of=date(2026, 7, 1))
    outlook = analyze_leave([balance], today=TODAY)[0]

    assert outlook.basis is ExpiryBasis.EARNED_PLUS_WINDOW
    assert outlook.expires_on == date(2026, 9, 29)
    assert outlook.days_remaining == 62  # just outside the 60-day warning window
    assert not outlook.is_at_risk


def test_comp_off_inside_the_warning_window_is_at_risk() -> None:
    balance = LeaveBalance(leave_type="CO- / CO+", available_balance=2.0, as_of=date(2026, 7, 1))
    outlook = analyze_leave([balance], today=date(2026, 8, 15))[0]

    assert outlook.days_remaining == 45
    assert outlook.is_at_risk
    assert not outlook.is_expired


def test_an_already_lapsed_balance_is_expired_not_expiring() -> None:
    """A negative countdown would read as nonsense; lapsed leave is its own state."""
    balance = LeaveBalance(leave_type="CO- / CO+", available_balance=2.0, as_of=date(2026, 1, 1))
    outlook = analyze_leave([balance], today=TODAY)[0]

    assert outlook.is_expired
    assert not outlook.is_at_risk


def test_expired_balances_lead_the_insights_and_read_as_lapsed() -> None:
    balance = LeaveBalance(leave_type="CO- / CO+", available_balance=2.0, as_of=date(2026, 1, 1))
    insight = leave_insights(analyze_leave([balance], today=TODAY))[0]

    assert insight.severity is Severity.CRITICAL
    assert "have expired" in insight.title
    assert "expire in -" not in insight.title  # never a negative countdown


def test_comp_off_without_an_earned_date_reports_unknown_rather_than_guessing() -> None:
    """The portal's summary row is undated; inventing a deadline would be worse than none."""
    balance = LeaveBalance(leave_type="CO- / CO+", available_balance=2.0)
    outlook = analyze_leave([balance], today=TODAY)[0]

    assert outlook.expires_on is None
    assert outlook.basis is ExpiryBasis.UNKNOWN
    assert not outlook.is_at_risk


def test_planned_leave_lapses_at_the_end_of_the_financial_year() -> None:
    """PL had no expiry rule at all and fell through to UNKNOWN, so the largest balance most
    people hold was the one type the app could never warn about. It runs on the financial
    year, not the calendar one — the portal's own register corroborates that by starting in
    April and holding nothing from the previous February."""
    balance = LeaveBalance(leave_type="PL", available_balance=6.0)
    outlook = analyze_leave([balance], today=TODAY)[0]

    assert outlook.basis is ExpiryBasis.FINANCIAL_YEAR_END
    assert outlook.expires_on == date(2027, 3, 31)


def test_the_two_year_ends_are_different_dates() -> None:
    """Carry-forward goes at the end of December and planned leave at the end of March. One
    `leave_year_end` applied to both would have to be wrong about one of them."""
    balances = [
        LeaveBalance(leave_type="PL", available_balance=6.0),
        LeaveBalance(leave_type="CF", available_balance=3.0),
    ]
    planned, carried = analyze_leave(balances, today=date(2026, 1, 15))

    assert planned.expires_on == date(2026, 3, 31)
    assert carried.expires_on == date(2026, 12, 31)


def test_a_custom_leave_policy_is_honoured() -> None:
    policy = LeavePolicy(leave_year_end=(3, 31), comp_off_validity_days=30)
    balance = LeaveBalance(leave_type="CF", available_balance=1.0)
    outlook = analyze_leave([balance], today=TODAY, policy=policy)[0]
    assert outlook.expires_on == date(2027, 3, 31)


# --- leave insights -------------------------------------------------------------------


def test_at_risk_balances_become_insights_most_urgent_first() -> None:
    balances = [
        LeaveBalance(leave_type="CF", available_balance=3.0),  # expires 31 Dec
        LeaveBalance(  # earned + 90 days -> expires 20 Nov
            leave_type="CO- / CO+", available_balance=2.0, as_of=date(2026, 8, 22)
        ),
    ]
    insights = leave_insights(analyze_leave(balances, today=date(2026, 11, 5)))

    assert [i.kind for i in insights] == [InsightKind.LEAVE_EXPIRING] * 2
    assert "CO- / CO+" in insights[0].title  # 15 days out, ahead of CF at 56
    assert "CF" in insights[1].title


def test_an_urgent_expiry_escalates_severity() -> None:
    balance = LeaveBalance(leave_type="CF", available_balance=3.0)
    insight = leave_insights(analyze_leave([balance], today=date(2026, 12, 20)))[0]
    assert insight.severity is Severity.WARNING


def test_nothing_at_risk_produces_no_insights() -> None:
    balance = LeaveBalance(leave_type="CF", available_balance=3.0)
    assert leave_insights(analyze_leave([balance], today=TODAY)) == []


# --- comp-off, one credit at a time ------------------------------------------------------
#
# The credits below are the user's own ledger rows, verbatim. Comp-off is earned half a day
# at a time across a year and each one carries its own 90-day clock, so a single expiry date
# on the aggregate balance answers the wrong question: it says when *some* of it goes,
# without saying how much or which.


def credit(when: date, days: float, remark: str = "", kind: str = "CO- / CO+") -> LeaveTransaction:
    return LeaveTransaction(
        leave_type=kind,
        opening_balance=0.0,
        consumed_days=0.0,
        credit_days=days,
        available_balance=days,
        transaction_date=when,
        remark=remark,
    )


LEDGER = [
    credit(date(2026, 4, 9), 0.5, "RDBMSPI interface setup"),
    credit(date(2026, 5, 31), 0.5, "Completed Ignition Core Certification"),
    credit(date(2026, 7, 18), 1.0, "Newmont Extra work on Saturday"),
]
AUGUST = date(2026, 8, 1)


def test_comp_off_is_dated_per_credit_not_per_balance() -> None:
    balance = LeaveBalance(leave_type="CO- / CO+", available_balance=1.5)
    outlook = analyze_leave([balance], today=AUGUST, credits=LEDGER)[0]

    assert [(lot.days, lot.expires_on) for lot in outlook.lots] == [
        (0.5, date(2026, 8, 29)),
        (1.0, date(2026, 10, 16)),
    ]
    # The soonest, because that is the next thing the user can actually lose.
    assert outlook.expires_on == date(2026, 8, 29)
    assert outlook.basis is ExpiryBasis.EARNED_PLUS_WINDOW


def test_spending_comes_off_the_oldest_credit_first() -> None:
    """Which comp-off was taken is recorded nowhere — the ledger leaves ``consumed_days`` at
    zero on every row — so oldest-first is less a guess about this portal than the only
    defensible reading. A ledger reaching further back than the balance would otherwise
    report long-spent comp-offs as still expiring."""
    balance = LeaveBalance(leave_type="CO- / CO+", available_balance=1.0)
    outlook = analyze_leave([balance], today=AUGUST, credits=LEDGER)[0]

    assert [lot.days for lot in outlook.lots] == [1.0]
    assert outlook.lots[0].earned_on == date(2026, 7, 18)


def test_only_the_credits_inside_the_window_are_called_at_risk() -> None:
    """Saying "1.5 days expire in 28 days" when half a day is close overstates it, and an
    alert that overstates is one people stop reading."""
    balance = LeaveBalance(leave_type="CO- / CO+", available_balance=1.5)
    outlook = analyze_leave([balance], today=AUGUST, credits=LEDGER)[0]

    assert outlook.at_risk_days == 0.5
    (insight,) = leave_insights([outlook])
    assert insight.title.startswith("0.5 days")


def test_a_comp_off_deadline_says_what_it_was_counted_from() -> None:
    """The rule is 90 days from approval, and the portal publishes no approval date for
    comp-off anywhere — ``Approve Date`` is on the swipe grid alone. Counting from the earned
    date is the honest substitute, and a date someone might book leave around has to carry
    how it was arrived at."""
    balance = LeaveBalance(leave_type="CO- / CO+", available_balance=1.5)
    (insight,) = leave_insights(analyze_leave([balance], today=AUGUST, credits=LEDGER))

    assert "does not publish an approval date" in insight.detail


def test_credits_past_their_window_are_counted_not_deducted() -> None:
    """The portal's balance is the authority on what exists; the 90-day rule is company policy
    we were told about. Reporting the discrepancy is right, silently subtracting it is not."""
    balance = LeaveBalance(leave_type="CO- / CO+", available_balance=2.0)
    outlook = analyze_leave([balance], today=date(2026, 9, 1), credits=LEDGER)[0]

    assert outlook.expired_days == 1.0  # the April and May half-days
    assert outlook.balance.available_balance == 2.0


def test_another_leave_types_credits_are_not_comp_off_lots() -> None:
    """The ledger carries every type in one table, and PL's monthly accrual is by far the
    most numerous thing in it."""
    ledger = [*LEDGER, credit(date(2026, 7, 31), 1.5, "Auto Increment.PL", kind="PL")]
    balance = LeaveBalance(leave_type="CO- / CO+", available_balance=1.5)
    outlook = analyze_leave([balance], today=AUGUST, credits=ledger)[0]

    assert sum(lot.days for lot in outlook.lots) == 1.5
