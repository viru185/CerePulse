"""Attendance persistence, round-tripping, and the offline backlog."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from cerepulse.core.errors import RepositoryError
from cerepulse.models.attendance import DayStatus, PunchDirection
from cerepulse.repository.attendance import AttendanceRepository
from tests.repository.conftest import EMPLOYEE, make_day, make_month, make_punches

JUL_1 = date(2026, 7, 1)
JUL_2 = date(2026, 7, 2)


# --- round-tripping -------------------------------------------------------------------


def test_a_month_round_trips(attendance: AttendanceRepository) -> None:
    attendance.save_month(make_month(make_day(JUL_1), make_day(JUL_2)))
    loaded = attendance.find_month(EMPLOYEE, 2026, 7)

    assert loaded is not None
    assert [day.day for day in loaded.days] == [JUL_1, JUL_2]
    assert loaded.employee_code == EMPLOYEE


def test_every_field_survives_the_round_trip(attendance: AttendanceRepository) -> None:
    original = make_day(JUL_1, total="9.01")
    attendance.save_month(make_month(original))
    loaded = attendance.find_day(EMPLOYEE, JUL_1)

    assert loaded is not None
    assert loaded.weekday == original.weekday
    assert loaded.shift_code == "GS"
    assert loaded.shift_in == time(8, 0)
    assert loaded.first_in == time(9, 21)
    assert loaded.portion == 1.0
    assert loaded.user_type_1 == "DP"
    assert loaded.remarks == "Attendance Muster"
    assert loaded.status is DayStatus.PRESENT


def test_durations_survive_without_hhmm_rounding(attendance: AttendanceRepository) -> None:
    """Stored as minutes, so 9h01m comes back as 9h01m and not 9.01 hours."""
    attendance.save_month(make_month(make_day(JUL_1, total="9.01")))
    loaded = attendance.find_day(EMPLOYEE, JUL_1)

    assert loaded is not None
    assert loaded.total_hours.minutes == 541
    assert loaded.total_hours.as_clock() == "9:01"


def test_punches_round_trip_in_order(attendance: AttendanceRepository) -> None:
    attendance.save_month(make_month(make_day(JUL_1)))
    attendance.save_day_detail(EMPLOYEE, JUL_1, make_punches())

    punches = attendance.find_punches(EMPLOYEE, JUL_1)
    assert [p.direction for p in punches] == [
        PunchDirection.IN,
        PunchDirection.OUT,
        PunchDirection.IN,
        PunchDirection.OUT,
    ]
    assert punches[0].at == time(9, 21)
    assert punches[0].machine == "IN"


def test_a_day_fetched_while_it_was_still_running_gets_one_more_try(
    attendance: AttendanceRepository,
) -> None:
    """The portal answers with nothing for the day in progress, and that answer is a lie.

    Storing it as "fetched, genuinely no punches" dropped the day out of the backlog for
    good, so Today showed the single In and Out reconstructed from the grid for a day that
    really had ten punches.
    """
    attendance.save_month(make_month(make_day(JUL_1, total="9.01")))
    # Fetched during the day itself: the empty result cannot be trusted.
    attendance.save_day_detail(EMPLOYEE, JUL_1, [], synced_at=datetime(2026, 7, 1, 14, 0))

    assert attendance.days_missing_detail(EMPLOYEE, 2026, 7) == [JUL_1]


def test_a_day_still_empty_when_asked_afterwards_is_believed(
    attendance: AttendanceRepository,
) -> None:
    """Otherwise drain_detail, which loops until the backlog empties, would never finish."""
    attendance.save_month(make_month(make_day(JUL_1, total="9.01")))
    attendance.save_day_detail(EMPLOYEE, JUL_1, [], synced_at=datetime(2026, 7, 3, 9, 0))

    assert attendance.days_missing_detail(EMPLOYEE, 2026, 7) == []


def test_the_retry_stops_once_the_punches_arrive(attendance: AttendanceRepository) -> None:
    attendance.save_month(make_month(make_day(JUL_1, total="9.01")))
    attendance.save_day_detail(EMPLOYEE, JUL_1, [], synced_at=datetime(2026, 7, 1, 14, 0))
    assert attendance.days_missing_detail(EMPLOYEE, 2026, 7) == [JUL_1]

    attendance.save_day_detail(EMPLOYEE, JUL_1, make_punches())
    assert attendance.days_missing_detail(EMPLOYEE, 2026, 7) == []


def test_a_day_with_no_hours_is_left_alone(attendance: AttendanceRepository) -> None:
    """An empty punch log is only suspicious when the grid claims hours for the same day."""
    attendance.save_month(make_month(make_day(JUL_1, total="0.00")))
    attendance.save_day_detail(EMPLOYEE, JUL_1, [], synced_at=datetime(2026, 7, 1, 14, 0))

    assert attendance.days_missing_detail(EMPLOYEE, 2026, 7) == []


def test_a_month_refresh_does_not_reopen_a_settled_day(
    attendance: AttendanceRepository,
) -> None:
    """synced_at is rewritten for every row on every refresh, so it cannot be the signal."""
    attendance.save_month(make_month(make_day(JUL_1, total="9.01")))
    attendance.save_day_detail(EMPLOYEE, JUL_1, [], synced_at=datetime(2026, 7, 3, 9, 0))

    attendance.save_month(make_month(make_day(JUL_1, total="9.01")))

    assert attendance.days_missing_detail(EMPLOYEE, 2026, 7) == []


def test_missing_month_returns_none(attendance: AttendanceRepository) -> None:
    assert attendance.find_month(EMPLOYEE, 2026, 7) is None
    assert attendance.find_day(EMPLOYEE, JUL_1) is None


# --- the behaviour that protects expensive data ---------------------------------------


def test_resyncing_a_month_does_not_erase_punch_detail(
    attendance: AttendanceRepository,
) -> None:
    """The grid carries no punches, so a plain refresh must not wipe fetched detail.

    Each day's punch log costs its own postback; losing them on every sync would be both
    slow and silently wrong.
    """
    attendance.save_month(make_month(make_day(JUL_1)))
    attendance.save_day_detail(EMPLOYEE, JUL_1, make_punches())

    # A later grid-only refresh, exactly as a routine sync would produce.
    attendance.save_month(make_month(make_day(JUL_1)))

    assert len(attendance.find_punches(EMPLOYEE, JUL_1)) == 4


def test_detail_loaded_is_never_downgraded(attendance: AttendanceRepository) -> None:
    attendance.save_month(make_month(make_day(JUL_1)))
    attendance.save_day_detail(EMPLOYEE, JUL_1, make_punches())
    attendance.save_month(make_month(make_day(JUL_1, detail_loaded=False)))

    day = attendance.find_day(EMPLOYEE, JUL_1)
    assert day is not None
    assert day.detail_loaded


def test_a_day_carrying_punches_replaces_the_stored_set(
    attendance: AttendanceRepository,
) -> None:
    attendance.save_month(make_month(make_day(JUL_1)))
    attendance.save_day_detail(EMPLOYEE, JUL_1, make_punches())
    attendance.save_month(make_month(make_day(JUL_1, punches=make_punches()[:2])))

    assert len(attendance.find_punches(EMPLOYEE, JUL_1)) == 2


def test_saving_detail_for_an_uncached_day_is_a_clear_error(
    attendance: AttendanceRepository,
) -> None:
    """Better a domain error naming the cause than a bare foreign-key IntegrityError."""
    with pytest.raises(RepositoryError, match="save the month before"):
        attendance.save_day_detail(EMPLOYEE, JUL_1, make_punches())


def test_fetching_an_empty_punch_log_is_recorded_as_loaded(
    attendance: AttendanceRepository,
) -> None:
    """ "Fetched, nothing there" must not look like "not fetched yet"."""
    attendance.save_month(make_month(make_day(JUL_1)))
    attendance.save_day_detail(EMPLOYEE, JUL_1, [])

    day = attendance.find_day(EMPLOYEE, JUL_1)
    assert day is not None
    assert day.detail_loaded
    assert day.punches == ()


def test_upsert_updates_rather_than_duplicating(attendance: AttendanceRepository) -> None:
    attendance.save_month(make_month(make_day(JUL_1, total="9.00")))
    attendance.save_month(make_month(make_day(JUL_1, total="8.30")))

    month = attendance.find_month(EMPLOYEE, 2026, 7)
    assert month is not None
    assert len(month.days) == 1
    assert month.days[0].total_hours.as_clock() == "8:30"


# --- sync backlog ---------------------------------------------------------------------


def test_days_missing_detail_lists_the_backlog(attendance: AttendanceRepository) -> None:
    attendance.save_month(
        make_month(
            make_day(JUL_1),
            make_day(JUL_2, status=DayStatus.HALF_DAY),
            make_day(date(2026, 7, 4), status=DayStatus.WEEKLY_OFF),
        )
    )
    assert attendance.days_missing_detail(EMPLOYEE, 2026, 7) == [JUL_1, JUL_2]


def test_fetched_days_leave_the_backlog(attendance: AttendanceRepository) -> None:
    attendance.save_month(make_month(make_day(JUL_1), make_day(JUL_2)))
    attendance.save_day_detail(EMPLOYEE, JUL_1, make_punches())

    assert attendance.days_missing_detail(EMPLOYEE, 2026, 7) == [JUL_2]


def test_non_working_days_are_never_in_the_backlog(attendance: AttendanceRepository) -> None:
    """Weekends have no punches to fetch; queueing them would waste requests forever."""
    attendance.save_month(make_month(make_day(JUL_1, status=DayStatus.WEEKLY_OFF)))
    assert attendance.days_missing_detail(EMPLOYEE, 2026, 7) == []


# --- offline history ------------------------------------------------------------------


def test_cached_months_are_listed_newest_first(attendance: AttendanceRepository) -> None:
    attendance.save_month(make_month(make_day(date(2026, 5, 4)), year=2026, month=5))
    attendance.save_month(make_month(make_day(date(2026, 7, 1)), year=2026, month=7))
    attendance.save_month(make_month(make_day(date(2026, 6, 2)), year=2026, month=6))

    assert attendance.cached_months(EMPLOYEE) == [(2026, 7), (2026, 6), (2026, 5)]


def test_a_date_range_crosses_month_boundaries(attendance: AttendanceRepository) -> None:
    """Trends span months, which find_month cannot serve without a query each."""
    attendance.save_month(make_month(make_day(date(2026, 5, 4)), year=2026, month=5))
    attendance.save_month(make_month(make_day(date(2026, 6, 2)), year=2026, month=6))
    attendance.save_month(make_month(make_day(JUL_1), year=2026, month=7))

    found = attendance.find_days_between(EMPLOYEE, date(2026, 6, 1), date(2026, 7, 31))
    assert [day.day for day in found] == [date(2026, 6, 2), JUL_1]


def test_a_date_range_brings_its_punches_with_it(attendance: AttendanceRepository) -> None:
    attendance.save_month(make_month(make_day(JUL_1)))
    attendance.save_day_detail(EMPLOYEE, JUL_1, make_punches())

    found = attendance.find_days_between(EMPLOYEE, JUL_1, JUL_1)
    assert found[0].punches
    assert found[0].punches[0].direction is PunchDirection.IN


def test_an_empty_range_is_empty_not_an_error(attendance: AttendanceRepository) -> None:
    assert attendance.find_days_between(EMPLOYEE, date(2025, 1, 1), date(2025, 12, 31)) == []


def test_months_are_isolated_from_each_other(attendance: AttendanceRepository) -> None:
    attendance.save_month(make_month(make_day(date(2026, 6, 30)), year=2026, month=6))
    attendance.save_month(make_month(make_day(JUL_1), year=2026, month=7))

    july = attendance.find_month(EMPLOYEE, 2026, 7)
    assert july is not None
    assert [day.day for day in july.days] == [JUL_1]


def test_employees_are_isolated_from_each_other(attendance: AttendanceRepository) -> None:
    attendance.save_month(make_month(make_day(JUL_1)))
    assert attendance.find_month("OTHER001", 2026, 7) is None


def test_a_stored_sync_timestamp_is_used(attendance: AttendanceRepository) -> None:
    stamp = datetime(2026, 7, 29, 22, 45)
    attendance.save_month(make_month(make_day(JUL_1)), synced_at=stamp)

    row = attendance.database.execute(
        "SELECT synced_at FROM attendance_day WHERE day = ?", (JUL_1.isoformat(),)
    ).fetchone()
    assert row["synced_at"] == stamp.isoformat()
