"""A synthetic year in the cache: what a long-time user's screens look like, with no portal.

Every name, figure and remark here is invented. The shapes are the real ones: a working year
of grid rows, punch logs for the recent months and grid-only rows for the older ones (the way
a real cache looks before a backfill), a day in progress, a day with an overrun lunch, comp-off
credits that were used, lapsed and are still pending, applications in every state, and swipe
requests including a ``Both``.
"""

from __future__ import annotations

import calendar
from dataclasses import asdict
from datetime import date, datetime, time, timedelta

from cerepulse.app import AppContext
from cerepulse.models.application import Application, ApplicationKind
from cerepulse.models.attendance import (
    AttendanceDay,
    AttendanceMonth,
    DayStatus,
    Punch,
    PunchDirection,
)
from cerepulse.models.leave import Holiday, LeaveBalance, LeaveTransaction
from cerepulse.models.pay import (
    CtcLine,
    CtcStatement,
    MonthlyRow,
    MonthlyStatement,
    PayLine,
    Payslip,
)
from cerepulse.models.swipe import SwipeRequest, SwipeStatus
from cerepulse.models.values import Duration
from cerepulse.repository.employee import Employee
from cerepulse.repository.leave import (
    ApplicationRepository,
    HolidayRepository,
    LeaveRepository,
    SwipeRequestRepository,
    SyncMetadataRepository,
)
from cerepulse.repository.pay import PayRepository

#: Tuesday afternoon, back from a lunch that ran long: the richest state Today has.
NOW = datetime(2026, 11, 24, 15, 40)
EMPLOYEE = Employee(code="QC0001", name="Alex Example", company_code="EXAMPLE")
#: Punch logs exist from here on; earlier months are grid-only, as before a backfill.
DETAIL_FROM = date(2026, 8, 1)

HOLIDAYS = {
    date(2026, 1, 26): "Republic Day",
    date(2026, 3, 4): "Holi",
    date(2026, 4, 3): "Good Friday",
    date(2026, 8, 15): "Independence Day",
    date(2026, 8, 28): "Raksha Bandhan",
    date(2026, 10, 2): "Gandhi Jayanti",
    date(2026, 10, 20): "Dussehra",
    date(2026, 11, 9): "Diwali",
    date(2026, 11, 10): "New Year (Vikram Samvat)",
    date(2026, 12, 25): "Christmas",
}
#: Days off, by code: CO- spends a comp-off, PL and CF are leave, OD is outdoor duty.
DAYS_OFF = {
    date(2026, 3, 16): ("LV", "PL"),
    date(2026, 8, 26): ("LV", "CO-"),
    date(2026, 11, 12): ("LV", "CO-"),
    date(2026, 11, 13): ("LV", "CO-"),
    date(2026, 6, 18): ("OD", "OD"),
    date(2026, 6, 19): ("OD", "OD"),
}
#: A weekly off that was worked, which is how a comp-off is earned.
WORKED_OFF_DAYS = (date(2026, 10, 3), date(2026, 10, 10), date(2026, 10, 24))
#: Back from a 1h45 lunch, so the break-adjusted finish differs from the flat one.
LONG_LUNCH = date(2026, 11, 23)
#: Left early, with no request filed: what Needs attention exists for.
SHORT_DAY = date(2026, 11, 18)


def seed(context: AppContext) -> None:
    """Write the synthetic year through the real repositories."""
    context.employees.save(EMPLOYEE)
    code = EMPLOYEE.code
    repo = context.attendance._attendance  # noqa: SLF001 — seeding, not production use
    meta: SyncMetadataRepository = context.attendance._sync_meta  # noqa: SLF001

    for month in range(1, NOW.month + 1):
        days = tuple(_day(day) for day in _days_of(NOW.year, month) if day <= NOW.date())
        repo.save_month(AttendanceMonth(code, NOW.year, month, days), synced_at=NOW)
        for row in days:
            if row.day >= DETAIL_FROM and row.status.counts_as_worked:
                fetched = NOW if row.day == NOW.date() else datetime.combine(row.day, time(23, 0))
                repo.save_day_detail(code, row.day, _punches(row.day), synced_at=fetched)
        meta.mark_synced(f"attendance:{NOW.year:04d}-{month:02d}", at=NOW)

    database = context.database
    HolidayRepository(database).save_all(
        Holiday(day=day, weekday=day.strftime("%a"), name=name) for day, name in HOLIDAYS.items()
    )
    leave = LeaveRepository(database)
    leave.save_balances(code, _balances())
    leave.save_transactions(code, _ledger())
    ApplicationRepository(database).save_all(code, _applications())
    SwipeRequestRepository(database).save_all(code, _swipes())
    for scope in ("leave", "swipe_requests", "applications", "holidays"):
        meta.mark_synced(scope, at=NOW)

    _seed_pay(PayRepository(database), code)


