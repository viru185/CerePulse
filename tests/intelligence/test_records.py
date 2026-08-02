"""The one timeline that replaced the Leave and Requests screens.

Those two were split by which portal page the data came from — the vendor's filing system,
not the user's. A week in June was outdoor duty on the muster, a comp-off credit in the
leave ledger and a swipe request in a third list, and piecing it together meant reading two
screens and holding the dates in your head.
"""

from __future__ import annotations

from datetime import date, time, timedelta

from cerepulse.intelligence.records import RecordKind, build_records
from cerepulse.models.application import Application, ApplicationKind
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
    assert [by_day[date(2026, 7, day)].status for day in (3, 4, 5)] == [
        "Pending",
        "Rejected",
        "Approved",
    ]


def test_a_lapsed_request_needs_doing_but_is_not_a_rejection() -> None:
    """Both leave the day uncorrected. One is an answer, the other is nobody giving one,
    and reading them as the same thing loses why."""
    (entry,) = build_records(requests=[request(date(2026, 7, 3), SwipeStatus.LAPSED)])

    assert entry.needs_action
    assert entry.status == "Lapsed"
    assert not entry.pending, "nothing is waiting on anybody any more"


def test_a_decided_request_says_when_it_was_decided() -> None:
    decided = SwipeRequest(
        for_date=date(2026, 7, 3),
        direction="In",
        in_time=time(9, 0),
        out_time=None,
        remark="Extra night work",
        status=SwipeStatus.APPROVED,
        approve_date=date(2026, 7, 31),
    )
    (entry,) = build_records(requests=[decided])

    assert "decided 31 Jul" in entry.detail
    assert "9:00 AM" in entry.detail, "what was actually asked for"


def test_a_pending_request_carries_no_decision_date() -> None:
    """The portal leaves Approve Date empty until somebody decides. Printing "decided —"
    beside a request nobody has looked at would be worse than leaving it out."""
    filed = SwipeRequest(
        for_date=date(2026, 7, 3),
        direction="In",
        in_time=time(9, 0),
        out_time=None,
        remark="Extra night work",
        status=SwipeStatus.IN_PROCESS,
        approve_date=date(2026, 7, 31),
    )
    (entry,) = build_records(requests=[filed])

    assert "decided" not in entry.detail


def test_a_request_is_titled_by_the_type_the_portal_gave_it() -> None:
    """The grid's Type column, which nothing used to read. It is what will separate a swipe
    correction from the other regularisations once those lists are parsed too."""
    outdoor = SwipeRequest(
        for_date=date(2026, 7, 3),
        direction="In",
        in_time=time(9, 0),
        out_time=None,
        remark="",
        status=SwipeStatus.IN_PROCESS,
        kind="Outdoor Duty",
    )
    (entry,) = build_records(requests=[outdoor])

    assert entry.title == "Outdoor Duty request"


# --- filed applications --------------------------------------------------------------------


def application(
    start: date,
    end: date | None = None,
    *,
    kind: ApplicationKind = ApplicationKind.OUTDOOR_DUTY,
    status: SwipeStatus = SwipeStatus.APPROVED,
    days: float = 1.0,
    remark: str = "",
    leave_type: str = "",
) -> Application:
    return Application(
        app_id=f"{start:%Y%m%d}",
        kind=kind,
        start=start,
        end=end or start,
        days=days,
        remark=remark,
        status=status,
        leave_type=leave_type,
    )


def test_an_outdoor_duty_day_gains_the_status_of_the_application_behind_it() -> None:
    """The muster says the day was outdoor duty. Only the application says it was signed
    off, which is the half the screen could not show before."""
    (entry,) = build_records(
        days=[day(date(2026, 6, 15), status=DayStatus.ON_DUTY, ut1="OD", remarks=TRAINING)],
        applications=[application(date(2026, 6, 14), date(2026, 6, 19))],
    )

    assert entry.kind is RecordKind.OUTDOOR_DUTY
    assert entry.status == "Approved"


def test_an_approved_application_is_not_listed_beside_the_days_it_produced() -> None:
    """Listing both would be the same week twice — the duplication merging the two screens
    was meant to end."""
    records = build_records(
        days=[day(date(2026, 6, 14), status=DayStatus.ON_DUTY, ut1="OD")],
        applications=[application(date(2026, 6, 14), date(2026, 6, 19))],
    )

    assert len(records) == 1


def test_an_application_the_muster_knows_nothing_about_gets_its_own_row() -> None:
    """Which is the interesting set: still waiting, refused, or for a date the portal has
    not reached yet."""
    (entry,) = build_records(
        applications=[
            application(date(2026, 8, 5), status=SwipeStatus.IN_PROCESS, remark="Client site")
        ]
    )

    assert entry.title == "Outdoor duty applied for"
    assert entry.pending
    assert entry.status == "Pending"
    assert "Client site" in entry.detail


def test_a_rejected_application_needs_doing_something_about() -> None:
    (entry,) = build_records(
        applications=[application(date(2026, 7, 21), status=SwipeStatus.REJECTED)]
    )
    assert entry.needs_action


def test_a_multi_day_application_says_how_far_it_runs() -> None:
    (entry,) = build_records(
        applications=[
            application(date(2026, 6, 14), date(2026, 6, 19), days=5.5, status=SwipeStatus.LAPSED)
        ]
    )
    assert "5.5 day(s)" in entry.detail
    assert "to 19 Jun" in entry.detail


def test_a_comp_off_credit_takes_its_status_from_the_application_that_earned_it() -> None:
    (entry,) = build_records(
        transactions=[ledger(date(2026, 7, 18), credit=1.0)],
        applications=[application(date(2026, 7, 18), kind=ApplicationKind.COMP_OFF)],
    )

    assert entry.kind is RecordKind.COMP_OFF_EARNED
    assert entry.status == "Approved"


def test_a_leave_day_taken_as_comp_off_finds_its_application_in_the_comp_off_list() -> None:
    """It is leave on the muster, but the request that authorised it was filed elsewhere."""
    (entry,) = build_records(
        days=[day(date(2026, 3, 16), status=DayStatus.LEAVE)],
        applications=[
            application(date(2026, 3, 16), kind=ApplicationKind.COMP_OFF, leave_type="CO-")
        ],
    )

    assert entry.status == "Approved"


def test_a_day_covered_twice_reports_the_decided_application() -> None:
    """Rejected and refiled is a real sequence, and the decision is what applies."""
    (entry,) = build_records(
        days=[day(date(2026, 6, 15), status=DayStatus.ON_DUTY, ut1="OD")],
        applications=[
            application(date(2026, 6, 15), status=SwipeStatus.IN_PROCESS),
            application(date(2026, 6, 15), status=SwipeStatus.REJECTED),
        ],
    )

    assert entry.status == "Rejected"


def test_records_without_applications_still_carry_no_status() -> None:
    """A blank chip on every row would read as one that failed to load."""
    (entry,) = build_records(days=[day(date(2026, 6, 15), status=DayStatus.ON_DUTY, ut1="OD")])
    assert entry.status == ""


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
