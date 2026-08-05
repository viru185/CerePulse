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
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from loguru import logger

from cerepulse.core.config import AppConfig
from cerepulse.core.errors import CerePulseError, TransportError
from cerepulse.intelligence.anomalies import Anomaly, detect_anomalies
from cerepulse.intelligence.attention import Attention, find_attention, swipe_index
from cerepulse.intelligence.day import DayAnalysis, analyze_day
from cerepulse.intelligence.insights import Insight
from cerepulse.intelligence.month import MonthAnalysis, analyze_month, week_start_for
from cerepulse.intelligence.nudges import LEAVE_LOOKBACK_DAYS, day_nudges, leave_nudges
from cerepulse.intelligence.policy import ShiftPolicy
from cerepulse.intelligence.trends import (
    MIN_SAMPLE,
    TrendReport,
    analyze_trends,
    build_facts,
    working_days_left,
)
from cerepulse.intelligence.voice import Tone, voice_day
from cerepulse.intelligence.week_vs_usual import WeekComparison, compare_week
from cerepulse.models.attendance import (
    AttendanceDay,
    AttendanceMonth,
    DayStatus,
    Punch,
    PunchDirection,
)
from cerepulse.models.leave import Holiday
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
from cerepulse.services.scopes import Scope, ScopeStatus

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
    #: The daily work target every figure here was measured against, so a chart can scale
    #: itself against the same number the text beside it used.
    work_target: Duration = Duration(0)
    #: This week against the baseline — the part of the screen that changes every day.
    week: WeekComparison | None = None

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
    #: The holiday calendar this month was measured against. Carried so the Week screen can
    #: project the days still ahead without a database read on the GUI thread.
    holidays: list[Holiday] = field(default_factory=list)
    #: Punch-level analysis per day, for the days that have one. Computed here anyway to
    #: build the month rollup; carrying it means a screen can show one day's detail without
    #: a second trip through the repository.
    analyses: dict[date, DayAnalysis] = field(default_factory=dict)

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
        """Analyze one day, fetching its punch log when the cache cannot be trusted.

        "Already stored" was the wrong test for today. A day in progress has a punch log that
        is *correct as far as it goes* and out of date the moment anyone swipes again, so
        treating a stored log as settled left Today showing the morning until the user forced
        a fetch by hand. Anything already fetched for a day that has finished is settled and
        is served from the cache, which is almost every read this method ever does.
        """
        cached = self._attendance.find_day(employee_code, day)
        stale = day == (now.date() if now else date.today())
        if cached is None or not cached.detail_loaded or stale:
            self.refresh_day_detail(employee_code, day)
            cached = self._attendance.find_day(employee_code, day)

        punches = list(cached.punches) if cached else []
        grid_only = False
        if not punches and cached is not None:
            punches = _punches_from_grid(cached)
            grid_only = bool(punches)

        analysis = analyze_day(
            punches,
            day=day,
            policy=self.policy,
            now=now,
            swipe_requests=self._swipes.find_all(employee_code),
            grid_only=grid_only,
        )
        # Nudges are appended rather than produced by `analyze_day`, because they are about
        # a habit rather than about the day's arithmetic and that separation is worth
        # keeping visible. They are ordinary insights from here on, so the notification
        # policy's quiet hours and once-a-day both apply to them unchanged.
        nudges = [
            *day_nudges(analysis, now=now),
            *self._leave_nudges(employee_code, today=day),
        ]
        nudged = replace(analysis, insights=(*analysis.insights, *nudges))
        # Voiced here rather than in the views, so the window, the tray tooltip and the
        # notifications all say the same thing about the same day.
        return voice_day(nudged, tone=Tone.parse(self._config.ui.tone))

    def _leave_nudges(self, employee_code: str, *, today: date) -> list[Insight]:
        """How long since a day off, read from the muster.

        Here rather than in :class:`LeaveService` because the answer is in the attendance
        history, not in the leave ledger — the ledger this portal writes records credits and
        leaves ``consumed_days`` at zero on every row, so a nudge built on it would have
        stayed silent forever and looked like a threshold set too high.
        """
        window = self._attendance.find_days_between(
            employee_code, today - timedelta(days=LEAVE_LOOKBACK_DAYS), today
        )
        return leave_nudges(window, today=today)

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
            work_target=self.policy.work_target,
            week=compare_week(
                build_facts(days, policy=self.policy, analyses=analyses),
                report.habits,
                week_start=week_start_for(now),
                today=now,
            ),
        )

    def scope_statuses(self, employee_code: str, *, year: int, month: int) -> list[ScopeStatus]:
        """Freshness for every syncable thing, for the sync panel.

        Read from the same metadata rows the TTL checks use, so what the panel shows and
        what the app believes can never disagree.
        """
        return [
            ScopeStatus(
                Scope.ATTENDANCE,
                self._sync_meta.last_synced(attendance_scope(year, month)),
            ),
            ScopeStatus(
                Scope.DAY_DETAIL,
                self._sync_meta.last_synced(attendance_scope(year, month)),
                pending=len(self._attendance.days_missing_detail(employee_code, year, month)),
            ),
            ScopeStatus(Scope.LEAVE, self._sync_meta.last_synced(Scope.LEAVE.value)),
            ScopeStatus(
                Scope.SWIPE_REQUESTS, self._sync_meta.last_synced(Scope.SWIPE_REQUESTS.value)
            ),
            ScopeStatus(Scope.APPLICATIONS, self._sync_meta.last_synced(Scope.APPLICATIONS.value)),
            ScopeStatus(Scope.HOLIDAYS, self._sync_meta.last_synced(Scope.HOLIDAYS.value)),
        ]

    def days_between(self, employee_code: str, *, start: date, end: date) -> list[AttendanceDay]:
        """Every cached day in a range, oldest first. Offline by design.

        Exists for the Records timeline, whose period can span more months than the one on
        the Attendance picker. Reads whatever the cache holds rather than fetching — a
        timeline that triggered a sync per period change would spend the portal's patience
        answering a question the cache already could.
        """
        return self._attendance.find_days_between(employee_code, start, end)

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
        """Fetch a month's grid and persist it. Punch detail is left to the backfill.

        A month whose grid has not changed is not written again. Past months are identical
        on every refresh for the rest of the year, so the default behaviour was to rewrite
        thirty-one rows a time to arrive at exactly what was already there.

        The digest is recorded only after a successful write. Recording it first would mean
        a failed save left the app believing the month was stored, and it would never try
        again.
        """
        logger.info("Refreshing attendance for {:04d}-{:02d}", year, month)
        fetched, _parsed = self._gateway.fetch_month(year, month)

        # The grid carries the employee code; trust it over the login username.
        code = fetched.employee_code or employee_code
        stored = AttendanceMonth(employee_code=code, year=year, month=month, days=fetched.days)

        scope = attendance_scope(year, month)
        digest = stored.content_digest()
        if self._sync_meta.content_hash(scope) == digest:
            logger.debug("{:04d}-{:02d} is unchanged; skipping the write", year, month)
            self._sync_meta.mark_synced(scope)
            return stored

        self._attendance.save_month(stored)
        self._sync_meta.mark_synced(scope, content_hash=digest)
        return stored

    def prune_history(self, employee_code: str, *, today: date | None = None) -> int:
        """Drop cached days older than the configured history window.

        Bounded by ``sync.history_months``, the same setting that decides how far a backfill
        reaches — so the cache holds what the app is willing to fetch and nothing beyond it.
        Nothing here is irreplaceable; every pruned day can be fetched again.
        """
        months = max(1, self._config.sync.history_months)
        now = today or date.today()
        # Whole months back from the first of the current one, so pruning never cuts a
        # month in half and leaves a rollup measuring a fortnight against a month's target.
        year, month = now.year, now.month - months
        while month < 1:
            year, month = year - 1, month + 12
        return self._attendance.prune_before(employee_code, date(year, month, 1))

    def refresh_day_detail(self, employee_code: str, day: date) -> None:
        """Fetch and store one day's punch log.

        The grid is fetched to find the day's row, and the page it came from goes straight
        into the postback rather than being thrown away and fetched again — which halves the
        requests and, more to the point, removes one whole full-ViewState period postback,
        the single heaviest thing in this path.
        """
        _month, parsed, page = self._gateway.fetch_month_page(day.year, day.month)
        target = next((item for item in parsed if item.day.day == day), None)
        if target is None:
            logger.warning("No attendance row for {} to fetch detail for", day)
            return

        punches = self._gateway.fetch_day_detail(target, page=page)
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

        Today is always in the backlog while it is still today, and sorting newest-first puts
        it at the head of the batch — so the day anyone is actually looking at is the one that
        gets refreshed first, even when a month of history is still filling in behind it.
        """
        pending = self._attendance.days_missing_detail(employee_code, year, month)
        if not pending:
            return 0

        batch = sorted(pending, reverse=True)[:batch_size]
        # One grid for the whole batch, and the same page reused for every postback in it.
        # Each day used to re-fetch and re-select the month for itself, so a twenty-day drain
        # made about sixty requests to do twenty-two requests' worth of work.
        _month, parsed, page = self._gateway.fetch_month_page(year, month)
        by_date = {item.day.day: item for item in parsed}

        fetched = 0
        for day in batch:
            target = by_date.get(day)
            if target is None:
                continue
            try:
                punches = self._gateway.fetch_day_detail(target, page=page)
            except CerePulseError as exc:
                # One bad day must not abandon the batch; it stays in the backlog.
                logger.warning("Could not fetch detail for {}: {}", day, exc)
                continue
            self._attendance.save_day_detail(employee_code, day, punches)
            fetched += 1

        logger.info("Backfilled detail for {} of {} pending days", fetched, len(pending))
        return fetched

    def drain_detail(
        self,
        employee_code: str,
        year: int,
        month: int,
        *,
        on_progress: Callable[[int, int], bool] | None = None,
    ) -> int:
        """Fetch every day still missing punch detail, in paced batches.

        :meth:`backfill_detail` is bounded so a routine refresh cannot stall on twenty
        postbacks. That leaves a fresh month needing four or five refreshes to fill, with
        nothing in the UI to say so and no way to ask for the rest — this is that way.

        ``on_progress`` receives (done, total) and returns False to stop, so the user can
        abandon a long drain without waiting it out.
        """
        total = len(self._attendance.days_missing_detail(employee_code, year, month))
        if not total:
            return 0

        fetched = 0
        while True:
            batch = self.backfill_detail(employee_code, year, month)
            if not batch:
                break
            fetched += batch
            if on_progress is not None and not on_progress(fetched, total):
                logger.info("Detail drain cancelled after {} of {} days", fetched, total)
                break
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
        # Today gets the clock; every other day is genuinely finished. Without it, today was
        # analysed as a *completed* day here — its open punch pair read as a missing punch
        # and its worked time as zero — while the Today screen analysed the same punches
        # with `now` and inferred the in-progress pair. Same day, two answers, and this one
        # fed the Week timelines, the day drawer and `find_attention`.
        current = today or date.today()
        analyses = {
            day.day: analyze_day(
                list(day.punches),
                day=day.day,
                policy=self.policy,
                swipe_requests=requests,
                now=datetime.now() if day.day == current else None,
            )
            for day in month.days
            if day.detail_loaded and day.punches
        }

        holidays = self._holidays.find_all()
        analysis = analyze_month(
            list(month.days),
            year=year,
            month=period_month,
            policy=self.policy,
            analyses=analyses,
            holidays=holidays,
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
            holidays=holidays,
            analyses=analyses,
        )


def _punches_from_grid(day: AttendanceDay) -> list[Punch]:
    """Rebuild a minimal punch log from the monthly grid's own first-in and last-out.

    The portal's day-detail panel comes back empty for the day currently in progress, and an
    empty result is legitimately recorded as "fetched, nothing there". The grid row, though,
    already carries first-in and last-out — so without this, Today reports "Nothing logged"
    for a day it plainly has hours for. That is exactly how a clock-in appears to go missing.

    Only the in and out are real. Any break taken inside that span is invisible here, which
    is why the caller marks the analysis ``grid_only`` and the screen says so.
    """
    if day.first_in is None:
        return []
    punches = [Punch(at=day.first_in, direction=PunchDirection.IN)]
    if day.last_out is not None and day.last_out != day.first_in:
        punches.append(Punch(at=day.last_out, direction=PunchDirection.OUT))
    return punches


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
