"""Cache-first attendance, offline degradation, and paced detail backfill."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from cerepulse.core.errors import TransportError
from cerepulse.intelligence.day import DayState
from cerepulse.intelligence.insights import InsightKind
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


def test_a_day_with_no_punch_log_falls_back_to_the_grid(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """The detail panel is empty for the day in progress; the grid row is not.

    Without this, Today reported "Nothing logged" for a day the portal plainly had hours
    for — which is exactly how a clock-in appears to go missing.
    """
    target = date(2026, 7, 1)
    seed_month(gateway, day(target, total="2.10"))
    gateway.punches[target] = []  # fetched, genuinely nothing there
    attendance_service.load_month(EMPLOYEE, *JULY)

    analysis = attendance_service.load_day(EMPLOYEE, target)

    assert analysis.state is not DayState.EMPTY
    assert analysis.first_in == datetime.combine(target, time(9, 0))
    assert analysis.last_out == datetime.combine(target, time(18, 0))
    assert InsightKind.GRID_ONLY in {i.kind for i in analysis.insights}


def test_the_grid_fallback_says_the_break_is_unknown(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """Only the in and out are real; a break inside that span is invisible."""
    target = date(2026, 7, 1)
    seed_month(gateway, day(target))
    gateway.punches[target] = []
    attendance_service.load_month(EMPLOYEE, *JULY)

    analysis = attendance_service.load_day(EMPLOYEE, target)
    note = next(i for i in analysis.insights if i.kind is InsightKind.GRID_ONLY)
    assert "not counted" in note.detail


def test_a_genuinely_empty_day_stays_empty(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """A weekend has no first-in to rebuild from, and must not gain one."""
    target = date(2026, 7, 4)
    gateway.months[JULY] = AttendanceMonth(
        employee_code=EMPLOYEE,
        year=2026,
        month=7,
        days=(
            AttendanceDay(
                day=target,
                weekday="Sat",
                status=DayStatus.WEEKLY_OFF,
                total_hours=Duration(0),
            ),
        ),
    )
    attendance_service.load_month(EMPLOYEE, *JULY)

    analysis = attendance_service.load_day(EMPLOYEE, target)
    assert analysis.state is DayState.EMPTY


def test_a_real_punch_log_is_never_replaced_by_the_grid(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """The fallback is for absence only. Real punches carry the breaks."""
    target = date(2026, 7, 1)
    seed_month(gateway, day(target))
    gateway.punches[target] = [
        Punch(at=time(9, 0), direction=PunchDirection.IN),
        Punch(at=time(13, 0), direction=PunchDirection.OUT),
        Punch(at=time(14, 0), direction=PunchDirection.IN),
        Punch(at=time(18, 0), direction=PunchDirection.OUT),
    ]
    attendance_service.load_month(EMPLOYEE, *JULY)

    analysis = attendance_service.load_day(EMPLOYEE, target)
    assert analysis.break_taken.as_clock() == "1:00"
    assert InsightKind.GRID_ONLY not in {i.kind for i in analysis.insights}


def test_load_day_speaks_in_the_configured_tone(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """Voiced in the service so the window, the tray and the toasts agree."""
    from dataclasses import replace

    target = date(2026, 7, 1)
    seed_month(gateway, day(target, total="10.00"))
    gateway.punches[target] = [
        Punch(at=time(9, 0), direction=PunchDirection.IN),
        Punch(at=time(19, 30), direction=PunchDirection.OUT),
    ]
    attendance_service.load_month(EMPLOYEE, *JULY)

    playful = attendance_service.load_day(EMPLOYEE, target)
    config = attendance_service._config
    attendance_service.use_config(replace(config, ui=replace(config.ui, tone="plain")))
    plain = attendance_service.load_day(EMPLOYEE, target)

    def overtime(analysis: object) -> str:
        return next(
            i.detail
            for i in analysis.insights  # type: ignore[attr-defined]
            if i.kind is InsightKind.OVERTIME
        )

    assert overtime(playful).startswith(overtime(plain))
    assert overtime(playful) != overtime(plain)


def test_a_saved_config_applies_without_a_restart(
    attendance_service: AttendanceService,
) -> None:
    """The service holds its own reference, so reassigning the context's alone is not enough."""
    from dataclasses import replace

    config = attendance_service._config
    attendance_service.use_config(
        replace(config, shift=replace(config.shift, work_target_hours=6.0))
    )
    assert attendance_service.policy.work_target.as_clock() == "6:00"


