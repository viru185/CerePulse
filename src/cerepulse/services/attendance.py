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

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

from loguru import logger

from cerepulse.core.config import AppConfig
from cerepulse.core.errors import CerePulseError, TransportError
from cerepulse.intelligence.anomalies import Anomaly, detect_anomalies
from cerepulse.intelligence.attention import Attention, find_attention, swipe_index
from cerepulse.intelligence.day import DayAnalysis, analyze_day
from cerepulse.intelligence.month import MonthAnalysis, analyze_month
from cerepulse.intelligence.policy import ShiftPolicy
from cerepulse.intelligence.trends import (
    MIN_SAMPLE,
    TrendReport,
    analyze_trends,
    working_days_left,
)
from cerepulse.intelligence.voice import Tone, voice_day
from cerepulse.models.attendance import AttendanceDay, AttendanceMonth, DayStatus
from cerepulse.models.swipe import SwipeRequest
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


@dataclass(slots=True)
class HistoryReport:
    """What a backfill managed, and what it could not."""

    planned: int = 0
    fetched: int = 0
    cancelled: bool = False
    failures: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return not self.failures and not self.cancelled

    @property
    def summary(self) -> str:
        if self.planned == 0:
            return "History is already up to date."
        if self.cancelled:
            return f"Stopped after {self.fetched} of {self.planned} months."
        if self.failures:
            return f"Fetched {self.fetched} of {self.planned} months; {len(self.failures)} failed."
        return f"Fetched {self.fetched} month(s) of history."


@dataclass(frozen=True, slots=True)
class TrendsView:
    """The Insights screen's payload: the report, plus how much history backs it."""

    report: TrendReport
    anomalies: list[Anomaly]
    #: Months with cached rows inside the window the report covers.
    months_cached: int
    #: Months ever synced, including ones that turned out to be empty.
    months_available: int
    #: The day the report was built for, so the view can mark the month still in progress.
    today: date

    @property
    def is_thin(self) -> bool:
        """Whether there is too little history for the screen to say anything useful."""
        return self.report.measured_days < MIN_SAMPLE


