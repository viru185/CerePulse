"""The application shell.

Holds the sidebar, the stack of screens, and the wiring between the two. Everything that
leaves the GUI thread lives in ``ui/controllers``: this class submits no work and owns no
fetching logic. It assembles, renders what the controllers hand it, and decides what a
click means.

Load order is deliberate. Cached data is rendered first and a refresh is kicked off behind
it, so the window is useful immediately rather than after a round trip to the portal.
"""

from __future__ import annotations

from datetime import date

from loguru import logger
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QKeyEvent
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
    SessionTakenError,
    TransportError,
)
from cerepulse.intelligence.attention import Attention, AttentionKind
from cerepulse.intelligence.day import DayAnalysis
from cerepulse.intelligence.insights import ActionKind, Insight, Severity
from cerepulse.intelligence.month import analyze_week, week_start_for
from cerepulse.notify.startup import set_registered
from cerepulse.notify.tray import Tray
from cerepulse.services.attendance import MonthView
from cerepulse.services.leave import LeaveView as LeaveData
from cerepulse.services.scopes import Scope
from cerepulse.transport import pages
from cerepulse.ui import formatting as fmt
from cerepulse.ui.assets import app_icon
from cerepulse.ui.controllers import NavigationController, SyncController, UpdateController
from cerepulse.ui.login_dialog import LoginDialog
from cerepulse.ui.theme import Palette, palette_for, stylesheet
from cerepulse.ui.views.about import AboutView
from cerepulse.ui.views.attendance import AttendanceView
from cerepulse.ui.views.insights import InsightsView
from cerepulse.ui.views.leave import LeaveViewWidget
from cerepulse.ui.views.requests import RequestsView, open_url
from cerepulse.ui.views.settings import SettingsView
from cerepulse.ui.views.sync_panel import SyncPanel
from cerepulse.ui.views.today import TodayView
from cerepulse.ui.views.week import WeekView
from cerepulse.ui.workers import TaskRunner
from cerepulse.update import Channel

SCREENS = ("Today", "Week", "Attendance", "Insights", "Leave", "Requests", "Settings", "About")
TODAY_SCREEN = SCREENS.index("Today")


