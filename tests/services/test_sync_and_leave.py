"""Session-expiry recovery, full-sync resilience, and leave workflows."""

from __future__ import annotations

from datetime import date, time

import pytest

from cerepulse.core.errors import SessionExpiredError, TransportError
from cerepulse.models.attendance import AttendanceDay, AttendanceMonth, DayStatus
from cerepulse.models.leave import Holiday, LeaveBalance, LeaveTransaction
from cerepulse.models.swipe import SwipeRequest, SwipeStatus
from cerepulse.models.values import Duration
from cerepulse.services.leave import LeaveService
from cerepulse.services.sync import SyncCoordinator
from tests.services.conftest import EMPLOYEE, FakeAuth, FakeGateway, offline

JULY = (2026, 7)


def a_day(when: date) -> AttendanceDay:
    return AttendanceDay(
        day=when,
        weekday=when.strftime("%a"),
        status=DayStatus.PRESENT,
        first_in=time(9, 0),
        last_out=time(18, 0),
        total_hours=Duration.from_hhmm("9.00"),
    )


def seed(gateway: FakeGateway) -> None:
    gateway.months[JULY] = AttendanceMonth(
        employee_code=EMPLOYEE, year=2026, month=7, days=(a_day(date(2026, 7, 1)),)
    )
    gateway.balances = [LeaveBalance(leave_type="PL", available_balance=6.0)]
    gateway.transactions = [LeaveTransaction("PL", 0.0, 0.0, 6.0, 6.0)]
    gateway.holidays = [Holiday(day=date(2026, 8, 15), weekday="Sat", name="Independence Day")]
    gateway.swipe_requests = [
        SwipeRequest(
            for_date=date(2026, 7, 24),
            direction="In",
            in_time=time(9, 0),
            out_time=None,
            remark="Work from home.",
            status=SwipeStatus.IN_PROCESS,
        )
    ]


# --- replay-once recovery -------------------------------------------------------------


def test_a_successful_operation_is_not_replayed(
    coordinator: SyncCoordinator, auth: FakeAuth
) -> None:
    calls = []
    assert coordinator.run(lambda: calls.append(1) or "ok") == "ok"
    assert len(calls) == 1
    assert auth.reauth_count == 0


def test_an_expired_session_reauthenticates_and_replays_once(
    coordinator: SyncCoordinator, auth: FakeAuth, gateway: FakeGateway
) -> None:
    attempts = []

    def operation() -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise SessionExpiredError("expired")
        return "recovered"

    assert coordinator.run(operation) == "recovered"
    assert len(attempts) == 2
    assert auth.reauth_count == 1


def test_the_menu_is_forgotten_after_reauthentication(
    coordinator: SyncCoordinator, gateway: FakeGateway
) -> None:
    """Its privilege tokens belonged to the dead session and would fail on reuse."""
    attempts = []

    def operation() -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise SessionExpiredError("expired")
        return "ok"

    coordinator.run(operation)
    assert gateway.menu_forgotten == 1


def test_expiring_twice_is_surfaced_rather_than_retried(
    coordinator: SyncCoordinator, auth: FakeAuth
) -> None:
    """A retry loop here would turn a rejected credential into an authentication storm."""
    attempts = []

    def always_expired() -> None:
        attempts.append(1)
        raise SessionExpiredError("expired")

    with pytest.raises(SessionExpiredError, match="again immediately"):
        coordinator.run(always_expired)

    assert len(attempts) == 2  # original + exactly one replay
    assert auth.reauth_count == 1


def test_a_failed_reauthentication_propagates(coordinator: SyncCoordinator, auth: FakeAuth) -> None:
    auth.reauth_fails = True
    with pytest.raises(SessionExpiredError, match="No credentials"):
        coordinator.run(lambda: (_ for _ in ()).throw(SessionExpiredError("expired")))


def test_non_session_errors_are_not_replayed(coordinator: SyncCoordinator, auth: FakeAuth) -> None:
    attempts = []

    def operation() -> None:
        attempts.append(1)
        raise TransportError("offline")

    with pytest.raises(TransportError):
        coordinator.run(operation)

    assert len(attempts) == 1
    assert auth.reauth_count == 0


# --- full sync ------------------------------------------------------------------------


def test_a_full_sync_refreshes_everything(
    coordinator: SyncCoordinator, gateway: FakeGateway
) -> None:
    seed(gateway)
    report = coordinator.sync_all(EMPLOYEE, year=2026, month=7)

    assert report.succeeded
    assert report.month_refreshed
    assert report.leave_refreshed
    assert report.swipes_refreshed
    assert report.holidays_refreshed
    assert report.detail_days_fetched == 1
    assert report.finished_at is not None


