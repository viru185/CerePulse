"""Downloading the whole year in the background, a small piece at a time.

The app used to hold only what the user had looked at: months they never opened had no
grid, and five months of punch logs were missing because nothing ever went back for them.
These pin the unit of work — one grid, or five days' punches — its order (newest first), that
it resumes from the cache rather than from memory, that one failing day cannot stall it, and
that the next sync's pruning does not throw the year away again.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from cerepulse.core.errors import ProtocolError
from cerepulse.models.attendance import AttendanceMonth
from cerepulse.services.attendance import AttendanceService
from tests.services.conftest import EMPLOYEE, FakeGateway
from tests.services.test_attendance_service import day, punches

TODAY = date(2026, 8, 3)
PERIODS = [(2026, 8), (2026, 7)]


def seed(gateway: FakeGateway) -> None:
    gateway.months[(2026, 8)] = AttendanceMonth(
        EMPLOYEE, 2026, 8, days=tuple(day(date(2026, 8, d)) for d in (3,))
    )
    gateway.months[(2026, 7)] = AttendanceMonth(
        EMPLOYEE, 2026, 7, days=tuple(day(date(2026, 7, d)) for d in range(1, 8))
    )
    for d in range(1, 8):
        gateway.punches[date(2026, 7, d)] = punches()


def run(service: AttendanceService, *, skip: frozenset[date] = frozenset()) -> list[object]:
    steps: list[object] = []
    for _ in range(20):
        step = service.year_backfill_step(EMPLOYEE, PERIODS, today=TODAY, skip=skip)
        steps.append(step)
        if step.finished:
            return steps
        skip = skip | set(step.days) | set(step.missed)
    raise AssertionError("the backfill never finished")


def test_newest_month_first_one_grid_or_five_days_per_step(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed(gateway)
    steps = run(attendance_service)

    shape = [
        (step.period, step.grid, len(step.days))  # type: ignore[attr-defined]
        for step in steps
    ]
    assert shape == [
        ((2026, 8), True, 0),  # August's grid; today's punches are the ordinary sync's job
        ((2026, 7), True, 0),
        ((2026, 7), False, 5),  # newest five July days
        ((2026, 7), False, 2),
        (None, False, 0),
    ]
    assert gateway.detail_fetches[:5] == [date(2026, 7, d) for d in (7, 6, 5, 4, 3)]


def test_a_stopped_download_resumes_from_the_cache(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """Nothing about progress lives in memory, so a restart carries on where it was."""
    seed(gateway)
    for _ in range(3):  # two grids and one batch, then the app is closed
        attendance_service.year_backfill_step(EMPLOYEE, PERIODS, today=TODAY)

    fresh = run(attendance_service)

    assert [(s.period, len(s.days)) for s in fresh] == [((2026, 7), 2), (None, 0)]  # type: ignore[attr-defined]


def test_a_day_that_keeps_failing_is_skipped_not_asked_for_forever(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed(gateway)
    attendance_service.year_backfill_step(EMPLOYEE, PERIODS, today=TODAY)
    attendance_service.year_backfill_step(EMPLOYEE, PERIODS, today=TODAY)

    gateway.fail_detail_with = ProtocolError("500")
    first = attendance_service.year_backfill_step(EMPLOYEE, PERIODS, today=TODAY)
    assert first.missed == (date(2026, 7, 7),)

    rest = run(attendance_service, skip=frozenset(first.days + first.missed))
    assert rest[-1].finished  # type: ignore[attr-defined]


def test_download_again_refetches_grids_already_held(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed(gateway)
    run(attendance_service)
    before = gateway.month_fetches

    step = attendance_service.year_backfill_step(
        EMPLOYEE, PERIODS, today=TODAY, regrid=frozenset({(2026, 7)})
    )

    assert (step.period, step.grid) == ((2026, 7), True)
    assert gateway.month_fetches == before + 1


def test_pruning_never_drops_the_current_year(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """A three-month window would otherwise delete in the next sync what the download had
    just spent ten minutes fetching, and the next launch would fetch it all again."""
    seed(gateway)
    run(attendance_service)
    attendance_service.use_config(
        replace(
            attendance_service._config,
            sync=replace(attendance_service._config.sync, history_months=1),
        )
    )

    assert attendance_service.prune_history(EMPLOYEE, today=date(2026, 11, 24)) == 0
    assert attendance_service.prune_history(EMPLOYEE, today=date(2027, 2, 1)) == 8
