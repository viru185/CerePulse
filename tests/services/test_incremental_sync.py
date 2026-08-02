"""0.8: not writing what has not changed, not keeping what cannot be reached.

A past month is identical on every refresh for the rest of the year. Rewriting its
thirty-one rows each time to arrive at exactly what was already there is the kind of work
that never shows up as a bug, only as a cache that is busier than it needs to be.
"""

from __future__ import annotations

from datetime import date

from cerepulse.models.attendance import AttendanceDay, AttendanceMonth, DayStatus
from cerepulse.models.values import Duration
from cerepulse.services.attendance import AttendanceService
from cerepulse.services.provider import Provider
from tests.services.conftest import EMPLOYEE, FakeGateway
from tests.services.test_attendance_service import day, seed_month

JULY = (2026, 7)


def month(*days: AttendanceDay) -> AttendanceMonth:
    return AttendanceMonth(employee_code=EMPLOYEE, year=2026, month=7, days=days)


# --- the digest --------------------------------------------------------------------------


def test_the_same_grid_digests_the_same() -> None:
    assert month(day(date(2026, 7, 1))).content_digest() == (
        month(day(date(2026, 7, 1))).content_digest()
    )


def test_a_changed_hour_changes_the_digest() -> None:
    before = month(day(date(2026, 7, 1), total="9.00"))
    after = month(day(date(2026, 7, 1), total="9.30"))

    assert before.content_digest() != after.content_digest()


def test_a_changed_status_changes_the_digest() -> None:
    before = month(day(date(2026, 7, 1)))
    after = month(day(date(2026, 7, 1), status=DayStatus.ABSENT))

    assert before.content_digest() != after.content_digest()


def test_row_order_does_not_change_the_digest() -> None:
    """The grid's order is the vendor's business, not a change in the employee's month."""
    first, second = day(date(2026, 7, 1)), day(date(2026, 7, 2))

    assert month(first, second).content_digest() == month(second, first).content_digest()


def test_punches_are_not_part_of_the_digest() -> None:
    """It answers "has the grid changed", and the grid never carries punches.

    Including them would make a month look changed the moment its detail was fetched, and
    the fetch would then rewrite the very rows it was reading.
    """
    from datetime import time

    from cerepulse.models.attendance import Punch, PunchDirection

    plain = day(date(2026, 7, 1))
    punched = AttendanceDay(
        **{
            **{f.name: getattr(plain, f.name) for f in plain.__dataclass_fields__.values()},
            "punches": (Punch(at=time(9, 0), direction=PunchDirection.IN),),
            "detail_loaded": True,
        }
    )

    assert month(plain).content_digest() == month(punched).content_digest()


# --- skipping the write --------------------------------------------------------------------


def test_an_unchanged_month_is_not_written_again(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed_month(gateway, day(date(2026, 7, 1)), day(date(2026, 7, 2)))
    attendance_service.refresh_month(EMPLOYEE, *JULY)

    written = attendance_service._attendance
    calls: list[AttendanceMonth] = []
    original = written.save_month
    written.save_month = lambda stored, **kwargs: calls.append(stored)  # type: ignore[assignment]

    attendance_service.refresh_month(EMPLOYEE, *JULY)
    written.save_month = original  # type: ignore[assignment]

    assert calls == [], "an unchanged month should not be rewritten"


def test_a_changed_month_is_written(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed_month(gateway, day(date(2026, 7, 1), total="9.00"))
    attendance_service.refresh_month(EMPLOYEE, *JULY)

    seed_month(gateway, day(date(2026, 7, 1), total="9.45"))
    attendance_service.refresh_month(EMPLOYEE, *JULY)

    stored = attendance_service._attendance.find_day(EMPLOYEE, date(2026, 7, 1))
    assert stored is not None
    assert stored.total_hours == Duration.from_hhmm("9.45")


def test_the_freshness_stamp_still_moves_on_an_unchanged_month(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """Otherwise the sync panel reports a month as stale forever precisely because it is
    stable, and every refresh re-fetches it to find that out again."""
    from cerepulse.repository.leave import attendance_scope

    seed_month(gateway, day(date(2026, 7, 1)))
    attendance_service.refresh_month(EMPLOYEE, *JULY)
    first = attendance_service._sync_meta.last_synced(attendance_scope(*JULY))

    attendance_service.refresh_month(EMPLOYEE, *JULY)
    second = attendance_service._sync_meta.last_synced(attendance_scope(*JULY))

    assert first is not None and second is not None
    assert second >= first


# --- pruning ---------------------------------------------------------------------------


def test_pruning_keeps_the_configured_window(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed_month(gateway, day(date(2026, 7, 1)))
    attendance_service.refresh_month(EMPLOYEE, *JULY)

    # history_months defaults well above one month, so nothing inside the window goes.
    removed = attendance_service.prune_history(EMPLOYEE, today=date(2026, 7, 31))

    assert removed == 0
    assert attendance_service._attendance.find_day(EMPLOYEE, date(2026, 7, 1)) is not None


def test_pruning_drops_what_is_past_the_window(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed_month(gateway, day(date(2026, 7, 1)))
    attendance_service.refresh_month(EMPLOYEE, *JULY)

    # Far enough in the future that July falls outside any sane history window.
    removed = attendance_service.prune_history(EMPLOYEE, today=date(2030, 1, 15))

    assert removed == 1
    assert attendance_service._attendance.find_day(EMPLOYEE, date(2026, 7, 1)) is None


# --- the provider seam --------------------------------------------------------------------


def test_the_portal_gateway_satisfies_the_provider_protocol() -> None:
    """Structural, so the gateway needs no changes to satisfy it — but it must still hold.

    A second HR portal should be a second implementation rather than a rewrite, and that
    only stays true while something checks the shape.
    """
    from cerepulse.services.portal import PortalGateway

    assert issubclass(PortalGateway, Provider)


def test_the_provider_has_no_way_to_write() -> None:
    """Read-only against the HR system is a design rule, and this is the cheapest guard.

    An implementation cannot file a swipe request through an interface with no verb for it.
    """
    verbs = [name for name in dir(Provider) if not name.startswith("_")]
    assert all(name.startswith(("fetch_", "available_")) for name in verbs), verbs