def test_one_failing_step_does_not_abandon_the_rest(
    coordinator: SyncCoordinator, gateway: FakeGateway
) -> None:
    """A leave outage should not cost the user their attendance refresh."""
    seed(gateway)
    original = gateway.fetch_leave

    def failing_leave() -> None:
        raise offline()

    gateway.fetch_leave = failing_leave  # type: ignore[method-assign]
    report = coordinator.sync_all(EMPLOYEE, year=2026, month=7)
    gateway.fetch_leave = original  # type: ignore[method-assign]

    assert not report.succeeded
    assert any("leave" in failure for failure in report.failures)
    assert report.month_refreshed  # unaffected


def test_a_total_outage_records_failures_without_raising(
    coordinator: SyncCoordinator, gateway: FakeGateway
) -> None:
    gateway.always_fail_with = offline()
    report = coordinator.sync_all(EMPLOYEE, year=2026, month=7)

    assert not report.succeeded
    assert len(report.failures) >= 3
    assert report.detail_days_fetched == 0


def test_backfill_can_be_skipped(coordinator: SyncCoordinator, gateway: FakeGateway) -> None:
    seed(gateway)
    report = coordinator.sync_all(EMPLOYEE, year=2026, month=7, backfill=False)

    assert report.month_refreshed
    assert report.detail_days_fetched == 0
    assert gateway.detail_fetches == []


def test_sync_defaults_to_the_current_month(
    coordinator: SyncCoordinator, gateway: FakeGateway
) -> None:
    seed(gateway)
    report = coordinator.sync_all(EMPLOYEE, today=date(2026, 7, 15))
    assert report.month_refreshed


# --- leave service --------------------------------------------------------------------


def test_leave_loads_and_caches(leave_service: LeaveService, gateway: FakeGateway) -> None:
    seed(gateway)
    view = leave_service.load_leave(EMPLOYEE, today=date(2026, 7, 29))

    assert [b.leave_type for b in view.balances] == ["PL"]
    assert not view.from_cache
    assert gateway.leave_fetches == 1

    cached = leave_service.load_leave(EMPLOYEE, today=date(2026, 7, 29))
    assert cached.from_cache
    assert gateway.leave_fetches == 1


def test_leave_serves_cache_during_an_outage(
    leave_service: LeaveService, gateway: FakeGateway
) -> None:
    seed(gateway)
    leave_service.load_leave(EMPLOYEE, today=date(2026, 7, 29))

    gateway.always_fail_with = offline()
    view = leave_service.load_leave(EMPLOYEE, today=date(2026, 7, 29), force_refresh=True)

    assert view.from_cache
    assert [b.leave_type for b in view.balances] == ["PL"]


def test_leave_with_no_cache_and_no_network_raises(
    leave_service: LeaveService, gateway: FakeGateway
) -> None:
    gateway.always_fail_with = offline()
    with pytest.raises(TransportError):
        leave_service.load_leave(EMPLOYEE, today=date(2026, 7, 29))


def test_expiring_leave_becomes_an_insight(
    leave_service: LeaveService, gateway: FakeGateway
) -> None:
    seed(gateway)
    gateway.balances = [LeaveBalance(leave_type="CF", available_balance=3.0)]
    view = leave_service.load_leave(EMPLOYEE, today=date(2026, 12, 10))

    assert view.insights
    assert "CF" in view.insights[0].title


def test_holidays_use_a_long_ttl(leave_service: LeaveService, gateway: FakeGateway) -> None:
    """Published once a year; re-fetching hourly would be waste."""
    seed(gateway)
    leave_service.load_holidays()
    leave_service.load_holidays()

    assert len(leave_service.load_holidays()) == 1


def test_swipe_requests_load_and_cache(leave_service: LeaveService, gateway: FakeGateway) -> None:
    seed(gateway)
    requests = leave_service.load_swipe_requests(EMPLOYEE)

    assert len(requests) == 1
    assert requests[0].is_open


def test_swipe_requests_survive_an_outage(
    leave_service: LeaveService, gateway: FakeGateway
) -> None:
    seed(gateway)
    leave_service.load_swipe_requests(EMPLOYEE)

    gateway.always_fail_with = offline()
    assert len(leave_service.load_swipe_requests(EMPLOYEE, force_refresh=True)) == 1
