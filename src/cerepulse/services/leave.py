"""Leave and holiday workflows, cache-first like attendance."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from loguru import logger

from cerepulse.core.config import AppConfig
from cerepulse.core.errors import ENDS_THE_RUN, CerePulseError, ParserError, SessionExpiredError
from cerepulse.intelligence.attention import StatusChange, status_changes
from cerepulse.intelligence.comp_off import CompOffLedger, comp_off_ledger, is_comp_off_spend
from cerepulse.intelligence.insights import Insight
from cerepulse.intelligence.leave import LeaveOutlook, LeavePolicy, analyze_leave, leave_insights
from cerepulse.intelligence.optimizer import BreakPlan, suggest_breaks
from cerepulse.intelligence.sandwich import SandwichAssessment, SandwichRule, assess
from cerepulse.models.application import Application, ApplicationKind, RequestStatus
from cerepulse.models.leave import Holiday, LeaveBalance, LeaveCategory, LeaveTransaction
from cerepulse.models.swipe import SwipeRequest
from cerepulse.repository.attendance import AttendanceRepository
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
    #: Comp-off credit by credit, when anything has synced to rebuild it from.
    comp_off: CompOffLedger | None = None


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
        attendance: AttendanceRepository | None = None,
    ) -> None:
        self._gateway = gateway
        self._leave = leave
        #: Kept for the composition root's signature; comp-off is read from applications now.
        self._attendance = attendance
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
        ledger = self.comp_off_ledger(employee_code, today=now)
        outlooks = analyze_leave(balances, today=now, policy=self._policy, comp_off=ledger)
        breaks = self.suggest_breaks(outlooks, today=now)
        return LeaveView(
            balances=balances,
            outlooks=outlooks,
            insights=leave_insights(outlooks),
            last_synced=self._sync_meta.last_synced(LEAVE_SCOPE),
            from_cache=from_cache,
            breaks=breaks,
            sandwiches=[self.assess_sandwich(plan, today=now) for plan in breaks],
            comp_off=ledger,
        )

    def comp_off_ledger(self, employee_code: str, *, today: date) -> CompOffLedger | None:
        """Comp-off credit by credit, from everything the cache holds about it.

        ``None`` when the cache holds nothing to rebuild it from — no comp-off application
        and no comp-off row in the register — so the screens can tell "none yet" from "none".
        """
        applications = self.cached_applications(employee_code)
        register = self._leave.find_transactions(employee_code)
        links = {
            app_id: found
            for app_id, (found, _at) in self._leave.find_comp_off_links(employee_code).items()
        }
        ledger = comp_off_ledger(
            today=today,
            applications=applications,
            links=links,
            register=register,
            snapshot=self._leave.find_comp_off_snapshot(employee_code),
            validity_days=self._policy.comp_off_validity_days,
            financial_year_end=self._policy.financial_year_end,
        )
        if not ledger.credits and not ledger.unmatched:
            return None
        return ledger

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
        if not transactions:
            # The register is replaced per fetch, so an empty one would wipe it. A real
            # register always has rows — one summary per leave type at the least — so an
            # empty one is a page that was not the register, said as such.
            raise ParserError("The leave register came back with no rows")
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
        try:
            self.refresh_comp_off(employee_code, fetched)
        except ENDS_THE_RUN:
            raise
        except CerePulseError as exc:
            # The applications are saved and good; which credit each leave used is a detail
            # that falls back to inference until the next sync reads it.
            logger.warning("Comp-off links not refreshed: {}", exc)

    def refresh_comp_off(
        self,
        employee_code: str,
        applications: list[Application] | None = None,
        *,
        today: date | None = None,
        force: bool = False,
    ) -> None:
        """Read which credit each comp-off leave used, and the portal's own balance.

        Bounded on purpose, because each application costs two requests to open. An approved
        leave's credit never changes, so it is opened once, ever; one still awaiting approval
        can be edited, so it is opened again at most once a day. The portal's balance is asked
        for once a day, and again whenever a comp-off application has been decided since —
        that is when it moves.
        """
        if self._applications is None:
            return
        now = today or date.today()
        filed = (
            applications if applications is not None else self.cached_applications(employee_code)
        )
        spends = [
            app
            for app in filed
            if is_comp_off_spend(app)
            and app.status in (RequestStatus.APPROVED, RequestStatus.IN_PROCESS)
        ]
        stored = self._leave.find_comp_off_links(employee_code)
        wanted = [
            app.app_id
            for app in spends
            if app.app_id not in stored or (app.is_open and stored[app.app_id][1].date() < now)
        ]
        earns = any(app.kind is ApplicationKind.COMP_OFF for app in filed)
        signature = _comp_off_signature(filed)
        previous = self._leave.find_comp_off_snapshot(employee_code)
        due = (earns or bool(spends)) and (
            force or previous is None or previous.as_of != now or previous.signature != signature
        )
        if not wanted and not due:
            return

        logger.info("Reading comp-off: {} application(s) to open", len(wanted))
        fetched = self._gateway.fetch_comp_off(wanted, today=now, snapshot=due)
        if fetched.links:
            self._leave.save_comp_off_links(employee_code, fetched.links)
        if fetched.snapshot is not None:
            self._leave.save_comp_off_snapshot(
                employee_code, replace(fetched.snapshot, signature=signature)
            )
            ledger = self.comp_off_ledger(employee_code, today=now)
            if ledger is not None and ledger.portal_agrees is False:
                logger.warning(
                    "Comp-off: the portal counts {:g} usable, the ledger {:g}",
                    fetched.snapshot.balance,
                    ledger.usable,
                )

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


def _comp_off_signature(applications: list[Application]) -> str:
    """Every comp-off application's id and state, so a decision can be noticed."""
    return "|".join(
        sorted(
            f"{app.app_id}:{app.status.value}"
            for app in applications
            if app.kind is ApplicationKind.COMP_OFF or is_comp_off_spend(app)
        )
    )
