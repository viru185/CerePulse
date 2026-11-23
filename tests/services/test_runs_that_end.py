"""Loops that fetch item after item stop on the failures that are every item's answer.

A lost session, a rejected password and an offline laptop say something about every request
still to come. Recorded per item, they turned a history backfill into a dozen requests
against a session the app no longer held, a Pay refresh into three "signed in somewhere
else" warnings, and a sync after waking from sleep into four retries per remaining step.
"""

from __future__ import annotations

from datetime import date

import pytest

from cerepulse.core.errors import (
    AuthenticationError,
    NetworkUnreachableError,
    SessionExpiredError,
    SessionTakenError,
)
from cerepulse.services.attendance import AttendanceService
from cerepulse.services.sync import SyncCoordinator
from tests.services.conftest import EMPLOYEE, FakeAuth, FakeGateway
from tests.services.test_attendance_service import JULY, day, seed_month


@pytest.mark.parametrize(
    "error",
    [SessionExpiredError("gone"), SessionTakenError("taken"), AuthenticationError("no")],
)
def test_a_history_backfill_stops_at_a_lost_session(
    attendance_service: AttendanceService, gateway: FakeGateway, error: Exception
) -> None:
    gateway.fail_month_with = error

    with pytest.raises(type(error)):
        attendance_service.backfill_history(EMPLOYEE, months=3, today=date(2026, 7, 29))
    assert gateway.month_fetches == 0, "no month after the first may be asked for"


def test_a_detail_backfill_stops_at_a_lost_session(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed_month(gateway, *[day(date(2026, 7, d)) for d in range(1, 6)])
    attendance_service.load_month(EMPLOYEE, *JULY)
    gateway.fail_detail_with = SessionExpiredError("gone")

    with pytest.raises(SessionExpiredError):
        attendance_service.backfill_detail(EMPLOYEE, *JULY)
    assert gateway.detail_fetches == []


def test_one_bad_day_still_does_not_abandon_the_batch(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    from cerepulse.core.errors import ParserError

    seed_month(gateway, *[day(date(2026, 7, d)) for d in range(1, 4)])
    attendance_service.load_month(EMPLOYEE, *JULY)
    gateway.fail_detail_with = ParserError("odd page")

    assert attendance_service.backfill_detail(EMPLOYEE, *JULY) == 2


def test_draining_a_month_that_holds_today_ends(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """Today is always "missing" detail so that it stays fresh, and the drain used to fetch
    it again in every batch, for ever. Each day is fetched once per drain now."""
    today = date.today()
    days = [day(today.replace(day=d)) for d in range(1, today.day + 1)]
    seed_month(gateway, *days)
    attendance_service.load_month(EMPLOYEE, today.year, today.month)

    fetched = attendance_service.drain_detail(EMPLOYEE, today.year, today.month)

    assert fetched == len(set(gateway.detail_fetches))
    assert gateway.detail_fetches.count(today) <= 1


# --- the whole sync ---------------------------------------------------------------------


class _Steps:
    """Both services at once; each step's outcome is scripted."""

    def __init__(self, fail: dict[str, Exception]) -> None:
        self.calls: list[str] = []
        self._fail = fail

    def _run(self, name: str) -> int:
        self.calls.append(name)
        if name in self._fail:
            raise self._fail[name]
        return 0

    def load_holidays(self, **_k: object) -> int:
        return self._run("holidays")

    def refresh_month(self, *_a: object) -> int:
        return self._run("attendance")

    def refresh_swipe_requests(self, *_a: object) -> int:
        return self._run("swipe requests")

    def refresh_leave(self, *_a: object) -> int:
        return self._run("leave")

    def refresh_applications(self, *_a: object) -> int:
        return self._run("applications")

    def backfill_detail(self, *_a: object) -> int:
        return self._run("day detail")

    def prune_history(self, *_a: object) -> int:
        return self._run("cache cleanup")


def _coordinator(steps: _Steps, auth: FakeAuth | None = None) -> SyncCoordinator:
    return SyncCoordinator(
        auth=auth or FakeAuth(),  # type: ignore[arg-type]
        gateway=FakeGateway(),  # type: ignore[arg-type]
        attendance=steps,  # type: ignore[arg-type]
        leave=steps,  # type: ignore[arg-type]
    )


def test_an_unreachable_portal_ends_the_sync_after_one_step() -> None:
    steps = _Steps({"attendance": NetworkUnreachableError("getaddrinfo failed")})
    report = _coordinator(steps).sync_all(EMPLOYEE)

    assert report.offline
    assert steps.calls == ["holidays", "attendance", "cache cleanup"], (
        "nothing else asks the network; the local cleanup still runs"
    )
    assert len(report.failures) == 1


def test_a_rejected_password_ends_the_sync_instead_of_signing_in_five_times() -> None:
    class Rejecting(FakeAuth):
        def reauthenticate(self) -> None:
            self.reauth_count += 1
            raise AuthenticationError("Sign-in failed")

    auth = Rejecting()
    steps = _Steps({"attendance": SessionExpiredError("gone")})

    with pytest.raises(AuthenticationError):
        _coordinator(steps, auth).sync_all(EMPLOYEE)
    assert auth.reauth_count == 1
