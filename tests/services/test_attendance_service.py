"""Cache-first attendance, offline degradation, and paced detail backfill."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from cerepulse.core.errors import TransportError
from cerepulse.models.attendance import (
    AttendanceDay,
    AttendanceMonth,
    DayStatus,
    Punch,
    PunchDirection,
)
from cerepulse.models.values import Duration
from cerepulse.services.attendance import AttendanceService
from tests.services.conftest import EMPLOYEE, FakeGateway, offline

JULY = (2026, 7)


def day(when: date, *, status: DayStatus = DayStatus.PRESENT, total: str = "9.00") -> AttendanceDay:
    return AttendanceDay(
        day=when,
        weekday=when.strftime("%a"),
        status=status,
        first_in=time(9, 0),
        last_out=time(18, 0),
        total_hours=Duration.from_hhmm(total),
    )


def seed_month(gateway: FakeGateway, *days: AttendanceDay) -> None:
    gateway.months[JULY] = AttendanceMonth(
        employee_code=EMPLOYEE, year=2026, month=7, days=tuple(days)
    )


def punches() -> list[Punch]:
    return [
        Punch(at=time(9, 0), direction=PunchDirection.IN),
        Punch(at=time(18, 0), direction=PunchDirection.OUT),
    ]


# --- cache-first ----------------------------------------------------------------------


def test_a_first_load_fetches_and_caches(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed_month(gateway, day(date(2026, 7, 1)))
    view = attendance_service.load_month(EMPLOYEE, *JULY)

    assert gateway.month_fetches == 1
    assert len(view.month.days) == 1
    assert not view.from_cache
    assert view.last_synced is not None


def test_a_second_load_inside_the_ttl_serves_cache(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """This is what makes the window open in under a second."""
    seed_month(gateway, day(date(2026, 7, 1)))
    attendance_service.load_month(EMPLOYEE, *JULY)
    view = attendance_service.load_month(EMPLOYEE, *JULY)

    assert gateway.month_fetches == 1  # not refetched
    assert view.from_cache
    assert len(view.month.days) == 1


def test_force_refresh_bypasses_the_ttl(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed_month(gateway, day(date(2026, 7, 1)))
    attendance_service.load_month(EMPLOYEE, *JULY)
    attendance_service.load_month(EMPLOYEE, *JULY, force_refresh=True)

    assert gateway.month_fetches == 2


def test_an_expired_ttl_refetches(
    attendance_service: AttendanceService, gateway: FakeGateway, repos: dict[str, object]
) -> None:
    seed_month(gateway, day(date(2026, 7, 1)))
    attendance_service.load_month(EMPLOYEE, *JULY)

    # Age the sync marker past the TTL.
    repos["sync_meta"].mark_synced(  # type: ignore[attr-defined]
        "attendance:2026-07", at=datetime(2020, 1, 1)
    )
    attendance_service.load_month(EMPLOYEE, *JULY)
    assert gateway.month_fetches == 2


# --- offline degradation --------------------------------------------------------------


def test_an_outage_after_caching_serves_stale_data_rather_than_failing(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """Offline is a normal state, not an error (Chapter 08 section 7)."""
    seed_month(gateway, day(date(2026, 7, 1)))
    attendance_service.load_month(EMPLOYEE, *JULY)

    gateway.always_fail_with = offline()
    view = attendance_service.load_month(EMPLOYEE, *JULY, force_refresh=True)

    assert view.from_cache
    assert len(view.month.days) == 1
    assert view.last_synced is not None


def test_an_outage_with_nothing_cached_raises(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    gateway.always_fail_with = offline()
    with pytest.raises(TransportError):
        attendance_service.load_month(EMPLOYEE, *JULY)


def test_cached_months_lists_offline_history(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed_month(gateway, day(date(2026, 7, 1)))
    attendance_service.load_month(EMPLOYEE, *JULY)
    assert attendance_service.cached_months(EMPLOYEE) == [(2026, 7)]


# --- backfill pacing ------------------------------------------------------------------


def test_backfill_is_bounded_per_call(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """A fresh month needs ~20 postbacks; firing them all at once would stall the refresh."""
    seed_month(gateway, *[day(date(2026, 7, d)) for d in range(1, 11)])
    attendance_service.load_month(EMPLOYEE, *JULY)

    fetched = attendance_service.backfill_detail(EMPLOYEE, *JULY, batch_size=3)
    assert fetched == 3
    assert len(gateway.detail_fetches) == 3


def test_backfill_takes_the_newest_days_first(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """Recent days are the ones a user actually opens."""
    seed_month(gateway, *[day(date(2026, 7, d)) for d in range(1, 6)])
    attendance_service.load_month(EMPLOYEE, *JULY)

    attendance_service.backfill_detail(EMPLOYEE, *JULY, batch_size=2)
    assert set(gateway.detail_fetches) == {date(2026, 7, 5), date(2026, 7, 4)}


def test_backfill_resumes_where_it_left_off(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed_month(gateway, *[day(date(2026, 7, d)) for d in range(1, 6)])
    attendance_service.load_month(EMPLOYEE, *JULY)

    attendance_service.backfill_detail(EMPLOYEE, *JULY, batch_size=2)
    attendance_service.backfill_detail(EMPLOYEE, *JULY, batch_size=2)

    assert len(set(gateway.detail_fetches)) == 4
    assert attendance_service.backfill_detail(EMPLOYEE, *JULY, batch_size=2) == 1


def test_backfill_skips_non_working_days(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed_month(
        gateway,
        day(date(2026, 7, 1)),
        day(date(2026, 7, 4), status=DayStatus.WEEKLY_OFF, total="0.00"),
    )
    attendance_service.load_month(EMPLOYEE, *JULY)
    attendance_service.backfill_detail(EMPLOYEE, *JULY)

    assert gateway.detail_fetches == [date(2026, 7, 1)]


def test_one_failed_day_does_not_abandon_the_batch(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """The failed day stays in the backlog and is retried next time."""
    seed_month(gateway, *[day(date(2026, 7, d)) for d in range(1, 4)])
    attendance_service.load_month(EMPLOYEE, *JULY)

    gateway.fail_detail_with = offline()
    fetched = attendance_service.backfill_detail(EMPLOYEE, *JULY)

    assert fetched == 2
    # The day that failed is still queued, so the next pass picks it up.
    assert attendance_service.backfill_detail(EMPLOYEE, *JULY) == 1


def test_backfill_with_nothing_pending_makes_no_requests(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed_month(gateway, day(date(2026, 7, 4), status=DayStatus.WEEKLY_OFF, total="0.00"))
    attendance_service.load_month(EMPLOYEE, *JULY)
    assert attendance_service.backfill_detail(EMPLOYEE, *JULY) == 0


def test_pending_detail_is_reported_on_the_view(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed_month(gateway, *[day(date(2026, 7, d)) for d in (1, 2, 3)])
    view = attendance_service.load_month(EMPLOYEE, *JULY)
    assert view.pending_detail == 3


# --- day analysis ---------------------------------------------------------------------


def test_load_day_fetches_detail_on_demand_and_analyzes(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    target = date(2026, 7, 1)
    seed_month(gateway, day(target))
    gateway.punches[target] = punches()
    attendance_service.load_month(EMPLOYEE, *JULY)

    analysis = attendance_service.load_day(EMPLOYEE, target)

    assert analysis.worked.as_clock() == "9:00"
    assert gateway.detail_fetches == [target]


def test_load_day_reuses_stored_punches(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    target = date(2026, 7, 1)
    seed_month(gateway, day(target))
    gateway.punches[target] = punches()
    attendance_service.load_month(EMPLOYEE, *JULY)

    attendance_service.load_day(EMPLOYEE, target)
    attendance_service.load_day(EMPLOYEE, target)

    assert gateway.detail_fetches == [target]  # fetched once


def test_the_shift_policy_comes_from_config(gateway: FakeGateway, repos: dict[str, object]) -> None:
    from dataclasses import replace

    from cerepulse.core.config import AppConfig

    base = AppConfig()
    tweaked = replace(base, shift=replace(base.shift, work_target_hours=7.5))
    service = AttendanceService(
        gateway=gateway,  # type: ignore[arg-type]
        attendance=repos["attendance"],  # type: ignore[arg-type]
        swipes=repos["swipes"],  # type: ignore[arg-type]
        holidays=repos["holidays"],  # type: ignore[arg-type]
        sync_meta=repos["sync_meta"],  # type: ignore[arg-type]
        employees=repos["employees"],  # type: ignore[arg-type]
        config=tweaked,
    )
    assert service.policy.work_target.as_clock() == "7:30"


def test_the_grid_employee_code_wins_over_the_login_name(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    gateway.months[JULY] = AttendanceMonth(
        employee_code="FROMGRID", year=2026, month=7, days=(day(date(2026, 7, 1)),)
    )
    stored = attendance_service.refresh_month("typed-by-user", *JULY)
    assert stored.employee_code == "FROMGRID"