def test_the_grid_employee_code_wins_over_the_login_name(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    gateway.months[JULY] = AttendanceMonth(
        employee_code="FROMGRID", year=2026, month=7, days=(day(date(2026, 7, 1)),)
    )
    stored = attendance_service.refresh_month("typed-by-user", *JULY)
    assert stored.employee_code == "FROMGRID"


# --- trends -----------------------------------------------------------------------------


def seed_span(
    attendance_service: AttendanceService,
    gateway: FakeGateway,
    periods: list[tuple[int, int]],
) -> None:
    """Cache a run of ordinary weekdays across several months."""
    for year, month in periods:
        cursor = date(year, month, 1)
        days = []
        while cursor.month == month:
            status = DayStatus.WEEKLY_OFF if cursor.weekday() >= 5 else DayStatus.PRESENT
            days.append(day(cursor, status=status, total="0.00" if status.is_off else "9.00"))
            cursor += timedelta(days=1)
        gateway.months[(year, month)] = AttendanceMonth(
            employee_code=EMPLOYEE, year=year, month=month, days=tuple(days)
        )
        attendance_service.refresh_month(EMPLOYEE, year, month)


def test_trends_read_across_months_from_the_cache_alone(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """A month picker reads one month; trends need the span, without touching the portal."""
    seed_span(attendance_service, gateway, [(2026, 5), (2026, 6), (2026, 7)])
    before = gateway.month_fetches

    view = attendance_service.load_trends(EMPLOYEE, today=date(2026, 7, 15))

    assert gateway.month_fetches == before  # offline by design
    assert view.months_cached == 3
    assert [(s.year, s.month) for s in view.report.months] == [(2026, 5), (2026, 6), (2026, 7)]


def test_trends_are_bounded_by_the_configured_history_length(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed_span(attendance_service, gateway, [(2026, 4), (2026, 5), (2026, 6), (2026, 7)])
    view = attendance_service.load_trends(EMPLOYEE, today=date(2026, 7, 15), months=2)

    assert [(s.year, s.month) for s in view.report.months] == [(2026, 6), (2026, 7)]


def test_a_nearly_empty_cache_is_reported_as_thin_rather_than_analyzed(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """Four days of history is not a habit, and drawing one from it teaches distrust."""
    target = date(2026, 7, 1)
    seed_month(gateway, day(target), day(target + timedelta(days=1)))
    attendance_service.load_month(EMPLOYEE, *JULY)

    view = attendance_service.load_trends(EMPLOYEE, today=date(2026, 7, 15))
    assert view.is_thin
    assert not view.report.habits.has_enough


def test_anomalies_finally_reach_a_screen(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """detect_anomalies has been implemented and tested since Phase 3 and shown nowhere."""
    seed_span(attendance_service, gateway, [(2026, 7)])
    broken = date(2026, 7, 6)  # a Monday
    gateway.months[JULY] = AttendanceMonth(
        employee_code=EMPLOYEE,
        year=2026,
        month=7,
        days=tuple(
            AttendanceDay(
                day=d.day,
                weekday=d.weekday,
                status=d.status,
                first_in=time(9, 0) if d.day != broken else time(9, 0),
                last_out=None if d.day == broken else time(18, 0),
                total_hours=Duration(0) if d.day == broken else d.total_hours,
            )
            for d in gateway.months[JULY].days
        ),
    )
    attendance_service.refresh_month(EMPLOYEE, *JULY)

    view = attendance_service.load_trends(EMPLOYEE, today=date(2026, 7, 31))
    assert any(anomaly.day == broken for anomaly in view.anomalies)


def test_today_is_not_reported_as_an_anomaly_for_being_unfinished(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """A single punch is an anomaly last Tuesday and simply Tuesday afternoon today."""
    today = date(2026, 7, 29)
    seed_span(attendance_service, gateway, [(2026, 7)])
    gateway.months[JULY] = AttendanceMonth(
        employee_code=EMPLOYEE,
        year=2026,
        month=7,
        days=tuple(
            AttendanceDay(
                day=d.day,
                weekday=d.weekday,
                status=d.status,
                first_in=time(9, 0) if d.status.counts_as_worked else None,
                last_out=None if d.day == today else (time(18, 0) if d.first_in else None),
                total_hours=Duration(0) if d.day == today else d.total_hours,
            )
            for d in gateway.months[JULY].days
        ),
    )
    attendance_service.refresh_month(EMPLOYEE, *JULY)

    view = attendance_service.load_trends(EMPLOYEE, today=today)
    assert not any(anomaly.day == today for anomaly in view.anomalies)


def test_the_forecast_counts_the_working_days_actually_left(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed_span(attendance_service, gateway, [(2026, 7)])
    view = attendance_service.load_trends(EMPLOYEE, today=date(2026, 7, 29))

    assert view.report.forecast is not None
    # 30 and 31 July 2026 are a Thursday and a Friday.
    assert view.report.forecast.working_days_remaining == 2


# --- history backfill -----------------------------------------------------------------


def test_history_is_bounded_by_the_configured_length(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    plan = attendance_service.history_plan(EMPLOYEE, months=3, today=date(2026, 7, 29))
    assert plan == [(2026, 7), (2026, 6), (2026, 5)]


def test_history_never_reaches_into_the_future(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """The portal offers all twelve months of the year, including ones not yet lived."""
    plan = attendance_service.history_plan(EMPLOYEE, months=12, today=date(2026, 7, 29))
    assert plan[0] == (2026, 7)
    assert all(period <= (2026, 7) for period in plan)


def test_history_skips_months_already_cached(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """So a second run is cheap and a cancelled one resumes where it stopped."""
    seed_month(gateway, day(date(2026, 7, 1)))
    attendance_service.load_month(EMPLOYEE, *JULY)

    plan = attendance_service.history_plan(EMPLOYEE, months=3, today=date(2026, 7, 29))
    assert (2026, 7) not in plan
    assert plan == [(2026, 6), (2026, 5)]


def test_force_refetches_cached_months(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seed_month(gateway, day(date(2026, 7, 1)))
    attendance_service.load_month(EMPLOYEE, *JULY)

    plan = attendance_service.history_plan(
        EMPLOYEE, months=2, today=date(2026, 7, 29), include_cached=True
    )
    assert (2026, 7) in plan


def test_backfill_fetches_each_planned_month(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    report = attendance_service.backfill_history(EMPLOYEE, months=3, today=date(2026, 7, 29))

    assert report.planned == 3
    assert report.fetched == 3
    assert report.succeeded
    assert gateway.month_fetches == 3


def test_one_failing_month_does_not_abandon_the_rest(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """A gap mid-year should not cost the user everything after it."""
    gateway.fail_month_with = offline()
    report = attendance_service.backfill_history(EMPLOYEE, months=3, today=date(2026, 7, 29))

    assert report.fetched == 2
    assert len(report.failures) == 1
    assert not report.succeeded


def test_progress_can_cancel_the_backfill(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    seen: list[tuple[int, int]] = []

    def progress(done: int, total: int, period: tuple[int, int]) -> bool:
        # Called before each fetch; returning False stops without fetching that month.
        seen.append(period)
        return done <= 2

    report = attendance_service.backfill_history(
        EMPLOYEE, months=5, today=date(2026, 7, 29), on_progress=progress
    )

    assert report.cancelled
    assert report.fetched == 2
    assert len(seen) == 3  # two fetched, the third refused


def test_nothing_to_do_reports_cleanly(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    gateway.periods = []
    report = attendance_service.backfill_history(EMPLOYEE, today=date(2026, 7, 29))

    assert report.planned == 0
    assert "already up to date" in report.summary


def test_an_empty_month_is_not_refetched_forever(
    attendance_service: AttendanceService, gateway: FakeGateway
) -> None:
    """A month before the employee joined has no rows but has still been fetched.

    Keying resume on cached rows alone would re-fetch it on every history sync.
    """
    attendance_service.refresh_month(EMPLOYEE, 2026, 1)  # the fake returns no days

    assert attendance_service.cached_months(EMPLOYEE) == []
    assert (2026, 1) in attendance_service.synced_months()

    plan = attendance_service.history_plan(EMPLOYEE, months=12, today=date(2026, 7, 29))
    assert (2026, 1) not in plan
