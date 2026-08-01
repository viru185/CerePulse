"""Every read that leaves the GUI thread.

One rule draws the line between this and the window: **anything that submits work to the
task runner lives here; anything that touches a widget lives there.** Results cross as
signals. That keeps the whole "when does data arrive, and what happens if it doesn't"
problem in one file, and leaves the views to do nothing but render what they are handed.

The runner's pool is deliberately single-slot — the portal is a stateful WebForms session
where concurrent postbacks invalidate each other — so everything here queues. That is
correctness, not politeness, and it is why the loading feedback matters: work submitted
while a history backfill is running will genuinely wait.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from PySide6.QtCore import QObject, Signal

from cerepulse.app import AppContext
from cerepulse.services.scopes import Scope
from cerepulse.ui.workers import TaskRunner

#: Callbacks the window hands in for the few flows it has to finish itself — a dialog to
#: close, a prompt to raise. Everything else comes back as a signal.
Success = Callable[[Any], None]
Failure = Callable[[BaseException], None]


class SyncController(QObject):
    """Fetches and analyses; renders nothing."""

    #: A month, freshly loaded or reloaded after a sync.
    month_ready = Signal(object)  # MonthView
    #: One analysed day, and whether it is today.
    day_ready = Signal(object, bool)  # DayAnalysis, is_today
    leave_ready = Signal(object)  # LeaveView
    ledger_ready = Signal(object)  # list[LeaveTransaction]
    swipes_ready = Signal(object)  # list[SwipeRequest]
    #: Requests decided since the previous fetch. Empty on every sync that changed nothing.
    swipes_decided = Signal(object)  # list[StatusChange]
    trends_ready = Signal(object)  # TrendsView
    periods_ready = Signal(object)  # list[tuple[int, int]]

    #: Today's row is not in the portal's data yet — distinct from a failure.
    today_missing = Signal(object, object)  # date, datetime | None

    #: Per-scope freshness for the sync panel.
    scopes_ready = Signal(object)  # list[ScopeStatus]
    scope_started = Signal(object)  # Scope
    scope_finished = Signal(object, int)  # Scope, count where meaningful

    #: A sync finished but some scopes failed. Empty list means everything succeeded.
    sync_finished = Signal(object)  # list[str]
    history_finished = Signal(str)
    #: Something the user needs to know about, rather than a debug line.
    failed = Signal(object)  # BaseException
    #: Non-fatal: the screen still works, but a part of it is missing.
    degraded = Signal(str, object)  # scope, BaseException

    def __init__(
        self,
        *,
        context: AppContext,
        runner: TaskRunner,
        employee_code: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._runner = runner
        self.employee_code = employee_code
        self.period = (date.today().year, date.today().month)
        self._history_cancelled = False
        self._detail_cancelled = False

    # --- months -----------------------------------------------------------------------

    def load_from_cache(self) -> None:
        """Render whatever is stored, so the window is useful before any round trip."""
        year, month = self.period
        self._runner.submit(
            "load-cache",
            lambda: self._context.attendance.load_month(
                self.employee_code, year, month, force_refresh=False, today=date.today()
            ),
            on_success=self.month_ready.emit,
            on_error=lambda exc: logger.debug("No cached month yet: {}", exc),
        )

    def refresh(self, *, force: bool = False) -> None:
        """Sync every scope, then reload the month behind it.

        ``force`` reaches the services now. It used to stop at this method, so pressing
        Refresh respected every cache TTL and could legitimately fetch nothing at all.
        """
        year, month = self.period
        self._runner.submit(
            "sync",
            lambda: self._context.sync.sync_all(
                self.employee_code, year=year, month=month, today=date.today(), force=force
            ),
            on_success=self._after_sync,
            on_error=self.failed.emit,
        )

    def _after_sync(self, report: object) -> None:
        self.sync_finished.emit(list(getattr(report, "failures", [])))
        self.reload_month()
        self.refresh_leave()
        self.report_scopes()

    # --- one scope at a time ------------------------------------------------------------

    def report_scopes(self) -> None:
        """Publish per-scope freshness for the sync panel."""
        year, month = self.period
        self._runner.submit(
            "scopes",
            lambda: self._context.attendance.scope_statuses(
                self.employee_code, year=year, month=month
            ),
            on_success=self.scopes_ready.emit,
            on_error=lambda exc: logger.debug("Could not read sync state: {}", exc),
        )

    def sync_scope(self, scope: Scope) -> None:
        """Refresh exactly one thing. Ignores its TTL — asking implies it looks wrong."""
        year, month = self.period
        self.scope_started.emit(scope)

        def run() -> int:
            return self._context.sync.sync_scope(
                scope,
                self.employee_code,
                year=year,
                month=month,
                on_progress=self._detail_progress,
            )

        def done(count: int) -> None:
            self.scope_finished.emit(scope, count)
            self.reload_month()
            if scope in (Scope.LEAVE, Scope.SWIPE_REQUESTS):
                self.refresh_leave()
            self.report_scopes()

        self._detail_cancelled = False
        self._runner.submit(f"scope-{scope.value}", run, on_success=done, on_error=self.failed.emit)

    def sync_day(self, day: date) -> None:
        """Re-fetch one day's punches, for when a past date never filled in."""
        self.scope_started.emit(Scope.DAY_DETAIL)

        def done(_result: object) -> None:
            self.scope_finished.emit(Scope.DAY_DETAIL, 1)
            self.load_day(day)
            self.report_scopes()

        self._runner.submit(
            "sync-day",
            lambda: self._context.sync.sync_day(self.employee_code, day),
            on_success=done,
            on_error=self.failed.emit,
        )

    def cancel_detail(self) -> None:
        self._detail_cancelled = True

    def _detail_progress(self, done: int, total: int) -> bool:
        # Worker thread: reads a flag, writes no widgets.
        logger.info("Punch detail {}/{}", done, total)
        return not self._detail_cancelled

    def reload_month(self) -> None:
        year, month = self.period
        self._runner.submit(
            "reload",
            lambda: self._context.attendance.load_month(
                self.employee_code, year, month, force_refresh=False, today=date.today()
            ),
            on_success=self.month_ready.emit,
            on_error=self.failed.emit,
        )

    def load_fetchable_months(self) -> None:
        """Ask the portal which months it will serve, so the picker can offer them."""
        self._runner.submit(
            "periods",
            lambda: self._context.attendance.fetchable_months(),
            on_success=self.periods_ready.emit,
            on_error=lambda exc: logger.debug("Could not list periods: {}", exc),
        )

    # --- days -------------------------------------------------------------------------

    def load_today(self, view: object) -> None:
        """Analyse today, or say plainly that the portal has no row for it yet.

        Never falls back to the most recent cached day: that made a failed sync look like a
        successful one showing the wrong figures, which is how a missing clock-in went
        unnoticed for a fortnight.
        """
        today = date.today()
        month = getattr(view, "month", None)
        if month is None or month.day_on(today) is None:
            self.today_missing.emit(today, getattr(view, "last_synced", None))
            return
        self.load_day(today)

    def load_day(self, day: date) -> None:
        today = date.today()
        self._runner.submit(
            "analyze-day",
            lambda: self._context.attendance.load_day(self.employee_code, day, now=datetime.now()),
            on_success=lambda analysis: self.day_ready.emit(analysis, day == today),
            on_error=lambda exc: self.degraded.emit("day", exc),
        )

    # --- leave ------------------------------------------------------------------------

    def refresh_leave(self) -> None:
        self._runner.submit(
            "leave",
            lambda: self._context.leave.load_leave(self.employee_code, today=date.today()),
            on_success=self._on_leave,
            on_error=self.failed.emit,
        )
        self._runner.submit(
            "swipes",
            lambda: self._context.leave.load_swipe_requests_with_changes(self.employee_code),
            on_success=self._on_swipes,
            on_error=lambda exc: self.degraded.emit("swipe requests", exc),
        )

    def _on_swipes(self, result: tuple[list[Any], list[Any]]) -> None:
        requests, changes = result
        self.swipes_ready.emit(requests)
        if changes:
            self.swipes_decided.emit(changes)

    def _on_leave(self, data: object) -> None:
        self.leave_ready.emit(data)
        self._runner.submit(
            "ledger",
            lambda: self._context.leave.leave_ledger(self.employee_code),
            on_success=self.ledger_ready.emit,
            on_error=lambda exc: self.degraded.emit("leave ledger", exc),
        )

    # --- trends -----------------------------------------------------------------------

    def refresh_trends(self) -> None:
        """Rebuild Insights from the cache. Never fetches.

        History is expensive to pull and cheap to read, so this reads what is stored and the
        screen states how much that is. Filling it is an explicit Sync history.
        """
        self._runner.submit(
            "trends",
            lambda: self._context.attendance.load_trends(self.employee_code, today=date.today()),
            on_success=self.trends_ready.emit,
            on_error=lambda exc: self.degraded.emit("insights", exc),
        )

    # --- history ----------------------------------------------------------------------

    def sync_history(self) -> None:
        """Fetch past months in the background, one paced request at a time."""
        employee = self.employee_code
        self._history_cancelled = False

        def run() -> object:
            return self._context.attendance.backfill_history(
                employee, on_progress=self._history_progress
            )

        def done(report: object) -> None:
            self._history_cancelled = False
            self.load_fetchable_months()
            self.reload_month()
            self.history_finished.emit(str(getattr(report, "summary", "History sync finished.")))

        self._runner.submit("history", run, on_success=done, on_error=self.failed.emit)

    def cancel_history(self) -> None:
        self._history_cancelled = True

    def _history_progress(self, done: int, total: int, period: tuple[int, int]) -> bool:
        # Runs on the worker thread, so it only reads a flag and writes no widgets.
        logger.info("History {}/{}: {:04d}-{:02d}", done, total, *period)
        return not self._history_cancelled

    # --- account and cache ------------------------------------------------------------

    def sign_in_silently(self, on_success: Success, on_failure: Callable[[], None]) -> None:
        """Try the saved password first; only prompt when that is missing or rejected."""
        self._runner.submit(
            "sign-in",
            self._context.sign_in_with_saved_credentials,
            on_success=on_success,
            on_error=lambda _exc: on_failure(),
        )

    def sign_in(
        self,
        username: str,
        password: str,
        *,
        remember: bool,
        on_success: Success,
        on_error: Failure,
    ) -> None:
        self._runner.submit(
            "sign-in",
            lambda: self._context.sign_in(username, password, remember=remember),
            on_success=on_success,
            on_error=on_error,
        )

    def sign_out(self, *, forget: bool, on_success: Success) -> None:
        self._runner.submit(
            "sign-out",
            lambda: self._context.sign_out(forget=forget),
            on_success=on_success,
            on_error=self.failed.emit,
        )

    def clear_cache(self, on_success: Success) -> None:
        self._runner.submit(
            "clear-cache",
            self._context.database.clear_cache,
            on_success=on_success,
            on_error=self.failed.emit,
        )

    def export_calendar(self, target: Path, on_success: Success, on_error: Failure) -> None:
        """Gather and write the .ics off-thread. The save location is chosen by the caller."""
        from cerepulse.export.ics import build_calendar

        employee = self.employee_code

        def run() -> int:
            today = date.today()
            taken = self._context.attendance.leave_days(
                employee, start=date(today.year, 1, 1), end=date(today.year, 12, 31)
            )
            events = self._context.leave.calendar_events(employee, leave_taken=taken, today=today)
            target.write_text(build_calendar(events), encoding="utf-8", newline="")
            return len(events)

        self._runner.submit("export-ics", run, on_success=on_success, on_error=on_error)


__all__ = ["SyncController"]
