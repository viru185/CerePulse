"""Attendance workflows: cache-first reads, paced detail backfill, and analysis.

Cache-first is what makes the window open in under a second (Chapter 06 section 3): the
cached month is returned immediately and a refresh only happens when the data has aged past
its TTL or the caller forces one.

Day detail is the expensive part — one postback per day, and a fresh month has around
twenty days needing it. Backfill is therefore paced: :meth:`AttendanceService.backfill_detail`
fetches a bounded batch per call, newest first, so the days a user actually looks at arrive
before the tail of the month.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from loguru import logger

from cerepulse.core.config import AppConfig
from cerepulse.core.errors import CerePulseError, TransportError
from cerepulse.intelligence.day import DayAnalysis, analyze_day
from cerepulse.intelligence.month import MonthAnalysis, analyze_month
from cerepulse.intelligence.policy import ShiftPolicy
from cerepulse.models.attendance import AttendanceMonth
from cerepulse.models.values import Duration
from cerepulse.repository.attendance import AttendanceRepository
from cerepulse.repository.employee import EmployeeRepository
from cerepulse.repository.leave import (
    HolidayRepository,
    SwipeRequestRepository,
    SyncMetadataRepository,
    attendance_scope,
)
from cerepulse.services.portal import PortalGateway

#: Days of detail fetched per backfill call. Each costs a postback, so this bounds how long
#: a single refresh can occupy the connection.
DEFAULT_DETAIL_BATCH = 5


@dataclass(frozen=True, slots=True)
class MonthView:
    """A month plus its analysis and freshness, which is all a screen needs."""

    month: AttendanceMonth
    analysis: MonthAnalysis
    last_synced: datetime | None
    from_cache: bool
    #: Worked days whose punch log has not been fetched yet.
    pending_detail: int

    @property
    def is_stale(self) -> bool:
        return self.last_synced is None


class AttendanceService:
    """Orchestrates attendance retrieval. Business rules live here, not in the UI."""

    def __init__(
        self,
        *,
        gateway: PortalGateway,
        attendance: AttendanceRepository,
        swipes: SwipeRequestRepository,
        holidays: HolidayRepository,
        sync_meta: SyncMetadataRepository,
        employees: EmployeeRepository,
        config: AppConfig,
    ) -> None:
        self._gateway = gateway
        self._attendance = attendance
        self._swipes = swipes
        self._holidays = holidays
        self._sync_meta = sync_meta
        self._employees = employees
        self._config = config

    # --- policy ---------------------------------------------------------------------

    @property
    def policy(self) -> ShiftPolicy:
        shift = self._config.shift
        return ShiftPolicy(
            work_target=Duration(round(shift.work_target_hours * 60)),
            break_target=Duration(round(shift.break_target_hours * 60)),
            shift_span=Duration(round(shift.shift_span_hours * 60)),
        )

    # --- reading --------------------------------------------------------------------

    def load_month(
        self,
        employee_code: str,
        year: int,
        month: int,
        *,
        force_refresh: bool = False,
        today: date | None = None,
    ) -> MonthView:
        """Return a month, refreshing from the portal only when stale or forced.

        A transport failure after a cached copy exists is not an error: the cached month is
        returned and the caller can tell it is stale from ``last_synced``.
        """
        scope = attendance_scope(year, month)
        stale = self._sync_meta.is_stale(scope, max_age_minutes=self._config.sync.cache_ttl_minutes)
        cached = self._attendance.find_month(employee_code, year, month)

        from_cache = True
        if force_refresh or stale or cached is None:
            try:
                self.refresh_month(employee_code, year, month)
                cached = self._attendance.find_month(employee_code, year, month)
                from_cache = False
            except TransportError as exc:
                if cached is None:
                    raise
                logger.warning("Refresh failed, serving cached month: {}", exc)

        if cached is None:
            cached = AttendanceMonth(employee_code=employee_code, year=year, month=month)

        return self._build_view(cached, year, month, from_cache=from_cache, today=today)

    def load_day(
        self, employee_code: str, day: date, *, now: datetime | None = None
    ) -> DayAnalysis:
        """Analyze one day from cache, fetching its punch log if not already stored."""
        cached = self._attendance.find_day(employee_code, day)
        if cached is None or not cached.detail_loaded:
            self.refresh_day_detail(employee_code, day)
            cached = self._attendance.find_day(employee_code, day)

        punches = list(cached.punches) if cached else []
        return analyze_day(
            punches,
            day=day,
            policy=self.policy,
            now=now,
            swipe_requests=self._swipes.find_all(employee_code),
        )

    def cached_months(self, employee_code: str) -> list[tuple[int, int]]:
        """Months available offline, newest first."""
        return self._attendance.cached_months(employee_code)

    # --- refreshing -----------------------------------------------------------------

    def refresh_month(self, employee_code: str, year: int, month: int) -> AttendanceMonth:
        """Fetch a month's grid and persist it. Punch detail is left to the backfill."""
        logger.info("Refreshing attendance for {:04d}-{:02d}", year, month)
        fetched, _parsed = self._gateway.fetch_month(year, month)

        # The grid carries the employee code; trust it over the login username.
        code = fetched.employee_code or employee_code
        stored = AttendanceMonth(employee_code=code, year=year, month=month, days=fetched.days)
        self._attendance.save_month(stored)
        self._sync_meta.mark_synced(attendance_scope(year, month))
        return stored

    def refresh_day_detail(self, employee_code: str, day: date) -> None:
        """Fetch and store one day's punch log."""
        _month, parsed = self._gateway.fetch_month(day.year, day.month)
        target = next((item for item in parsed if item.day.day == day), None)
        if target is None:
            logger.warning("No attendance row for {} to fetch detail for", day)
            return

        punches = self._gateway.fetch_day_detail(target)
        self._attendance.save_day_detail(employee_code, day, punches)

    def backfill_detail(
        self,
        employee_code: str,
        year: int,
        month: int,
        *,
        batch_size: int = DEFAULT_DETAIL_BATCH,
    ) -> int:
        """Fetch detail for up to ``batch_size`` days still missing it, newest first.

        Returns how many days were fetched. Bounded on purpose: a fresh month needs roughly
        twenty postbacks, and firing them all at once would stall the refresh and hammer the
        portal for data the user may never open.
        """
        pending = self._attendance.days_missing_detail(employee_code, year, month)
        if not pending:
            return 0

        batch = sorted(pending, reverse=True)[:batch_size]
        _month, parsed = self._gateway.fetch_month(year, month)
        by_date = {item.day.day: item for item in parsed}

        fetched = 0
        for day in batch:
            target = by_date.get(day)
            if target is None:
                continue
            try:
                punches = self._gateway.fetch_day_detail(target)
            except CerePulseError as exc:
                # One bad day must not abandon the batch; it stays in the backlog.
                logger.warning("Could not fetch detail for {}: {}", day, exc)
                continue
            self._attendance.save_day_detail(employee_code, day, punches)
            fetched += 1

        logger.info("Backfilled detail for {} of {} pending days", fetched, len(pending))
        return fetched

    # --- assembly -------------------------------------------------------------------

    def _build_view(
        self,
        month: AttendanceMonth,
        year: int,
        period_month: int,
        *,
        from_cache: bool,
        today: date | None,
    ) -> MonthView:
        code = month.employee_code
        analyses = {
            day.day: analyze_day(
                list(day.punches),
                day=day.day,
                policy=self.policy,
                swipe_requests=self._swipes.find_all(code),
            )
            for day in month.days
            if day.detail_loaded and day.punches
        }

        analysis = analyze_month(
            list(month.days),
            year=year,
            month=period_month,
            policy=self.policy,
            analyses=analyses,
            holidays=self._holidays.find_all(),
            today=today,
        )
        return MonthView(
            month=month,
            analysis=analysis,
            last_synced=self._sync_meta.last_synced(attendance_scope(year, period_month)),
            from_cache=from_cache,
            pending_detail=len(self._attendance.days_missing_detail(code, year, period_month)),
        )