# --- attendance ------------------------------------------------------------------------


def _days_of(year: int, month: int) -> list[date]:
    return [date(year, month, d) for d in range(1, calendar.monthrange(year, month)[1] + 1)]


def _wobble(day: date, spread: int) -> int:
    """A deterministic -spread..+spread minutes, so the year is not suspiciously tidy."""
    return (day.toordinal() * 37 % (2 * spread + 1)) - spread


def _arrival(day: date) -> time:
    minutes = 9 * 60 + 10 + _wobble(day, 22)
    return time(minutes // 60, minutes % 60)


def _punches(day: date) -> tuple[Punch, ...]:
    if day == NOW.date():
        return (
            Punch(time(9, 12), PunchDirection.IN),
            Punch(time(13, 2), PunchDirection.OUT),
            Punch(time(14, 25), PunchDirection.IN),
        )
    if day == LONG_LUNCH:
        return (
            Punch(time(9, 5), PunchDirection.IN),
            Punch(time(12, 50), PunchDirection.OUT),
            Punch(time(14, 35), PunchDirection.IN),
            Punch(time(18, 40), PunchDirection.OUT),
        )
    if day == SHORT_DAY:
        return (
            Punch(time(9, 20), PunchDirection.IN),
            Punch(time(13, 10), PunchDirection.OUT),
            Punch(time(13, 55), PunchDirection.IN),
            Punch(time(16, 35), PunchDirection.OUT),
        )
    start = datetime.combine(day, _arrival(day))
    lunch_out = start + timedelta(hours=4, minutes=_wobble(day, 15))
    lunch_in = lunch_out + timedelta(minutes=45 + _wobble(day, 12))
    leave = start + timedelta(hours=9, minutes=5 + _wobble(day, 25))
    return (
        Punch(start.time(), PunchDirection.IN),
        Punch(lunch_out.time(), PunchDirection.OUT),
        Punch(lunch_in.time(), PunchDirection.IN),
        Punch(leave.time(), PunchDirection.OUT),
    )


def _day(day: date) -> AttendanceDay:
    weekday = day.strftime("%a")
    if day in HOLIDAYS:
        return AttendanceDay(day, weekday, DayStatus.HOLIDAY, user_type_1="HL")
    if day in DAYS_OFF:
        first, second = DAYS_OFF[day]
        status = DayStatus.ON_DUTY if first == "OD" else DayStatus.LEAVE
        return AttendanceDay(day, weekday, status, user_type_1=first, user_type_2=second, portion=1)
    if day.weekday() >= 5 and day not in WORKED_OFF_DAYS:
        return AttendanceDay(day, weekday, DayStatus.WEEKLY_OFF, user_type_1="WO")

    punches = _punches(day)
    first_in = punches[0].at
    last = punches[-1].at
    span = (datetime.combine(day, last) - datetime.combine(day, first_in)).seconds // 60
    return AttendanceDay(
        day,
        weekday,
        DayStatus.PRESENT,
        shift_code="GEN",
        shift_in=time(9, 0),
        shift_out=time(18, 0),
        first_in=first_in,
        last_out=last,
        user_type_1="CO+" if day in WORKED_OFF_DAYS else "DP",
        portion=1,
        total_hours=Duration(span),
    )


# --- leave -----------------------------------------------------------------------------

#: (earned, days, application status). Mirrors a real year's shape: some used, two lapsed
#: halves, two still awaiting approval.
COMP_OFF_CREDITS = (
    (date(2026, 4, 9), 0.5, SwipeStatus.APPROVED),
    (date(2026, 5, 31), 0.5, SwipeStatus.APPROVED),
    (date(2026, 7, 18), 1.0, SwipeStatus.APPROVED),
    (date(2026, 8, 4), 0.5, SwipeStatus.APPROVED),
    (date(2026, 9, 19), 1.0, SwipeStatus.APPROVED),
    (date(2026, 9, 21), 1.0, SwipeStatus.IN_PROCESS),
    (date(2026, 9, 26), 1.0, SwipeStatus.APPROVED),
    (date(2026, 10, 2), 1.0, SwipeStatus.APPROVED),
    (date(2026, 10, 3), 1.0, SwipeStatus.APPROVED),
    (date(2026, 10, 7), 1.0, SwipeStatus.APPROVED),
    (date(2026, 10, 10), 1.0, SwipeStatus.APPROVED),
    (date(2026, 10, 24), 1.0, SwipeStatus.APPROVED),
    (date(2026, 11, 19), 1.0, SwipeStatus.IN_PROCESS),
)
#: (day, days, status) for CO- leave applications.
COMP_OFF_SPENT = (
    (date(2026, 8, 26), 1.0, SwipeStatus.APPROVED),
    (date(2026, 8, 27), 0.5, SwipeStatus.APPROVED),
    (date(2026, 11, 12), 1.0, SwipeStatus.IN_PROCESS),
    (date(2026, 11, 13), 1.0, SwipeStatus.IN_PROCESS),
)
CO_TYPE = "CO- / CO+"


def _balances() -> list[LeaveBalance]:
    credited = sum(days for _day, days, _status in COMP_OFF_CREDITS)
    consumed = sum(days for _day, days, _status in COMP_OFF_SPENT)
    return [
        LeaveBalance("PL", 12.0, 0.0, 12.0),
        LeaveBalance(CO_TYPE, credited - consumed, consumed, credited),
        LeaveBalance("CF", 2.5, 0.5, 0.0),
        LeaveBalance("ML", 0.0),
    ]


def _ledger() -> list[LeaveTransaction]:
    rows: list[LeaveTransaction] = []
    running = 0.0
    events = sorted(
        [(day, days, 0.0) for day, days, _status in COMP_OFF_CREDITS]
        + [(day, 0.0, days) for day, days, _status in COMP_OFF_SPENT]
    )
    for day, credit, spent in events:
        opening = running
        running += credit - spent
        rows.append(
            LeaveTransaction(
                leave_type=CO_TYPE,
                opening_balance=opening,
                consumed_days=spent,
                credit_days=credit,
                available_balance=running,
                transaction_date=day,
                remark="Comp-off earned" if credit else "Comp-off availed",
                is_credit=credit > 0,
            )
        )
    for month in range(4, NOW.month + 1):
        rows.append(
            LeaveTransaction(
                leave_type="PL",
                opening_balance=float(month - 4),
                consumed_days=0.0,
                credit_days=1.5,
                available_balance=float(month - 3),
                transaction_date=date(NOW.year, month, 1),
                remark="Monthly credit",
                is_credit=True,
            )
        )
    return rows


def _applications() -> list[Application]:
    found: list[Application] = []
    for index, (day, days, status) in enumerate(COMP_OFF_CREDITS):
        found.append(
            Application(
                app_id=str(6000 + index),
                kind=ApplicationKind.COMP_OFF,
                start=day,
                end=day,
                days=days,
                remark="Release weekend: migration and smoke tests.",
                status=status,
            )
        )
    for index, (day, days, status) in enumerate(COMP_OFF_SPENT):
        found.append(
            Application(
                app_id=str(7000 + index),
                kind=ApplicationKind.LEAVE,
                start=day,
                end=day,
                days=days,
                remark="Family function out of town.",
                status=status,
                leave_type="CO-",
            )
        )
    found += [
        Application("7100", ApplicationKind.LEAVE, date(2026, 3, 16), date(2026, 3, 16), 1.0,
                    "Personal errand.", SwipeStatus.APPROVED, "PL"),
        Application("7101", ApplicationKind.LEAVE, date(2026, 8, 27), date(2026, 8, 27), 0.5,
                    "Second half off.", SwipeStatus.APPROVED, "CF"),
        Application("7102", ApplicationKind.LEAVE, date(2026, 12, 28), date(2026, 12, 31), 4.0,
                    "Year-end holiday.", SwipeStatus.IN_PROCESS, "PL"),
        Application("7103", ApplicationKind.LEAVE, date(2026, 7, 6), date(2026, 7, 6), 1.0,
                    "Filed twice by mistake.", SwipeStatus.REJECTED, "PL"),
        Application("7200", ApplicationKind.OUTDOOR_DUTY, date(2026, 6, 18), date(2026, 6, 19),
                    2.0, "Client site visit, Ahmedabad.", SwipeStatus.APPROVED),
    ]  # fmt: skip
    return found


def _swipes() -> list[SwipeRequest]:
    return [
        SwipeRequest(date(2026, 11, 4), "Out", None, time(18, 15), "Forgot to swipe out.",
                     SwipeStatus.IN_PROCESS, None, "Forgot", "Swipe"),
        SwipeRequest(date(2026, 10, 15), "In", time(9, 0), None, "Card not working.",
                     SwipeStatus.APPROVED, date(2026, 10, 31), "Card issue", "Swipe"),
        SwipeRequest(date(2026, 9, 8), "Both", time(9, 0), time(18, 0), "At the client's office.",
                     SwipeStatus.APPROVED, date(2026, 9, 30), "On duty", "Swipe"),
        SwipeRequest(date(2026, 9, 2), "Out", None, time(19, 5), "Late release, gate reader down.",
                     SwipeStatus.REJECTED, date(2026, 9, 10), "Forgot", "Swipe"),
        SwipeRequest(date(2026, 7, 21), "In", time(9, 5), None, "Swiped at the wrong door.",
                     SwipeStatus.LAPSED, None, "Forgot", "Swipe"),
    ]  # fmt: skip


# --- pay -------------------------------------------------------------------------------


def _seed_pay(repo: PayRepository, code: str) -> None:
    """Only on Windows, where DPAPI exists; the Pay screen shows its empty state elsewhere."""
    try:
        ctc = CtcStatement(
            for_year="2026-27",
            lines=(
                CtcLine("Basic", 40000.0, 480000.0, section="Earnings"),
                CtcLine("HRA", 20000.0, 240000.0, section="Earnings"),
                CtcLine("Special allowance", 15000.0, 180000.0, section="Earnings"),
                CtcLine("(A) Total Gross", 75000.0, 900000.0, section="Earnings", is_total=True),
                CtcLine("Gratuity", 1923.0, 23076.0, section="Benefits"),
                CtcLine("(A+C) Cost To Company", 76923.0, 923076.0, is_total=True),
            ),
        )
        repo.save(code, "ctc", "", asdict(ctc), synced_at=NOW)
        months = ("Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct")
        monthly = MonthlyStatement(
            period_label="Apr 2026 - Oct 2026",
            months=months,
            rows=(
                MonthlyRow("Gross", tuple(75000.0 for _ in months)),
                MonthlyRow("Deductions", tuple(9200.0 for _ in months)),
                MonthlyRow("Net pay", tuple(65800.0 for _ in months)),
            ),
        )
        repo.save(code, "monthly", "current", asdict(monthly), synced_at=NOW)
        for month in (8, 9, 10):
            slip = Payslip(
                period=date(2026, month, 1),
                earnings=(PayLine("Basic", 40000.0), PayLine("HRA", 20000.0),
                          PayLine("Special allowance", 15000.0)),
                deductions=(PayLine("Provident fund", 4800.0), PayLine("Income tax", 4200.0),
                            PayLine("Professional tax", 200.0)),
                variables=(),
                gross=75000.0,
                total_deductions=9200.0,
                net_pay=65800.0,
                in_words="Sixty-five thousand eight hundred only",
            )  # fmt: skip
            payload = asdict(slip)
            payload["period"] = slip.period.isoformat()
            repo.save(code, "payslip", f"{NOW.year}{month:02d}", payload, synced_at=NOW)
    except Exception:  # noqa: BLE001 — no DPAPI (CI on Linux): Pay stays empty
        return