@dataclass(frozen=True, slots=True)
class MonthView:
    """A month plus its analysis and freshness, which is all a screen needs."""

    month: AttendanceMonth
    analysis: MonthAnalysis
    last_synced: datetime | None
    from_cache: bool
    #: Worked days whose punch log has not been fetched yet.
    pending_detail: int
    #: Filed swipe requests for this month, grouped by the day they are for.
    swipes: dict[date, list[SwipeRequest]] = field(default_factory=dict)
    #: Days with something outstanding, keyed by date.
    attention: dict[date, Attention] = field(default_factory=dict)

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

    def use_config(self, config: AppConfig) -> None:
        """Adopt a newly saved configuration.

        Everything derived from config — the shift policy, the cache TTL, the tone — is read
        through ``self._config`` at call time rather than snapshotted, so this is all it
        takes for Settings to apply without a restart.
        """
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
        analysis = analyze_day(
            punches,
            day=day,
            policy=self.policy,
            now=now,
            swipe_requests=self._swipes.find_all(employee_code),
        )
        # Voiced here rather than in the views, so the window, the tray tooltip and the
        # notifications all say the same thing about the same day.
        return voice_day(analysis, tone=Tone.parse(self._config.ui.tone))

    def load_trends(
        self,
        employee_code: str,
        *,
        today: date | None = None,
        months: int | None = None,
    ) -> TrendsView:
        """Assemble the Insights screen from cached history. Never touches the network.

        Offline by design: history is expensive to fetch and cheap to read, so this reads
        whatever the cache holds and reports how much that is rather than triggering a sync
        the user did not ask for.
        """
        now = today or date.today()
        span = months if months is not None else self._config.sync.history_months
        start = _months_before(date(now.year, now.month, 1), span - 1)
        days = self._attendance.find_days_between(employee_code, start, now)

        analyses = {
            day.day: analyze_day(list(day.punches), day=day.day, policy=self.policy)
            for day in days
            if day.detail_loaded and day.punches
        }

        this_month = [day for day in days if (day.day.year, day.day.month) == (now.year, now.month)]
        # An unfinished today contributes no measured hours, so it has to be counted as a
        # day still to come or it falls out of the forecast on both sides.
        today_row = next((day for day in this_month if day.day == now), None)
        report = analyze_trends(
            days,
            policy=self.policy,
            analyses=analyses,
            today=now,
            working_days_remaining=working_days_left(
                now,
                off_weekdays=_off_weekdays(this_month),
                holidays={holiday.day for holiday in self._holidays.find_all()},
                including_today=today_row is None or today_row.last_out is None,
            ),
        )
        # Today is excluded from the scan. A day with an in-punch and no out-punch is a
        # genuine anomaly on Tuesday of last week and simply Tuesday afternoon today, and
        # every live day would otherwise open the app with a warning about itself.
        settled = [day for day in this_month if day.day < now]
        return TrendsView(
            report=report,
            anomalies=detect_anomalies(settled, analyses=analyses, policy=self.policy),
            months_cached=len({(day.day.year, day.day.month) for day in days}),
            months_available=len(self.synced_months()),
            today=now,
        )

    def leave_days(self, employee_code: str, *, start: date, end: date) -> list[date]:
        """Dates the portal recorded as leave, for the calendar export."""
        return [
            day.day
            for day in self._attendance.find_days_between(employee_code, start, end)
            if day.status is DayStatus.LEAVE
        ]

    def cached_months(self, employee_code: str) -> list[tuple[int, int]]:
        """Months holding at least one day, newest first."""
        return self._attendance.cached_months(employee_code)

    def synced_months(self) -> set[tuple[int, int]]:
        """Months that have been fetched, whether or not they held any days.

        Distinct from :meth:`cached_months`, which only sees months with rows. A month
        before the employee joined is legitimately empty, and keying resume on rows would
        re-fetch it on every single history sync.
        """
        found: set[tuple[int, int]] = set()
        for scope in self._sync_meta.all_scopes():
            _, _, period = scope.partition("attendance:")
            if period and len(period) == 7 and period[4] == "-":
                found.add((int(period[:4]), int(period[5:])))
        return found

    # --- history --------------------------------------------------------------------

    def fetchable_months(self, *, today: date | None = None) -> list[tuple[int, int]]:
        """Months the portal will actually serve, newest first, never into the future.

        The year dropdown is the real ceiling — it currently offers only the running
        portal year — so history stops at that January however many months are configured.
        """
        now = today or date.today()
        offered = self._gateway.available_periods()
        return [period for period in offered if period <= (now.year, now.month)]

    def history_plan(
        self,
        employee_code: str,
        *,
        months: int | None = None,
        today: date | None = None,
        include_cached: bool = False,
    ) -> list[tuple[int, int]]:
        """Which months a backfill would fetch, newest first.

        Already-synced months are skipped by default so a second run is cheap and a
        cancelled one resumes where it stopped.
        """
        limit = months if months is not None else self._config.sync.history_months
        candidates = self.fetchable_months(today=today)[:limit]
        if include_cached:
            return candidates

        already = self.synced_months()
        return [period for period in candidates if period not in already]

    def backfill_history(
        self,
        employee_code: str,
        *,
        months: int | None = None,
        today: date | None = None,
        force: bool = False,
        on_progress: Callable[[int, int, tuple[int, int]], bool] | None = None,
    ) -> HistoryReport:
        """Fetch and cache past months, newest first.

        ``on_progress`` receives ``(done, total, period)`` and returns False to stop, which
        is how the UI offers a cancel. One month failing does not abandon the rest: a gap
        in the middle of the year should not cost the user everything after it.
        """
        plan = self.history_plan(employee_code, months=months, today=today, include_cached=force)
        report = HistoryReport(planned=len(plan))

        for index, (year, month) in enumerate(plan, start=1):
            if on_progress is not None and not on_progress(index, len(plan), (year, month)):
                report.cancelled = True
                logger.info("History backfill cancelled after {} month(s)", report.fetched)
                break
            try:
                self.refresh_month(employee_code, year, month)
                report.fetched += 1
            except CerePulseError as exc:
                logger.warning("Could not fetch {:04d}-{:02d}: {}", year, month, exc)
                report.failures.append(f"{year:04d}-{month:02d}: {exc}")

        logger.info("History backfill: {} of {} month(s) fetched", report.fetched, report.planned)
        return report

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
        # Fetched once. Reading it per day put one query per day of punch detail behind a
        # dict comprehension, which is a lot of round trips for a value that never changes.
        requests = self._swipes.find_all(code)
        analyses = {
            day.day: analyze_day(
                list(day.punches),
                day=day.day,
                policy=self.policy,
                swipe_requests=requests,
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
        swipes = swipe_index(requests)
        return MonthView(
            month=month,
            analysis=analysis,
            last_synced=self._sync_meta.last_synced(attendance_scope(year, period_month)),
            from_cache=from_cache,
            pending_detail=len(self._attendance.days_missing_detail(code, year, period_month)),
            swipes=swipes,
            attention=find_attention(
                list(month.days),
                policy=self.policy,
                analyses=analyses,
                swipes=swipes,
                today=today or date.today(),
            ),
        )


def _months_before(anchor: date, count: int) -> date:
    """The first of the month ``count`` months before ``anchor``."""
    index = anchor.year * 12 + (anchor.month - 1) - count
    return date(index // 12, index % 12 + 1, 1)


def _off_weekdays(days: list[AttendanceDay]) -> set[int]:
    """Which weekdays this employee is rostered off, read from the portal's own markings.

    Falls back to Saturday and Sunday only when the month is too empty to tell — better a
    conventional guess than counting a weekend as a working day still to come.
    """
    off = {day.day.weekday() for day in days if day.status is DayStatus.WEEKLY_OFF}
    worked = {day.day.weekday() for day in days if day.status.counts_as_worked}
    inferred = off - worked
    return inferred or {5, 6}
