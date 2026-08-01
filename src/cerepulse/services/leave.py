"""Leave and holiday workflows, cache-first like attendance."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from loguru import logger

from cerepulse.core.config import AppConfig
from cerepulse.core.errors import TransportError
from cerepulse.export.ics import CalendarEvent
from cerepulse.intelligence.insights import Insight
from cerepulse.intelligence.leave import LeaveOutlook, LeavePolicy, analyze_leave, leave_insights
from cerepulse.intelligence.optimizer import BreakPlan, suggest_breaks
from cerepulse.models.leave import Holiday, LeaveBalance, LeaveCategory, LeaveTransaction
from cerepulse.models.swipe import SwipeRequest
from cerepulse.repository.leave import (
    HolidayRepository,
    LeaveRepository,
    SwipeRequestRepository,
    SyncMetadataRepository,
)
from cerepulse.services.portal import PortalGateway

LEAVE_SCOPE = "leave"
SWIPE_SCOPE = "swipe_requests"
HOLIDAY_SCOPE = "holidays"

#: Holidays are published once a year, so re-fetching them hourly is waste.
HOLIDAY_TTL_MINUTES = 24 * 60

#: How far ahead the optimizer looks. Six months covers the rest of any leave year without
#: suggesting a bridge over holidays the company has not published yet.
PLANNING_HORIZON_DAYS = 183

#: Leave that can actually be spent on a planned break. Medical leave is not a planning
#: instrument, and suggesting someone bridge a long weekend with it would be a poor joke.
SPENDABLE = frozenset({LeaveCategory.PLANNED, LeaveCategory.CARRY_FORWARD, LeaveCategory.CASUAL})


@dataclass(frozen=True, slots=True)
class LeaveView:
    """Balances with their expiry assessment, plus the insights worth surfacing."""

    balances: list[LeaveBalance]
    outlooks: list[LeaveOutlook]
    insights: list[Insight]
    last_synced: datetime | None
    from_cache: bool
    #: The cheapest breaks the current balance can buy, in date order.
    breaks: list[BreakPlan] = field(default_factory=list)


class LeaveService:
    """Leave balances, swipe requests, and the holiday calendar."""

    def __init__(
        self,
        *,
        gateway: PortalGateway,
        leave: LeaveRepository,
        swipes: SwipeRequestRepository,
        holidays: HolidayRepository,
        sync_meta: SyncMetadataRepository,
        config: AppConfig,
        policy: LeavePolicy | None = None,
    ) -> None:
        self._gateway = gateway
        self._leave = leave
        self._swipes = swipes
        self._holidays = holidays
        self._sync_meta = sync_meta
        self._config = config
        self._policy = policy or LeavePolicy()

    def use_config(self, config: AppConfig) -> None:
        """Adopt a newly saved configuration, so Settings applies without a restart."""
        self._config = config

    # --- leave ----------------------------------------------------------------------

    def load_leave(
        self,
        employee_code: str,
        *,
        today: date | None = None,
        force_refresh: bool = False,
    ) -> LeaveView:
        stale = self._sync_meta.is_stale(
            LEAVE_SCOPE, max_age_minutes=self._config.sync.cache_ttl_minutes
        )
        balances = self._leave.find_balances(employee_code)

        from_cache = True
        if force_refresh or stale or not balances:
            try:
                self.refresh_leave(employee_code)
                balances = self._leave.find_balances(employee_code)
                from_cache = False
            except TransportError as exc:
                if not balances:
                    raise
                logger.warning("Leave refresh failed, serving cached balances: {}", exc)

        now = today or date.today()
        outlooks = analyze_leave(balances, today=now, policy=self._policy)
        return LeaveView(
            balances=balances,
            outlooks=outlooks,
            insights=leave_insights(outlooks),
            last_synced=self._sync_meta.last_synced(LEAVE_SCOPE),
            from_cache=from_cache,
            breaks=self.suggest_breaks(outlooks, today=now),
        )

    def suggest_breaks(
        self,
        outlooks: list[LeaveOutlook],
        *,
        today: date | None = None,
        horizon_days: int = PLANNING_HORIZON_DAYS,
    ) -> list[BreakPlan]:
        """The best breaks bookable from the balance the user actually holds.

        Only whole days of *planned* leave are spendable on a holiday: medical leave is not
        a planning instrument, and a half day cannot bridge anything.
        """
        now = today or date.today()
        budget = int(
            sum(
                outlook.balance.available_balance
                for outlook in outlooks
                if outlook.balance.category in SPENDABLE and outlook.balance.available_balance > 0
            )
        )
        return suggest_breaks(
            start=now,
            end=now + timedelta(days=horizon_days),
            holidays={holiday.day for holiday in self._holidays.find_all() if holiday.day >= now},
            max_leave=budget,
        )

    def calendar_events(
        self,
        employee_code: str,
        *,
        leave_taken: Iterable[date] = (),
        today: date | None = None,
    ) -> list[CalendarEvent]:
        """Everything worth having in a personal calendar, as all-day events.

        Holidays and leave already taken are facts the portal holds. Expiry deadlines are
        included because they are the one thing it never tells anyone, and a date in a
        calendar is the only form of that warning that survives closing this app.

        ``leave_taken`` is passed in rather than read here: those dates come from the
        attendance grid, which is not this service's to reach into.
        """
        now = today or date.today()
        events = [
            CalendarEvent(
                day=holiday.day,
                summary=holiday.name or "Company holiday",
                category="HOLIDAY",
            )
            for holiday in self._holidays.find_all()
        ]
        events += [CalendarEvent(day=day, summary="Leave", category="LEAVE") for day in leave_taken]

        outlooks = analyze_leave(
            self._leave.find_balances(employee_code), today=now, policy=self._policy
        )
        events += [
            CalendarEvent(
                day=outlook.expires_on,
                summary=f"{outlook.balance.leave_type} expires",
                description=(
                    f"{outlook.balance.available_balance:g} day(s) of "
                    f"{outlook.balance.leave_type} lapse on this date."
                ),
                category="DEADLINE",
            )
            for outlook in outlooks
            if outlook.expires_on is not None and outlook.has_balance
        ]
        return events

    def leave_ledger(self, employee_code: str) -> list[LeaveTransaction]:
        """Every stored ledger movement, newest first.

        Exists so the UI has a public way to read it. The Leave screen previously reached
        through this service into its repository, which meant the one screen that shows the
        ledger was also the one place that knew where it was stored.
        """
        return self._leave.find_transactions(employee_code)

    def refresh_leave(self, employee_code: str) -> None:
        logger.info("Refreshing leave balances")
        balances, transactions = self._gateway.fetch_leave()
        self._leave.save_balances(employee_code, balances)
        self._leave.save_transactions(employee_code, transactions)
        self._sync_meta.mark_synced(LEAVE_SCOPE)

    # --- swipe requests -------------------------------------------------------------

    def load_swipe_requests(
        self, employee_code: str, *, force_refresh: bool = False
    ) -> list[SwipeRequest]:
        stale = self._sync_meta.is_stale(
            SWIPE_SCOPE, max_age_minutes=self._config.sync.cache_ttl_minutes
        )
        if force_refresh or stale:
            try:
                self.refresh_swipe_requests(employee_code)
            except TransportError as exc:
                logger.warning("Swipe-request refresh failed, serving cache: {}", exc)
        return self._swipes.find_all(employee_code)

    def refresh_swipe_requests(self, employee_code: str) -> None:
        logger.info("Refreshing swipe requests")
        self._swipes.save_all(employee_code, self._gateway.fetch_swipe_requests())
        self._sync_meta.mark_synced(SWIPE_SCOPE)

    # --- holidays -------------------------------------------------------------------

    def load_holidays(self, *, force_refresh: bool = False) -> list[Holiday]:
        stale = self._sync_meta.is_stale(HOLIDAY_SCOPE, max_age_minutes=HOLIDAY_TTL_MINUTES)
        holidays = self._holidays.find_all()

        if force_refresh or stale or not holidays:
            try:
                self.refresh_holidays()
                holidays = self._holidays.find_all()
            except TransportError as exc:
                logger.warning("Holiday refresh failed, serving cache: {}", exc)
        return holidays

    def refresh_holidays(self) -> None:
        logger.info("Refreshing holiday calendar")
        self._holidays.save_all(self._gateway.fetch_holidays())
        self._sync_meta.mark_synced(HOLIDAY_SCOPE)
