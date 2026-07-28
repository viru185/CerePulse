"""Sync orchestration and session-expiry recovery.

Chapter 05 section 9 defines the recovery path: stop dependent work, clear the invalid
session, re-authenticate, reload context, then resume the pending operation. The
implementation detail that matters is **replay exactly once**. A retry loop around an
expiry looks harmless but turns a rejected credential into an authentication storm, so a
second expiry during the replay is surfaced rather than retried.

Re-authentication also invalidates the cached navigation menu: its privilege tokens belong
to the old session, so the gateway is told to forget them before the replay.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TypeVar

from loguru import logger

from cerepulse.auth.manager import AuthManager
from cerepulse.core.errors import CerePulseError, SessionExpiredError, TransportError
from cerepulse.services.attendance import AttendanceService
from cerepulse.services.leave import LeaveService
from cerepulse.services.portal import PortalGateway

T = TypeVar("T")


@dataclass(slots=True)
class SyncReport:
    """What one full sync accomplished, and what it could not."""

    started_at: datetime
    finished_at: datetime | None = None
    month_refreshed: bool = False
    detail_days_fetched: int = 0
    leave_refreshed: bool = False
    swipes_refreshed: bool = False
    holidays_refreshed: bool = False
    reauthenticated: bool = False
    failures: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return not self.failures

    @property
    def duration_seconds(self) -> float:
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()


class SyncCoordinator:
    """Runs sync work, recovering once from an expired session."""

    def __init__(
        self,
        *,
        auth: AuthManager,
        gateway: PortalGateway,
        attendance: AttendanceService,
        leave: LeaveService,
    ) -> None:
        self._auth = auth
        self._gateway = gateway
        self._attendance = attendance
        self._leave = leave

    # --- recovery -------------------------------------------------------------------

    def run(self, operation: Callable[[], T]) -> T:
        """Run an operation, re-authenticating and replaying it once if the session died."""
        try:
            return operation()
        except SessionExpiredError:
            logger.warning("Session expired; re-authenticating before replaying")
            self._auth.reauthenticate()
            # The menu's privilege tokens belonged to the dead session.
            self._gateway.forget_menu()

            try:
                result = operation()
            except SessionExpiredError as exc:
                # Expiring again immediately means re-auth is not actually working.
                # Retrying would become an authentication storm.
                raise SessionExpiredError(
                    "Session expired again immediately after re-authenticating"
                ) from exc
            logger.info("Replay after re-authentication succeeded")
            return result

    # --- full sync ------------------------------------------------------------------

    def sync_all(
        self,
        employee_code: str,
        *,
        year: int | None = None,
        month: int | None = None,
        today: date | None = None,
        backfill: bool = True,
    ) -> SyncReport:
        """Refresh everything for one month, continuing past individual failures.

        Each step is independent: a leave outage should not cost the user their attendance
        refresh, so failures are collected rather than raised.
        """
        now = today or date.today()
        period_year = year or now.year
        period_month = month or now.month
        report = SyncReport(started_at=datetime.now())
        state_before = self._auth.state

        report.holidays_refreshed = self._step(
            report, "holidays", lambda: self._leave.load_holidays(force_refresh=False)
        )
        report.month_refreshed = self._step(
            report,
            "attendance",
            lambda: self._attendance.refresh_month(employee_code, period_year, period_month),
        )
        report.swipes_refreshed = self._step(
            report,
            "swipe requests",
            lambda: self._leave.refresh_swipe_requests(employee_code),
        )
        report.leave_refreshed = self._step(
            report, "leave", lambda: self._leave.refresh_leave(employee_code)
        )

        if backfill and report.month_refreshed:
            fetched = self._step_value(
                report,
                "day detail",
                lambda: self._attendance.backfill_detail(employee_code, period_year, period_month),
            )
            report.detail_days_fetched = fetched or 0

        report.reauthenticated = self._auth.state is not state_before
        report.finished_at = datetime.now()
        logger.info(
            "Sync finished in {:.1f}s ({} failures)",
            report.duration_seconds,
            len(report.failures),
        )
        return report

    def _step(self, report: SyncReport, name: str, operation: Callable[[], object]) -> bool:
        """Run one sync step with recovery, recording rather than raising on failure.

        Success is judged by whether a failure was recorded, not by the return value —
        most steps legitimately return None.
        """
        before = len(report.failures)
        self._step_value(report, name, operation)
        return len(report.failures) == before

    def _step_value(self, report: SyncReport, name: str, operation: Callable[[], T]) -> T | None:
        """As :meth:`_step`, but returns the operation's result (None if it failed)."""
        try:
            return self.run(operation)
        except (TransportError, SessionExpiredError) as exc:
            logger.warning("Sync step {!r} failed: {}", name, exc)
            report.failures.append(f"{name}: {exc}")
        except CerePulseError as exc:
            logger.error("Sync step {!r} failed: {}", name, exc)
            report.failures.append(f"{name}: {exc}")
        return None
