"""Leave and holiday workflows, cache-first like attendance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from loguru import logger

from cerepulse.core.config import AppConfig
from cerepulse.core.errors import TransportError
from cerepulse.intelligence.insights import Insight
from cerepulse.intelligence.leave import LeaveOutlook, LeavePolicy, analyze_leave, leave_insights
from cerepulse.models.leave import Holiday, LeaveBalance
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


@dataclass(frozen=True, slots=True)
class LeaveView:
    """Balances with their expiry assessment, plus the insights worth surfacing."""

    balances: list[LeaveBalance]
    outlooks: list[LeaveOutlook]
    insights: list[Insight]
    last_synced: datetime | None
    from_cache: bool


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

        outlooks = analyze_leave(balances, today=today or date.today(), policy=self._policy)
        return LeaveView(
            balances=balances,
            outlooks=outlooks,
            insights=leave_insights(outlooks),
            last_synced=self._sync_meta.last_synced(LEAVE_SCOPE),
            from_cache=from_cache,
        )

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
