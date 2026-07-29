"""The application shell.

Holds the sidebar, the stack of screens, and the one rule that governs all of them: every
service call goes through the task runner, never inline.

Load order is deliberate. Cached data is rendered first and a refresh is kicked off behind
it, so the window is useful immediately rather than after a round trip to the portal.
"""

from __future__ import annotations

from datetime import date, datetime

from loguru import logger
from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from cerepulse import __about__ as about
from cerepulse.app import AppContext
from cerepulse.core.errors import (
    AuthenticationError,
    CerePulseError,
    SessionExpiredError,
    TransportError,
)
from cerepulse.intelligence.attention import AttentionKind
from cerepulse.intelligence.day import DayAnalysis
from cerepulse.intelligence.insights import ActionKind, Insight, InsightKind, Severity
from cerepulse.intelligence.month import analyze_week, week_start_for
from cerepulse.notify.startup import set_registered
from cerepulse.notify.tray import Tray
from cerepulse.services.attendance import MonthView
from cerepulse.services.leave import LeaveView as LeaveData
from cerepulse.transport import pages
from cerepulse.ui import formatting as fmt
from cerepulse.ui.assets import app_icon
from cerepulse.ui.login_dialog import LoginDialog
from cerepulse.ui.theme import Palette, palette_for, stylesheet
from cerepulse.ui.views.about import AboutView
from cerepulse.ui.views.attendance import AttendanceView
from cerepulse.ui.views.insights import InsightsView
from cerepulse.ui.views.leave import LeaveViewWidget
from cerepulse.ui.views.requests import RequestsView, open_url
from cerepulse.ui.views.settings import SettingsView
from cerepulse.ui.views.today import TodayView
from cerepulse.ui.views.week import WeekView
from cerepulse.ui.whats_new import UpdateAvailableDialog, WhatsNewDialog
from cerepulse.ui.workers import TaskRunner
from cerepulse.update import Release, check_for_update, mark_seen, should_show_whats_new

SCREENS = ("Today", "Week", "Attendance", "Insights", "Leave", "Requests", "Settings", "About")


