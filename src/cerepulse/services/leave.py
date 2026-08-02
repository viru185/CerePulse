"""Leave and holiday workflows, cache-first like attendance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from loguru import logger

from cerepulse.core.config import AppConfig
from cerepulse.core.errors import CerePulseError, SessionExpiredError
from cerepulse.intelligence.attention import StatusChange, status_changes
from cerepulse.intelligence.insights import Insight
from cerepulse.intelligence.leave import LeaveOutlook, LeavePolicy, analyze_leave, leave_insights
from cerepulse.intelligence.optimizer import BreakPlan, suggest_breaks
from cerepulse.intelligence.sandwich import SandwichAssessment, SandwichRule, assess
from cerepulse.models.application import Application
from cerepulse.models.leave import Holiday, LeaveBalance, LeaveCategory, LeaveTransaction
from cerepulse.models.swipe import SwipeRequest
from cerepulse.repository.leave import (
    ApplicationRepository,
    HolidayRepository,
    LeaveRepository,
    SwipeRequestRepository,
    SyncMetadataRepository,
)
from cerepulse.services.portal import PortalGateway


def _parse_rule(value: str) -> SandwichRule:
    """A typo resolves to OFF, so a mistyped setting cannot invent a policy."""
    try:
        return SandwichRule(value.strip().lower())
    except ValueError:
        logger.warning("Unknown sandwich rule {!r}; treating it as off", value)
        return SandwichRule.OFF


LEAVE_SCOPE = "leave"
SWIPE_SCOPE = "swipe_requests"
APPLICATION_SCOPE = "applications"
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
    #: What each of those breaks costs once the configured sandwich rule is applied,
    #: aligned with ``breaks``. Every entry reports no sandwiches while the rule is off,
    #: which is what keeps an unconfirmed policy off the screen entirely.
    sandwiches: list[SandwichAssessment] = field(default_factory=list)


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
        applications: ApplicationRepository | None = None,
    ) -> None:
        self._gateway = gateway
        self._leave = leave
        self._swipes = swipes
        self._applications = applications
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
            except SessionExpiredError:
                raise
            except CerePulseError as exc:
                if not balances:
                    raise
                logger.warning("Leave refresh failed, serving cached balances: {}", exc)

        now = today or date.today()
        outlooks = analyze_leave(balances, today=now, policy=self._policy)
        breaks = self.suggest_breaks(outlooks, today=now)
        return LeaveView(
            balances=balances,
            outlooks=outlooks,
            insights=leave_insights(outlooks),
            last_synced=self._sync_meta.last_synced(LEAVE_SCOPE),
            from_cache=from_cache,
            breaks=breaks,
            sandwiches=[self.assess_sandwich(plan, today=now) for plan in breaks],
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

    def sandwich_rule(self) -> SandwichRule:
        """The configured sandwich rule. ``OFF`` unless the user has asserted otherwise."""
        return _parse_rule(self._config.leave_rules.sandwich_rule)

    def assess_sandwich(self, plan: BreakPlan, *, today: date | None = None) -> SandwichAssessment:
        """What a break plan really costs once the configured sandwich rule is applied.

        Off by default, and off means silent: the assessment reports the booked days and no
        sandwiches at all, so the UI has nothing to render rather than a zero to explain.
        Nothing in SpineHR states whether the employer applies this, and a warning that
        might not apply would have people leaving leave unbooked over a rule that does not
        exist.
        """
        now = today or date.today()
        return assess(
            set(plan.leave_days),
            rule=self.sandwich_rule(),
            holidays={holiday.day for holiday in self._holidays.find_all() if holiday.day >= now},
        )

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
        return self.load_swipe_requests_with_changes(employee_code, force_refresh=force_refresh)[0]

    def cached_swipe_requests(self, employee_code: str) -> list[SwipeRequest]:
        """What is stored, with no fetch and no TTL check. For painting before the network."""
        return self._swipes.find_all(employee_code)

    # --- leave, outdoor-duty and comp-off applications --------------------------------

    def load_applications(
        self, employee_code: str, *, force_refresh: bool = False
    ) -> list[Application]:
        """Filed applications, refreshed when stale and served from cache when not."""
        if self._applications is None:
            return []
        stale = self._sync_meta.is_stale(
            APPLICATION_SCOPE, max_age_minutes=self._config.sync.cache_ttl_minutes
        )
        if force_refresh or stale:
            try:
                self.refresh_applications(employee_code)
            except SessionExpiredError:
                raise
            except CerePulseError as exc:
                logger.warning("Application refresh failed, serving cache: {}", exc)
        return self._applications.find_all(employee_code)

    def cached_applications(self, employee_code: str) -> list[Application]:
        return self._applications.find_all(employee_code) if self._applications else []

    def refresh_applications(self, employee_code: str) -> None:
        if self._applications is None:
            return
        logger.info("Refreshing leave, outdoor-duty and comp-off applications")
        fetched = self._gateway.fetch_applications()
        self._applications.save_all(employee_code, fetched)
        self._sync_meta.mark_synced(APPLICATION_SCOPE)
        logger.info("{} application(s) cached", len(fetched))

    def load_swipe_requests_with_changes(
        self, employee_code: str, *, force_refresh: bool = False
    ) -> tuple[list[SwipeRequest], list[StatusChange]]:
        """The requests, and any that were decided since the previous fetch.

        The changes come back with the data rather than through a callback or a field on
        this service, so nothing here has to remember anything between calls — which also
        means a caller that does not care simply uses :meth:`load_swipe_requests`.
        """
        stale = self._sync_meta.is_stale(
            SWIPE_SCOPE, max_age_minutes=self._config.sync.cache_ttl_minutes
        )
        changes: list[StatusChange] = []
        if force_refresh or stale:
            try:
                changes = self.refresh_swipe_requests(employee_code)
            except SessionExpiredError:
                # Not ours to absorb: the app has to hear about a dead session.
                raise
            except CerePulseError as exc:
                logger.warning("Swipe-request refresh failed, serving cache: {}", exc)
        return self._swipes.find_all(employee_code), changes

    def refresh_swipe_requests(self, employee_code: str) -> list[StatusChange]:
        """Fetch, compare with what was stored, then save. Returns what moved.

        The comparison has to happen before the save, because the save is what destroys the
        only record of the previous statuses. The portal carries no filed date, no request
        id and no approver, so diffing two fetches is the only way to know a request was
        ever decided — without it, an approval is something the user finds by re-checking.
        """
        logger.info("Refreshing swipe requests")
        before = self._swipes.find_all(employee_code)
        fetched = self._gateway.fetch_swipe_requests()
        changes = status_changes(before, fetched)

        self._swipes.save_all(employee_code, fetched)
        self._sync_meta.mark_synced(SWIPE_SCOPE)
        if changes:
            logger.info("{} swipe request(s) decided since the last sync", len(changes))
        return changes

    # --- holidays -------------------------------------------------------------------

    def load_holidays(self, *, force_refresh: bool = False) -> list[Holiday]:
        stale = self._sync_meta.is_stale(HOLIDAY_SCOPE, max_age_minutes=HOLIDAY_TTL_MINUTES)
        holidays = self._holidays.find_all()

        if force_refresh or stale or not holidays:
            try:
                self.refresh_holidays()
                holidays = self._holidays.find_all()
            except SessionExpiredError:
                raise
            except CerePulseError as exc:
                logger.warning("Holiday refresh failed, serving cache: {}", exc)
        return holidays

    def refresh_holidays(self) -> None:
        logger.info("Refreshing holiday calendar")
        self._holidays.save_all(self._gateway.fetch_holidays())
        self._sync_meta.mark_synced(HOLIDAY_SCOPE)
