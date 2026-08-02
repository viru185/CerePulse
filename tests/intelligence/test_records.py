"""The one timeline that replaced the Leave and Requests screens.

Those two were split by which portal page the data came from — the vendor's filing system,
not the user's. A week in June was outdoor duty on the muster, a comp-off credit in the
leave ledger and a swipe request in a third list, and piecing it together meant reading two
screens and holding the dates in your head.
"""

from __future__ import annotations

from datetime import date, time, timedelta

from cerepulse.intelligence.records import RecordKind, build_records
from cerepulse.models.attendance import AttendanceDay, DayStatus
from cerepulse.models.leave import Holiday, LeaveTransaction
from cerepulse.models.swipe import SwipeRequest, SwipeStatus
from cerepulse.models.values import Duration

TRAINING = "EAE (EcoStruxure Automation Expert) Training in Bengaluru."


def day(
    when: date,
    *,
    status: DayStatus = DayStatus.PRESENT,
    ut1: str = "DP",
    remarks: str = "Attendance Muster",
) -> AttendanceDay:
    return AttendanceDay(
        day=when,
        weekday=when.strftime("%a"),
        status=status,
        user_type_1=ut1,
        total_hours=Duration(540),
        remarks=remarks,
    )


def request(when: date, status: SwipeStatus) -> SwipeRequest:
    return SwipeRequest(
        for_date=when,
        direction="In",
        in_time=time(9, 0),
        out_time=None,
        remark="Extra night work",
        status=status,
    )


def holiday(when: date, name: str) -> Holiday:
    return Holiday(day=when, weekday=when.strftime("%a"), name=name)


def ledger(when: date | None, *, credit: float = 0.0, consumed: float = 0.0) -> LeaveTransaction:
    return LeaveTransaction(
        leave_type="CO- / CO+",
        opening_balance=0.0,
        consumed_days=consumed,
        credit_days=credit,
        available_balance=1.0,
        transaction_date=when,
    )


# --- what gets in -------------------------------------------------------------------------


def test_an_ordinary_working_day_is_not_a_record() -> None:
    """A record of everything is just a copy of the attendance table."""
    assert build_records(days=[day(date(2026, 6, 22))]) == []


def test_outdoor_duty_carries_its_reason() -> None:
    records = build_records(
        days=[day(date(2026, 6, 15), status=DayStatus.ON_DUTY, ut1="OD", remarks=TRAINING)]
    )
    (entry,) = records
    assert entry.kind is RecordKind.OUTDOOR_DUTY
    assert entry.detail == TRAINING


def test_leave_absence_and_half_days_appear() -> None:
    records = build_records(
        days=[
            day(date(2026, 6, 1), status=DayStatus.LEAVE),
            day(date(2026, 6, 2), status=DayStatus.ABSENT),
            day(date(2026, 6, 3), status=DayStatus.HALF_DAY),
        ]
    )
    assert {entry.kind for entry in records} == {RecordKind.LEAVE, RecordKind.ABSENCE}


def test_an_absence_needs_doing_something_about() -> None:
    (entry,) = build_records(days=[day(date(2026, 6, 2), status=DayStatus.ABSENT)])
    assert entry.needs_action


def test_swipe_requests_carry_their_state() -> None:
    records = build_records(
        requests=[
            request(date(2026, 7, 3), SwipeStatus.IN_PROCESS),
            request(date(2026, 7, 4), SwipeStatus.REJECTED),
            request(date(2026, 7, 5), SwipeStatus.APPROVED),
        ]
    )
    by_day = {entry.day: entry for entry in records}
    assert by_day[date(2026, 7, 3)].pending
    assert by_day[date(2026, 7, 4)].needs_action
    assert by_day[date(2026, 7, 5)].is_settled


def test_comp_off_movements_become_entries() -> None:
    records = build_records(
        transactions=[
            ledger(date(2026, 6, 20), credit=1.0),
            ledger(date(2026, 7, 10), consumed=1.0),
        ]
    )
    assert {entry.kind for entry in records} == {
        RecordKind.COMP_OFF_EARNED,
        RecordKind.COMP_OFF_SPENT,
    }


def test_an_undated_ledger_row_cannot_go_on_a_timeline() -> None:
    """The same reason comp-off expiry is reported as unknown rather than invented."""
    assert build_records(transactions=[ledger(None, credit=1.0)]) == []


def test_non_comp_off_ledger_rows_are_left_out() -> None:
    """They are running balances, not events."""
    planned = LeaveTransaction(
        leave_type="PL",
        opening_balance=5.0,
        consumed_days=1.0,
        credit_days=0.0,
        available_balance=4.0,
        transaction_date=date(2026, 6, 20),
    )
    assert build_records(transactions=[planned]) == []


def test_holidays_are_bounded_by_the_attendance_range() -> None:
    """The holiday calendar runs a year ahead; listing all of it buries what happened."""
    records = build_records(
        days=[day(date(2026, 6, 1)), day(date(2026, 6, 30))],
        holidays=[
            holiday(date(2026, 6, 15), "Inside"),
            holiday(date(2026, 12, 25), "Months away"),
        ],
    )
    holidays = [entry for entry in records if entry.kind is RecordKind.HOLIDAY]
    assert [entry.title for entry in holidays] == ["Inside"]


def test_holidays_need_an_attendance_range_to_be_bounded_by() -> None:
    records = build_records(holidays=[holiday(date(2026, 6, 15), "Anything")])
    assert records == []


# --- ordering -----------------------------------------------------------------------------


def test_the_timeline_runs_newest_first() -> None:
    records = build_records(
        days=[
            day(date(2026, 6, 1), status=DayStatus.LEAVE),
            day(date(2026, 6, 20), status=DayStatus.LEAVE),
        ]
    )
    assert [entry.day for entry in records] == [date(2026, 6, 20), date(2026, 6, 1)]


def test_within_one_day_the_thing_needing_action_leads() -> None:
    """A rejection and a holiday on one date should not be ordered by which list they
    came from."""
    when = date(2026, 6, 15)
    records = build_records(
        days=[day(when), day(when + timedelta(days=1))],
        holidays=[holiday(when, "Company holiday")],
        requests=[request(when, SwipeStatus.REJECTED)],
    )
    same_day = [entry for entry in records if entry.day == when]
    assert same_day[0].needs_action


# --- everything at once ---------------------------------------------------------------------


def test_all_four_sources_merge_into_one_stream() -> None:
    """The whole point: one place to answer "what happened to my time"."""
    records = build_records(
        days=[
            day(date(2026, 6, 15), status=DayStatus.ON_DUTY, ut1="OD", remarks=TRAINING),
            day(date(2026, 6, 10), status=DayStatus.LEAVE),
            day(date(2026, 6, 22)),
        ],
        requests=[request(date(2026, 6, 18), SwipeStatus.IN_PROCESS)],
        transactions=[ledger(date(2026, 6, 20), credit=1.0)],
        holidays=[holiday(date(2026, 6, 12), "Company holiday")],
    )

    kinds = [entry.kind for entry in records]
    assert RecordKind.OUTDOOR_DUTY in kinds
    assert RecordKind.LEAVE in kinds
    assert RecordKind.SWIPE_REQUEST in kinds
    assert RecordKind.COMP_OFF_EARNED in kinds
    assert RecordKind.HOLIDAY in kinds
    # And the ordinary working day is still not among them.
    assert date(2026, 6, 22) not in {entry.day for entry in records}