class MainWindow(QMainWindow):
    """The single window. Views render; this coordinates."""

    signed_in = Signal(str)

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._palette: Palette = palette_for(context.config.ui.theme)
        self._runner = TaskRunner(self)
        self._employee_code = context.employee_code
        self._period = (date.today().year, date.today().month)
        self._week_start = week_start_for(date.today())
        self._month_view: MonthView | None = None
        #: Populated in the background; empty until the portal has been asked once.
        self._fetchable_months: list[tuple[int, int]] = []

        self.setWindowTitle(about.NAME)
        self.setWindowIcon(app_icon())
        self.resize(1120, 760)
        self.setMinimumSize(900, 600)

        self._build_ui()
        self._tray = self._build_tray()
        self._runner.busy_changed.connect(self._on_busy_changed)

        # Background refresh, on the interval from settings.
        self._auto = QTimer(self)
        self._auto.setInterval(context.config.sync.refresh_interval_minutes * 60_000)
        self._auto.timeout.connect(lambda: self.refresh(force=True, quiet=True))
        self._auto.start()

    # --- construction ---------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_sidebar())

        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)

        self.today = TodayView(self._palette)
        self.week = WeekView(self._palette)
        self.attendance = AttendanceView(self._palette)
        self.insights = InsightsView(self._palette)
        self.leave = LeaveViewWidget(self._palette)
        self.requests = RequestsView(self._palette)
        self.settings = SettingsView(self._context.config)
        self.about = AboutView()

        for view in (
            self.today,
            self.week,
            self.attendance,
            self.insights,
            self.leave,
            self.requests,
            self.settings,
            self.about,
        ):
            self._stack.addWidget(view)

        self.today.refresh_requested.connect(lambda: self.refresh(force=True))
        self.today.insight_action.connect(self._handle_insight_action)
        self.today.back_to_today.connect(self._show_today)
        self.attendance.refresh_requested.connect(lambda: self.refresh(force=True))
        self.attendance.month_changed.connect(self._change_month)
        self.attendance.day_selected.connect(self._open_day)
        self.leave.refresh_requested.connect(self._refresh_leave)
        self.requests.refresh_requested.connect(self._refresh_leave)
        self.requests.open_portal.connect(
            lambda: open_url(self._context.client.url_for(pages.SWIPE_REQUESTS))
        )
        self.week.week_changed.connect(self._change_week)
        self.insights.refresh_requested.connect(self._refresh_trends)
        self.insights.sync_history_requested.connect(self.sync_history)
        self.about.update_check_requested.connect(self._check_for_update_now)
        self.settings.config_saved.connect(self._save_config)
        self.settings.sign_out_requested.connect(self._sign_out)
        self.settings.clear_cache_requested.connect(self._clear_cache)
        self.settings.sync_history_requested.connect(self.sync_history)
        self.settings.cancel_history_requested.connect(self.cancel_history)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(196)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(8, 16, 8, 12)
        layout.setSpacing(2)

        brand = QLabel(about.NAME)
        brand.setObjectName("SidebarBrand")
        tagline = QLabel(about.TAGLINE)
        tagline.setObjectName("SidebarTagline")
        tagline.setWordWrap(True)
        layout.addWidget(brand)
        layout.addWidget(tagline)

        self._nav = QButtonGroup(self)
        self._nav.setExclusive(True)
        for index, name in enumerate(SCREENS):
            button = QPushButton(name)
            button.setObjectName("SidebarButton")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.clicked.connect(lambda _=False, i=index: self._stack.setCurrentIndex(i))
            self._nav.addButton(button, index)
            layout.addWidget(button)

        layout.addStretch(1)

        self._status = QLabel("Starting…")
        self._status.setObjectName("SidebarTagline")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        return sidebar

    # --- lifecycle ------------------------------------------------------------------

    def start(self) -> None:
        """Render whatever is cached, then sync behind it."""
        self._load_from_cache()
        if self._context.auth.is_authenticated:
            self.refresh(force=False, quiet=True)
        else:
            self._sign_in_silently()

        # Deferred so neither blocks the first paint.
        QTimer.singleShot(600, self._load_fetchable_months)
        QTimer.singleShot(400, self._show_whats_new_if_updated)
        if self._context.config.updates.check_on_startup:
            QTimer.singleShot(4000, self._check_for_update)

    def _show_whats_new_if_updated(self) -> None:
        """Show release notes once, the first time a new version runs."""
        if not should_show_whats_new():
            return
        WhatsNewDialog(version=about.VERSION, parent=self).exec()
        mark_seen()

    def _check_for_update(self) -> None:
        """Look for a newer release. Failures stay silent — this is never urgent."""
        self._runner.submit(
            "update-check",
            check_for_update,
            on_success=self._on_update_found,
            on_error=lambda exc: logger.debug("Update check failed: {}", exc),
        )

    def _check_for_update_now(self) -> None:
        """Explicit check from About. Unlike the startup one, this reports "up to date"."""
        self._runner.submit(
            "update-check",
            check_for_update,
            on_success=self._on_manual_check,
            on_error=self._on_error,
        )

    def _on_manual_check(self, release: Release | None) -> None:
        if release is None:
            QMessageBox.information(
                self,
                "Up to date",
                f"{about.NAME} {about.VERSION} is the latest version.",
            )
            return
        UpdateAvailableDialog(release, parent=self).exec()

    def _on_update_found(self, release: Release | None) -> None:
        if release is None:
            return
        if self._tray is not None and not self.isVisible():
            # Minimised to the tray: a toast is less intrusive than stealing focus.
            self._tray.notify(
                Insight(
                    InsightKind.ON_TRACK,
                    Severity.INFO,
                    f"{about.NAME} {release.version} is available",
                    "Open CerePulse to see what changed.",
                )
            )
            return
        UpdateAvailableDialog(release, parent=self).exec()

    def _sign_in_silently(self) -> None:
        """Try the saved password first; only prompt if that is unavailable or rejected."""
        self._status.setText("Signing in…")
        self._runner.submit(
            "sign-in",
            self._context.sign_in_with_saved_credentials,
            on_success=self._on_signed_in,
            on_error=lambda _exc: self.prompt_sign_in(),
        )

    def prompt_sign_in(self) -> None:
        dialog = LoginDialog(
            username=self._context.config.portal.username,
            remember=self._context.config.portal.remember_me,
            parent=self,
        )

        def attempt(username: str, password: str, remember: bool) -> None:
            def accepted(employee_code: str) -> None:
                dialog.accept()
                self._on_signed_in(employee_code)

            self._runner.submit(
                "sign-in",
                lambda: self._context.sign_in(username, password, remember=remember),
                on_success=accepted,
                on_error=lambda exc: dialog.show_error(_message_for(exc)),
            )

        dialog.submitted.connect(attempt)
        if dialog.exec() == LoginDialog.DialogCode.Rejected and not self._month_view:
            self._status.setText("Not signed in — showing cached data only.")

    def _on_signed_in(self, employee_code: str) -> None:
        self._employee_code = employee_code
        self.signed_in.emit(employee_code)
        self.refresh(force=True, quiet=True)

    def _build_tray(self) -> Tray | None:
        """Create the tray icon, unless the desktop has no tray or the user opted out."""
        if self._context.config.ui.background_mode != "tray":
            return None
        if not Tray.is_available():
            logger.info("No system tray available; running foreground-only")
            return None

        tray = Tray(app_icon(), self._context.config.notifications, self)
        tray.open_requested.connect(self._restore_window)
        tray.refresh_requested.connect(lambda: self.refresh(force=True))
        tray.quit_requested.connect(self._quit)
        tray.show()
        return tray

    def _restore_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit(self) -> None:
        """Leave for good, rather than back to the tray."""
        from PySide6.QtWidgets import QApplication

        self._quitting = True
        if self._tray is not None:
            self._tray.hide()
        QApplication.quit()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 — Qt override
        """Close to the tray when running in tray mode; otherwise actually exit."""
        if self._tray is not None and not getattr(self, "_quitting", False):
            event.ignore()
            self.hide()
            return

        self._auto.stop()
        self._runner.wait(3000)
        if self._tray is not None:
            self._tray.hide()
        super().closeEvent(event)

    # --- data ------------------------------------------------------------------------

    def _load_from_cache(self) -> None:
        """Render cached data without touching the network. Runs off-thread for SQLite."""
        year, month = self._period
        self._runner.submit(
            "load-cache",
            lambda: self._context.attendance.load_month(
                self._employee_code, year, month, force_refresh=False, today=date.today()
            ),
            on_success=self._apply_month,
            on_error=lambda exc: logger.debug("No cached month yet: {}", exc),
        )

    def refresh(self, *, force: bool = False, quiet: bool = False) -> None:
        """Sync everything, then re-render."""
        year, month = self._period
        if not quiet:
            self._status.setText("Refreshing…")

        self._runner.submit(
            "sync",
            lambda: self._context.sync.sync_all(
                self._employee_code, year=year, month=month, today=date.today()
            ),
            on_success=lambda report: self._after_sync(report, force=force),
            on_error=self._on_error,
        )

    def _after_sync(self, report: object, *, force: bool) -> None:
        failures = getattr(report, "failures", [])
        if failures:
            self.today.banner.show_message(
                f"Some data could not be refreshed: {failures[0]}", Severity.WARNING
            )
        else:
            self.today.banner.clear_message()

        year, month = self._period
        self._runner.submit(
            "reload",
            lambda: self._context.attendance.load_month(
                self._employee_code, year, month, force_refresh=False, today=date.today()
            ),
            on_success=self._apply_month,
            on_error=self._on_error,
        )
        self._refresh_leave()

    def _load_fetchable_months(self) -> None:
        """Ask the portal which months it will serve, so the picker can offer them."""

        def adopt(periods: list[tuple[int, int]]) -> None:
            self._fetchable_months = periods
            if self._month_view is not None:
                self._apply_month(self._month_view)

        self._runner.submit(
            "periods",
            lambda: self._context.attendance.fetchable_months(),
            on_success=adopt,
            on_error=lambda exc: logger.debug("Could not list periods: {}", exc),
        )

    def sync_history(self) -> None:
        """Fetch past months in the background, reporting progress in the sidebar."""
        employee = self._employee_code

        def run() -> object:
            return self._context.attendance.backfill_history(
                employee,
                on_progress=lambda done, total, period: self._history_progress(done, total, period),
            )

        def done(report: object) -> None:
            summary = getattr(report, "summary", "History sync finished.")
            self.settings.banner.show_message(summary, Severity.SUCCESS)
            self._history_cancelled = False
            self._load_fetchable_months()
            if self._month_view is not None:
                self._apply_month(self._month_view)

        self._history_cancelled = False
        self.settings.banner.show_message("Fetching history…", Severity.INFO)
        self._runner.submit("history", run, on_success=done, on_error=self._on_error)

    def cancel_history(self) -> None:
        self._history_cancelled = True

    def _history_progress(self, done: int, total: int, period: tuple[int, int]) -> bool:
        # Runs on the worker thread, so it only reads a flag and writes no widgets.
        logger.info("History {}/{}: {:04d}-{:02d}", done, total, *period)
        return not getattr(self, "_history_cancelled", False)

    def _refresh_trends(self) -> None:
        """Rebuild the Insights screen from the cache. Never fetches.

        History is expensive to pull and cheap to read, so this reads what is stored and
        the screen says how much that is. Filling it is an explicit Sync history.
        """
        self._runner.submit(
            "trends",
            lambda: self._context.attendance.load_trends(self._employee_code, today=date.today()),
            on_success=self.insights.show_trends,
            on_error=lambda exc: self.insights.show_error(str(exc)),
        )

    def _refresh_leave(self) -> None:
        self._runner.submit(
            "leave",
            lambda: self._context.leave.load_leave(self._employee_code, today=date.today()),
            on_success=self._apply_leave,
            on_error=self._on_error,
        )
        self._runner.submit(
            "swipes",
            lambda: self._context.leave.load_swipe_requests(self._employee_code),
            on_success=self._apply_swipes,
            on_error=lambda exc: logger.debug("Swipe requests unavailable: {}", exc),
        )

    # --- rendering ------------------------------------------------------------------

    def _apply_month(self, view: MonthView) -> None:
        self._month_view = view
        year, month = self._period

        # Offer every month the portal can serve, not only what is cached, so history is
        # reachable from the picker. The fetchable list needs the portal, so fall back to
        # the cache when offline.
        # Synced, not merely non-empty: a month before the employee joined is genuinely
        # empty and should not read as "not synced" forever.
        cached = self._context.attendance.synced_months() | set(
            self._context.attendance.cached_months(self._employee_code)
        )
        months = sorted(cached | {(year, month)}, reverse=True)
        if self._fetchable_months:
            months = sorted(set(self._fetchable_months) | cached | {(year, month)}, reverse=True)
        self.attendance.set_available_months(months, (year, month), cached=cached)
        self.attendance.show_month(view)

        week = analyze_week(
            list(view.month.days),
            week_start=self._week_start,
            policy=self._context.attendance.policy,
        )
        self.week.show_week(week, self._context.attendance.policy.work_target)

        self._render_today(view)
        self._render_status(view)
        # Trends read straight from the cache the month refresh has just written, so they
        # follow it rather than running on their own schedule.
        self._refresh_trends()

    def _render_today(self, view: MonthView) -> None:
        today = date.today()
        # Only ever render today as today. Falling back to the last cached day made a
        # failed sync look like a successful one showing the wrong figures, which is how a
        # missing clock-in went unnoticed.
        if view.month.day_on(today) is None:
            self.today.show_awaiting_today(today, last_synced=view.last_synced)
            return

        def rendered(analysis: DayAnalysis) -> None:
            self.today.show_analysis(analysis, is_today=analysis.day == today)
            if self._tray is not None and analysis.day == today:
                self._tray.set_analysis(analysis)
                self._tray.notify_insights(list(analysis.insights))

        self._runner.submit(
            "analyze-day",
            lambda: self._context.attendance.load_day(
                self._employee_code, today, now=datetime.now()
            ),
            on_success=rendered,
            on_error=lambda exc: logger.debug("Could not analyze today: {}", exc),
        )

    def _render_status(self, view: MonthView) -> None:
        synced = fmt.relative_time(view.last_synced)
        pending = f" · {view.pending_detail} day(s) pending" if view.pending_detail else ""
        self._status.setText(f"Synced {synced}{pending}")

        if view.from_cache and view.last_synced is None:
            self.today.banner.show_message(
                "Showing cached data — not synced yet.", Severity.WARNING
            )

    def _apply_leave(self, data: LeaveData) -> None:
        self.leave.show_leave(data)
        if self._tray is not None:
            # Expiry warnings are worth a toast; the policy caps them to once a day.
            self._tray.notify_insights(data.insights)
        self._runner.submit(
            "ledger",
            lambda: self._context.leave._leave.find_transactions(self._employee_code),
            on_success=self.leave.show_ledger,
            on_error=lambda exc: logger.debug("Ledger unavailable: {}", exc),
        )

    def _apply_swipes(self, requests: list[object]) -> None:
        # Taken from the month view rather than recomputed. This screen used to carry its
        # own third definition of "needs a request", which disagreed with both the others.
        needing: list[date] = []
        if self._month_view is not None:
            needing = [
                day
                for day, attention in sorted(self._month_view.attention.items())
                if attention.kind is AttentionKind.SHORT_NO_REQUEST
            ]
        self.requests.show_requests(requests, needing=needing)  # type: ignore[arg-type]

    # --- actions --------------------------------------------------------------------

    def _change_month(self, year: int, month: int) -> None:
        self._period = (year, month)
        self._load_from_cache()
        self.refresh(force=False, quiet=True)

    def _change_week(self, week_start: date) -> None:
        self._week_start = week_start
        if self._month_view is not None:
            week = analyze_week(
                list(self._month_view.month.days),
                week_start=week_start,
                policy=self._context.attendance.policy,
            )
            self.week.show_week(week, self._context.attendance.policy.work_target)

    def _show_today(self) -> None:
        """Return to today, switching the period back if the user wandered months."""
        today = date.today()
        if self._period != (today.year, today.month):
            self._change_month(today.year, today.month)
            return
        if self._month_view is not None:
            self._render_today(self._month_view)

    def _open_day(self, day: date) -> None:
        self._stack.setCurrentIndex(0)
        button = self._nav.button(0)
        if button is not None:
            button.setChecked(True)
        self._runner.submit(
            "analyze-day",
            lambda: self._context.attendance.load_day(self._employee_code, day, now=datetime.now()),
            on_success=lambda analysis: self.today.show_analysis(
                analysis, is_today=day == date.today()
            ),
            on_error=self._on_error,
        )

    def _handle_insight_action(self, insight: Insight) -> None:
        if insight.action is None:
            return
        if insight.action.kind is ActionKind.OPEN_SWIPE_REQUEST:
            open_url(self._context.client.url_for(pages.SWIPE_REQUESTS))
        elif insight.action.kind is ActionKind.OPEN_ATTENDANCE:
            open_url(self._context.client.url_for(pages.ATTENDANCE_REPORT))

    def _save_config(self, config: object) -> None:
        from cerepulse.core.config import save_config

        try:
            save_config(config)  # type: ignore[arg-type]
        except CerePulseError as exc:
            self.settings.banner.show_message(str(exc), Severity.CRITICAL)
            return

        # The services keep their own reference to the config, so pushing it through the
        # graph is what makes a changed shift policy or tone take effect now rather than
        # at the next launch.
        self._context.apply_config(config)  # type: ignore[arg-type]
        self._auto.setInterval(config.sync.refresh_interval_minutes * 60_000)  # type: ignore[attr-defined]
        self._apply_theme(config.ui.theme)  # type: ignore[attr-defined]
        if self._tray is not None:
            self._tray.update_config(config.notifications)  # type: ignore[attr-defined]
        if self._month_view is not None:
            self._render_today(self._month_view)

        note = ""
        if not set_registered(config.ui.start_with_windows):  # type: ignore[attr-defined]
            if config.ui.start_with_windows:  # type: ignore[attr-defined]
                # Almost always because this is a source run, not an installed build.
                note = " Start-with-Windows needs an installed build."

        self.settings.banner.show_message(
            f"Saved. Switching tray mode or theme fully applies on the next start.{note}",
            Severity.SUCCESS,
        )

    def _apply_theme(self, theme: str) -> None:
        from PySide6.QtWidgets import QApplication

        self._palette = palette_for(theme)
        application = QApplication.instance()
        if application is not None:
            application.setStyleSheet(stylesheet(self._palette))  # type: ignore[attr-defined]

    def _sign_out(self, forget: bool) -> None:
        self._runner.submit(
            "sign-out",
            lambda: self._context.sign_out(forget=forget),
            on_success=lambda _: self.prompt_sign_in(),
            on_error=self._on_error,
        )

    def _clear_cache(self) -> None:
        confirmed = QMessageBox.question(
            self,
            "Clear cached data?",
            "Local attendance and leave data will be removed and re-synced on the next "
            "refresh. Nothing changes in SpineHR.",
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        self._runner.submit(
            "clear-cache",
            self._context.database.clear_cache,
            on_success=lambda _: self.refresh(force=True),
            on_error=self._on_error,
        )

    # --- feedback -------------------------------------------------------------------

    def _on_busy_changed(self, busy: bool) -> None:
        if busy:
            self._status.setText("Syncing…")
        elif self._month_view is not None:
            self._render_status(self._month_view)

    def _on_error(self, exc: BaseException) -> None:
        if isinstance(exc, SessionExpiredError | AuthenticationError):
            # Recovery already tried and failed; only now is a prompt warranted.
            self.prompt_sign_in()
            return
        self.today.banner.show_message(
            _message_for(exc),
            Severity.WARNING if isinstance(exc, TransportError) else Severity.CRITICAL,
        )


def _message_for(exc: BaseException) -> str:
    """User-facing text. Internals stay in the logs (Chapter 14 section 10)."""
    if isinstance(exc, CerePulseError):
        return exc.user_message if type(exc).user_message else str(exc)
    return "Something went wrong. Check the logs for details."