class MainWindow(QMainWindow):
    """The single window. Views render; controllers fetch; this connects them."""

    signed_in = Signal(str)

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._palette: Palette = palette_for(context.config.ui.theme)
        self._runner = TaskRunner(self)
        self._employee_code = context.employee_code
        self._week_start = week_start_for(date.today())
        self._month_view: MonthView | None = None
        #: A date the user explicitly asked to look at. Without it, any background refresh
        #: reloads the month and yanks the screen back to today mid-read.
        self._viewing_day: date | None = None
        #: Populated in the background; empty until the portal has been asked once.
        self._fetchable_months: list[tuple[int, int]] = []
        self._sync_panel: SyncPanel | None = None
        #: Set when the portal session was taken elsewhere. While paused the app serves
        #: cache and does not touch the network until the user asks it to.
        self._paused = False
        self._scopes: list[object] = []
        self._busy_scope: Scope | None = None

        self.setWindowTitle(about.NAME)
        self.setWindowIcon(app_icon())
        self.resize(1120, 760)
        self.setMinimumSize(900, 600)

        self._build_ui()
        self._tray = self._build_tray()
        self._build_controllers()
        self._runner.busy_changed.connect(self._on_busy_changed)
        self._runner.activity_changed.connect(self._on_activity)

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
        self.today.action_triggered.connect(self._handle_action)
        self.today.date_selected.connect(self._view_day)
        self.today.back_to_today.connect(self._go_back)
        self.attendance.refresh_requested.connect(lambda: self.refresh(force=True))
        self.attendance.month_changed.connect(self._change_month)
        self.attendance.day_selected.connect(self._open_day)
        self.attendance.fetch_detail_requested.connect(
            lambda: self._sync.sync_scope(Scope.DAY_DETAIL)
        )
        self.attendance.open_portal.connect(lambda: self._open_portal(pages.ATTENDANCE_REPORT))
        self.today.sync_day_requested.connect(self._sync_day)
        self.leave.refresh_requested.connect(lambda: self._sync.refresh_leave())
        self.requests.refresh_requested.connect(lambda: self._sync.refresh_leave())
        self.requests.open_portal.connect(lambda: self._open_portal(pages.SWIPE_REQUESTS))
        self.requests.day_selected.connect(self._open_day)
        self.week.week_changed.connect(self._change_week)
        self.week.day_selected.connect(self._open_day)
        self.insights.refresh_requested.connect(lambda: self._sync.refresh_trends())
        self.insights.sync_history_requested.connect(self.sync_history)
        self.about.update_check_requested.connect(self._check_for_update_now)
        self.about.rollback_requested.connect(self._rollback)
        self.about.test_connection_requested.connect(self._test_connection)
        self.settings.config_saved.connect(self._save_config)
        self.settings.sign_out_requested.connect(self._sign_out)
        self.settings.clear_cache_requested.connect(self._clear_cache)
        self.settings.sync_history_requested.connect(self.sync_history)
        self.settings.cancel_history_requested.connect(self.cancel_history)
        self.settings.test_notification_requested.connect(self._test_notification)

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
            button.clicked.connect(lambda _=False, i=index: self._navigation.select(i))
            self._nav.addButton(button, index)
            layout.addWidget(button)

        layout.addStretch(1)

        # The status is the way in to per-scope sync, so it is a button rather than a
        # label: the one place that says how fresh things are should also be where you go
        # to do something about it.
        self._status = QPushButton("Starting…")
        self._status.setObjectName("SidebarButton")
        self._status.setToolTip("What has been synced, and when")
        self._status.clicked.connect(self._open_sync_panel)
        layout.addWidget(self._status)
        return sidebar

    def _build_controllers(self) -> None:
        self._navigation = NavigationController(self._stack, self._nav, SCREENS, self)
        self._navigation.screen_changed.connect(self._on_screen_changed)

        updates = self._context.config.updates
        self._updates = UpdateController(
            runner=self._runner,
            window=self,
            channel=Channel.parse(updates.channel),
            download_automatically=updates.download_automatically,
            notifier=(self._tray.notify if self._tray is not None else None),
            parent=self,
        )
        self._updates.update_failed.connect(
            lambda message: self.about.banner.show_message(message, Severity.WARNING)
        )
        self._updates.handover.connect(self._quit)

        self._sync = SyncController(
            context=self._context,
            runner=self._runner,
            employee_code=self._employee_code,
            parent=self,
        )
        self._sync.month_ready.connect(self._apply_month)
        self._sync.day_ready.connect(self._apply_day)
        self._sync.today_missing.connect(self.today.show_awaiting_today)
        self._sync.leave_ready.connect(self._apply_leave)
        self._sync.ledger_ready.connect(self.leave.show_ledger)
        self._sync.swipes_ready.connect(self._apply_swipes)
        self._sync.swipes_decided.connect(self._on_swipes_decided)
        self._sync.trends_ready.connect(self.insights.show_trends)
        self._sync.periods_ready.connect(self._adopt_periods)
        self._sync.sync_finished.connect(self._on_sync_finished)
        self._sync.scopes_ready.connect(self._on_scopes_ready)
        self._sync.scope_started.connect(self._on_scope_started)
        self._sync.scope_finished.connect(self._on_scope_finished)
        self._sync.history_finished.connect(
            lambda summary: self.settings.banner.show_message(summary, Severity.SUCCESS)
        )
        self._sync.failed.connect(self._on_error)
        self._sync.degraded.connect(self._on_degraded)

    def _build_tray(self) -> Tray | None:
        """Create the tray icon whenever it could be useful.

        Qt delivers notifications through a tray icon, so tying the tray to
        ``background_mode`` silently made notifications impossible in foreground mode —
        with no log line and no way for the user to tell. The tray now exists whenever
        notifications are on; ``background_mode`` governs only what closing the window does.
        """
        wanted = (
            self._context.config.ui.background_mode == "tray"
            or self._context.config.notifications.enabled
        )
        if not wanted:
            return None
        if not Tray.is_available():
            logger.info("No system tray on this desktop; notifications are unavailable")
            return None

        tray = Tray(app_icon(), self._context.config.notifications, self)
        tray.open_requested.connect(self._restore_window)
        tray.refresh_requested.connect(lambda: self.refresh(force=True))
        tray.quit_requested.connect(self._quit)
        tray.show()
        if tray.delivery_problem() is not None:
            logger.warning("Tray is up but cannot notify: {}", tray.delivery_problem())
        return tray

    @property
    def _closes_to_tray(self) -> bool:
        """Whether the window hides instead of exiting. Distinct from having a tray icon."""
        return self._tray is not None and self._context.config.ui.background_mode == "tray"

    # --- lifecycle ------------------------------------------------------------------

    def start(self) -> None:
        """Render whatever is cached, then sync behind it."""
        self._sync.load_from_cache()
        if self._context.auth.is_authenticated:
            self.refresh(force=False, quiet=True)
        else:
            self._sign_in_silently()

        # Deferred so none of it blocks the first paint.
        QTimer.singleShot(600, self._sync.load_fetchable_months)
        self._updates.schedule_startup_checks(
            check_for_updates=self._context.config.updates.check_on_startup
        )
        self.about.refresh(channel=self._context.config.updates.channel)

    def _check_for_update_now(self) -> None:
        self._updates.check_now(on_error=self._on_error)

    def _rollback(self, version: str) -> None:
        """Reinstall an earlier version whose installer is still staged."""
        confirmed = QMessageBox.question(
            self,
            f"Roll back to {version}?",
            f"CerePulse will close, reinstall {version}, and reopen. Your cached data and "
            f"settings are untouched.",
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        from cerepulse.update import InstallError, rollback_to

        try:
            rollback_to(version)
        except InstallError as exc:
            self.about.banner.show_message(str(exc), Severity.WARNING)
            return
        self._quit()

    def _test_connection(self) -> None:
        """Prove the portal is reachable and the saved credentials still work."""
        self.about.banner.show_message("Testing the connection to SpineHR…", Severity.INFO)
        self._runner.submit(
            "connection-test",
            self._context.gateway.fetch_employee,
            on_success=lambda employee: self.about.banner.show_message(
                f"Connected. Signed in as {getattr(employee, 'code', 'unknown')}.",
                Severity.SUCCESS,
            ),
            on_error=lambda exc: self.about.banner.show_message(
                f"Could not reach SpineHR: {_message_for(exc)}", Severity.CRITICAL
            ),
        )

    def _sign_in_silently(self) -> None:
        self._status.setText("Signing in…")
        self._sync.sign_in_silently(self._on_signed_in, self.prompt_sign_in)

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

            self._sync.sign_in(
                username,
                password,
                remember=remember,
                on_success=accepted,
                on_error=lambda exc: dialog.show_error(_message_for(exc)),
            )

        dialog.submitted.connect(attempt)
        if dialog.exec() == LoginDialog.DialogCode.Rejected and not self._month_view:
            self._status.setText("Not signed in — showing cached data only.")

    def _on_signed_in(self, employee_code: str) -> None:
        self._employee_code = employee_code
        self._sync.employee_code = employee_code
        self.signed_in.emit(employee_code)
        self.refresh(force=True, quiet=True)

    def come_forward(self) -> None:
        """Show and focus, whether hidden in the tray or merely buried.

        Called when a second launch hands over rather than starting its own copy: the user
        asked for CerePulse, so the answer has to be a window, not silence.
        """
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _restore_window(self) -> None:
        self.come_forward()

    def _quit(self) -> None:
        """Leave for good, rather than back to the tray."""
        from PySide6.QtWidgets import QApplication

        self._quitting = True
        if self._tray is not None:
            self._tray.hide()
        QApplication.quit()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 — Qt override
        """Escape retraces a drill-down, the way it does everywhere else.

        Only when there is somewhere to go: swallowing Escape on a top-level screen would
        make it feel broken.
        """
        if event.key() == Qt.Key.Key_Escape and self._navigation.back():
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 — Qt override
        """Close to the tray when running in tray mode; otherwise actually exit."""
        if self._closes_to_tray and not getattr(self, "_quitting", False):
            event.ignore()
            self.hide()
            return

        self._auto.stop()
        self._runner.wait(3000)
        if self._tray is not None:
            self._tray.hide()
        super().closeEvent(event)

    # --- sync entry points ------------------------------------------------------------

    def refresh(self, *, force: bool = False, quiet: bool = False) -> None:
        if self._paused:
            # An explicit Refresh is the user asking for the session back; a background
            # tick is not, and honouring one would take the session off their browser.
            if quiet:
                return
            self._resume()
        if not quiet:
            self._status.setText("Refreshing…")
        self._sync.refresh(force=force)

    def _open_portal(self, page: str) -> None:
        """Open SpineHR in the browser, signed in, and hand the session over.

        Verified against the live portal: a login form scraped here and submitted by a
        client with none of this app's cookies is accepted, so the browser really can be
        signed in without the user typing anything.

        It is a handover, not a share — a cookie cannot be injected into a browser from
        outside it, so this creates a second session and the portal ends ours. Standing
        down first is what stops the two fighting over it.
        """
        self._status.setText("Opening SpineHR…")

        def ready(handover: object) -> None:
            open_url(handover.url)  # type: ignore[attr-defined]
            self._stand_down(
                "SpineHR is open in your browser and CerePulse has handed the session "
                "over. Press Refresh when you are done with it."
            )

        def failed(exc: BaseException) -> None:
            # Falling back to a plain link is still useful: the user signs in themselves,
            # which costs a password but works.
            logger.warning("Could not sign the browser in: {}", exc)
            open_url(self._context.client.url_for(page))
            self._stand_down(
                f"Opened SpineHR, but CerePulse could not sign you in automatically "
                f"({_message_for(exc)}). It has paused either way."
            )

        self._sync.open_in_browser(page, on_ready=ready, on_error=failed)

    def _stand_down(self, message: str = "") -> None:
        """Stop using the portal because something else is signed in as the same user.

        SpineHR allows one session, so the alternative — signing straight back in — takes it
        off whatever else has it. When that is the user's own browser the two trade it back
        and forth all afternoon, each breaking the other, and the app is the one that should
        give way: it has a cache to fall back on and the browser does not.
        """
        self._paused = True
        self._auto.stop()
        logger.info("Paused: the portal session is in use elsewhere")

        self._status.setText("Paused — signed in elsewhere")
        self.today.banner.show_message(
            message
            or (
                "SpineHR is signed in somewhere else, so CerePulse has paused rather than "
                "taking the session back. Showing cached data. Press Refresh when you are "
                "done in the browser."
            ),
            Severity.WARNING,
            key="session",
        )

    def _resume(self) -> None:
        """Take the session back, because the user just asked for it."""
        self._paused = False
        self.today.banner.clear_message("session")
        self._auto.start()
        logger.info("Resuming: reclaiming the portal session at the user's request")

    def sync_history(self) -> None:
        self.settings.banner.show_message("Fetching history…", Severity.INFO)
        self._sync.sync_history()

    def cancel_history(self) -> None:
        self._sync.cancel_history()

    def _open_sync_panel(self) -> None:
        """Show what is fresh and what is not, with a button per thing."""
        if self._sync_panel is None:
            self._sync_panel = SyncPanel(self._palette, self)
            self._sync_panel.scope_requested.connect(self._sync.sync_scope)
            self._sync_panel.refresh_all_requested.connect(lambda: self.refresh(force=True))
        self._sync.report_scopes()
        self._sync_panel.show()
        self._sync_panel.raise_()

    def _sync_day(self, day: date) -> None:
        self._status.setText(f"Fetching {fmt.day_label(day)}…")
        self._sync.sync_day(day)

    def _on_scopes_ready(self, statuses: list[object]) -> None:
        self._scopes = statuses
        if self._sync_panel is not None and self._sync_panel.isVisible():
            self._sync_panel.show_statuses(statuses, busy=self._busy_scope)  # type: ignore[arg-type]

    def _on_scope_started(self, scope: Scope) -> None:
        self._busy_scope = scope
        self._status.setText(f"Fetching {scope.label.lower()}…")
        if self._sync_panel is not None and self._sync_panel.isVisible():
            self._sync_panel.show_statuses(self._scopes, busy=scope)  # type: ignore[arg-type]

    def _on_scope_finished(self, scope: Scope, count: int) -> None:
        self._busy_scope = None
        if scope is Scope.DAY_DETAIL and count:
            self.attendance.banner.show_message(
                f"Fetched punch detail for {count} day(s).", Severity.SUCCESS
            )

    # --- rendering ------------------------------------------------------------------

    def _apply_month(self, view: MonthView) -> None:
        self._month_view = view
        year, month = self._sync.period

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

        self._render_week(view)
        self._show_current_day(view)
        self._render_status(view)
        # Trends read straight from the cache the month refresh has just written, so they
        # follow it rather than running on their own schedule.
        self._sync.refresh_trends()

    def _show_current_day(self, view: MonthView) -> None:
        """Re-render whichever day Today is showing — not always today's.

        A refresh reloads the month, and the month reload used to always re-analyse today.
        Someone reading Tuesday's punches would be thrown back to the current day fifteen
        minutes later, by a timer, with no explanation.
        """
        if self._viewing_day is not None and self._viewing_day != date.today():
            self._sync.load_day(self._viewing_day)
            return
        self._sync.load_today(view)

    def _render_week(self, view: MonthView) -> None:
        week = analyze_week(
            list(view.month.days),
            week_start=self._week_start,
            policy=self._context.attendance.policy,
            today=date.today(),
            holidays=view.holidays,
        )
        self.week.show_week(week, self._context.attendance.policy.work_target)

    def _apply_day(self, analysis: DayAnalysis, is_today: bool) -> None:
        self.today.show_analysis(analysis, is_today=is_today)
        self.today.set_back_target(self._navigation.origin_name)
        if self._tray is not None and is_today:
            self._tray.set_analysis(analysis)
            self._tray.notify_insights(list(analysis.insights))

    def _render_status(self, view: MonthView) -> None:
        synced = fmt.relative_time(view.last_synced)
        pending = f" · {view.pending_detail} day(s) pending" if view.pending_detail else ""
        self._status.setText(f"Synced {synced}{pending}")

        if view.from_cache and view.last_synced is None:
            self.today.banner.show_message(
                "Showing cached data — not synced yet.", Severity.WARNING, key="cache"
            )
        else:
            self.today.banner.clear_message("cache")

    def _apply_leave(self, data: LeaveData) -> None:
        self.leave.show_leave(data)
        if self._tray is not None:
            # Expiry warnings are worth a toast; the policy caps them to once a day.
            self._tray.notify_insights(data.insights)

    def _apply_swipes(self, requests: list[object]) -> None:
        # Taken from the month view rather than recomputed. This screen used to carry its
        # own third definition of "needs a request", which disagreed with both the others.
        # The whole Attention travels, not just the date, so the screen can say *why* a day
        # was flagged in the same words the Attendance tooltip uses.
        needing: list[Attention] = []
        if self._month_view is not None:
            needing = [
                attention
                for _day, attention in sorted(self._month_view.attention.items())
                if attention.kind is AttentionKind.SHORT_NO_REQUEST
            ]
        self.requests.show_requests(requests, needing=needing)  # type: ignore[arg-type]

    def _on_swipes_decided(self, changes: list[object]) -> None:
        """News the portal never volunteers: a request has been approved or turned down.

        Worth a toast because the alternative is re-opening SpineHR every day to find out,
        which is exactly the errand this app exists to remove.
        """
        from cerepulse.intelligence.attention import decision_insight

        insight = decision_insight(changes)  # type: ignore[arg-type]
        if insight is None:
            return
        self.requests.banner.show_message(
            f"{insight.title} — {insight.detail}", insight.severity, key="decided"
        )
        if self._tray is not None:
            self._tray.notify_insights([insight])

    def _adopt_periods(self, periods: list[tuple[int, int]]) -> None:
        self._fetchable_months = periods
        if self._month_view is not None:
            self._apply_month(self._month_view)

    # --- actions --------------------------------------------------------------------

    def _change_month(self, year: int, month: int) -> None:
        self._sync.period = (year, month)
        self._sync.load_from_cache()
        self.refresh(force=False, quiet=True)

    def _change_week(self, week_start: date) -> None:
        """Move the week, pulling in another month when the week has left this one.

        Only the loaded month's rows are available, so stepping back from the first week of
        a month used to render four blank days and totals that quietly under-reported. The
        week's own month is loaded instead. A week that straddles two months still shows
        only the days from one of them — the grid is fetched a month at a time.
        """
        self._week_start = week_start
        if (week_start.year, week_start.month) != self._sync.period:
            self._change_month(week_start.year, week_start.month)
            return
        if self._month_view is not None:
            self._render_week(self._month_view)

    def _open_day(self, day: date) -> None:
        """Drill into a day from another screen, remembering where it was opened from."""
        self._navigation.drill_to(TODAY_SCREEN)
        self.today.set_back_target(self._navigation.origin_name)
        self._view_day(day)

    def _view_day(self, day: date) -> None:
        """Show one date on Today, pulling in its month first when it is another month."""
        self._viewing_day = day
        if (day.year, day.month) != self._sync.period:
            self._sync.period = (day.year, day.month)
            self._sync.load_from_cache()
            self.refresh(force=False, quiet=True)
        self._sync.load_day(day)

    def _go_back(self) -> None:
        """The Today context bar's exit: back to the origin screen, or back to today."""
        self._viewing_day = None
        if self._navigation.back():
            return
        today = date.today()
        if self._sync.period != (today.year, today.month):
            self._change_month(today.year, today.month)
            return
        if self._month_view is not None:
            self._sync.load_today(self._month_view)

    def _on_screen_changed(self, _index: int) -> None:
        self.today.set_back_target(self._navigation.origin_name)

    def _handle_insight_action(self, insight: Insight) -> None:
        if insight.action is not None:
            self._handle_action(insight.action)

    def _handle_action(self, action: object) -> None:
        """Where an action leads. Insight chips and the next-action card share the set."""
        kind = getattr(action, "kind", None)
        if kind is ActionKind.OPEN_SWIPE_REQUEST:
            self._open_portal(pages.SWIPE_REQUESTS)
        elif kind is ActionKind.OPEN_ATTENDANCE:
            self._open_portal(pages.ATTENDANCE_REPORT)

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
        self._updates.use_config(
            channel=Channel.parse(config.updates.channel),  # type: ignore[attr-defined]
            download_automatically=config.updates.download_automatically,  # type: ignore[attr-defined]
        )
        self._auto.setInterval(config.sync.refresh_interval_minutes * 60_000)  # type: ignore[attr-defined]
        self._apply_theme(config.ui.theme)  # type: ignore[attr-defined]
        if self._tray is not None:
            self._tray.update_config(config.notifications)  # type: ignore[attr-defined]
        if self._month_view is not None:
            self._sync.load_today(self._month_view)

        note = ""
        if not set_registered(config.ui.start_with_windows):  # type: ignore[attr-defined]
            if config.ui.start_with_windows:  # type: ignore[attr-defined]
                # Almost always because this is a source run, not an installed build.
                note = " Start-with-Windows needs an installed build."

        self.about.refresh(channel=config.updates.channel)  # type: ignore[attr-defined]
        self.settings.banner.show_message(
            f"Saved. Switching tray mode or theme fully applies on the next start.{note}",
            Severity.SUCCESS,
        )

    def _test_notification(self) -> None:
        """Try a toast and say plainly what happened, including when nothing could."""
        if self._tray is None:
            self.settings.banner.show_message(
                "No system tray is available, so notifications cannot be shown.",
                Severity.WARNING,
            )
            return

        outcome = self._tray.self_test()
        self.settings.banner.show_message(
            outcome,
            Severity.SUCCESS if outcome.startswith("Sent") else Severity.WARNING,
        )

    def _apply_theme(self, theme: str) -> None:
        from PySide6.QtWidgets import QApplication

        self._palette = palette_for(theme)
        application = QApplication.instance()
        if application is not None:
            application.setStyleSheet(stylesheet(self._palette))  # type: ignore[attr-defined]

    def _sign_out(self, forget: bool) -> None:
        self._sync.sign_out(forget=forget, on_success=lambda _: self.prompt_sign_in())

    def _clear_cache(self) -> None:
        confirmed = QMessageBox.question(
            self,
            "Clear cached data?",
            "Local attendance and leave data will be removed and re-synced on the next "
            "refresh. Nothing changes in SpineHR.",
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        self._sync.clear_cache(on_success=lambda _: self.refresh(force=True))

    # --- feedback -------------------------------------------------------------------

    def _on_sync_finished(self, failures: list[str]) -> None:
        if failures:
            self.today.banner.show_message(
                f"Some data could not be refreshed: {failures[0]}",
                Severity.WARNING,
                key="sync",
            )
        else:
            # Only its own message. Clearing the whole strip meant a successful sync
            # silently swallowed the sign-in error sitting above it.
            self.today.banner.clear_message("sync")

    def _on_busy_changed(self, busy: bool) -> None:
        self.today.set_loading(busy)
        if not busy and self._month_view is not None:
            self._render_status(self._month_view)

    def _on_activity(self, label: str, queued: int) -> None:
        """Say what is running, not merely that something is.

        Ten task names used to collapse into "Syncing…", so a two-minute history backfill
        and a cache read looked identical. The queue depth matters too: the pool is
        single-slot by design, so work behind a backfill genuinely waits.
        """
        if not label:
            return
        waiting = f" · {queued} waiting" if queued else ""
        self._status.setText(f"{label}…{waiting}")

    def _on_degraded(self, scope: str, exc: BaseException) -> None:
        """A part of a screen is missing, but the screen still works.

        These used to be debug lines and nothing else, so a leave ledger that never loaded
        looked exactly like a leave ledger with no rows.
        """
        logger.warning("Could not load {}: {}", scope, exc)
        banner = {
            "insights": self.insights.banner,
            "leave ledger": self.leave.banner,
            "swipe requests": self.requests.banner,
        }.get(scope, self.today.banner)
        banner.show_message(
            f"Could not load {scope}: {_message_for(exc)}", Severity.WARNING, key=scope
        )

    def _on_error(self, exc: BaseException) -> None:
        if isinstance(exc, SessionTakenError):
            self._stand_down()
            return
        if isinstance(exc, SessionExpiredError | AuthenticationError):
            # Recovery already tried and failed; only now is a prompt warranted.
            self.prompt_sign_in()
            return
        self.today.banner.show_message(
            _message_for(exc),
            Severity.WARNING if isinstance(exc, TransportError) else Severity.CRITICAL,
            key="error",
        )


def _message_for(exc: BaseException) -> str:
    """User-facing text. Internals stay in the logs (Chapter 14 section 10)."""
    if isinstance(exc, CerePulseError):
        return exc.user_message if type(exc).user_message else str(exc)
    return "Something went wrong. Check the logs for details."
