"""The application shell.

Holds the sidebar, the stack of screens, and the wiring between the two. Everything that
leaves the GUI thread lives in ``ui/controllers``: this class submits no work and owns no
fetching logic. It assembles, renders what the controllers hand it, and decides what a
click means.

Load order is deliberate. Cached data is rendered first and a refresh is kicked off behind
it, so the window is useful immediately rather than after a round trip to the portal.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

from loguru import logger
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
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
from cerepulse.ui.views.about import AboutView, open_logs
from cerepulse.ui.views.attendance import AttendanceView
from cerepulse.ui.views.insights import InsightsView
from cerepulse.ui.views.records import RecordsView, open_url
from cerepulse.ui.views.settings import SettingsView
from cerepulse.ui.views.sync_panel import SyncPanel
from cerepulse.ui.views.today import TodayView
from cerepulse.ui.views.week import WeekView
from cerepulse.ui.widgets import Banner
from cerepulse.ui.workers import TaskRunner
from cerepulse.update import Channel

SCREENS = ("Today", "Week", "Attendance", "Insights", "Records", "Settings", "About")
TODAY_SCREEN = SCREENS.index("Today")
SETTINGS_SCREEN = SCREENS.index("Settings")

#: Where a free TomTom key comes from. Linked rather than walked through step by step, so a
#: vendor redesign cannot turn this into instructions that confidently describe buttons that
#: no longer exist.
KEY_GUIDE_URL = "https://developer.tomtom.com/how-to-get-tomtom-api-key"

SIDEBAR_WIDTH = 196
#: Room the sidebar status button actually has for text: the sidebar, less its 8px margins,
#: less the 14px padding and 1px border each side that ``#SidebarButton`` carries. Taken
#: from the geometry rather than read off the widget, because the first status is set before
#: the layout has ever run and ``width()`` would still be reporting a default.
STATUS_TEXT_WIDTH = SIDEBAR_WIDTH - 16 - 30


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
        #: The two record sources that arrive on their own schedules, kept so the
        #: timeline can be rebuilt whenever any one of them lands.
        self._swipes: list[object] = []
        self._ledger: list[object] = []
        self._applications: list[object] = []
        #: Months known synced or cached, refreshed on every month render. Held so stepping
        #: the period can decide "does this month need a fetch" without a database read on
        #: the GUI thread.
        self._synced_months: set[tuple[int, int]] = set()
        #: Collapses a burst of "the timeline changed" calls into one render. A sync delivers
        #: all four sources within a turn or two of the event loop, and rebuilding four times
        #: means three renders that nothing ever sees.
        self._records_pending = QTimer(self)
        self._records_pending.setSingleShot(True)
        self._records_pending.timeout.connect(self._rebuild_records_now)
        #: The unelided sidebar status, kept because the button only holds the short form.
        self._status_text = ""

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
        self.records = RecordsView(self._palette)
        self.settings = SettingsView(self._context.config)
        self.about = AboutView()

        for view in (
            self.today,
            self.week,
            self.attendance,
            self.insights,
            self.records,
            self.settings,
            self.about,
        ):
            self._stack.addWidget(view)

        for banner in (self.today.banner, self.records.banner, self.insights.banner):
            banner.logs_requested.connect(open_logs)

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
        # Through `refresh`, not straight to the service. A Refresh button that cannot end
        # a pause is worse than no button: Records is the screen the portal is opened from,
        # so its Refresh was the one the user reached for and the one that did nothing.
        self.records.refresh_requested.connect(self._refresh_records)
        self.records.open_portal.connect(lambda: self._open_portal(pages.SWIPE_REQUESTS))
        self.records.day_selected.connect(self._open_day)
        self.records.period_changed.connect(self._rebuild_records)
        self.week.week_changed.connect(self._change_week)
        self.week.day_selected.connect(self._open_day)
        self.insights.refresh_requested.connect(self._refresh_insights)
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
        self.settings.address_lookup_requested.connect(self._find_address)
        self.settings.place_picked.connect(self._place_chosen)
        self.settings.open_in_maps_requested.connect(self._open_in_maps)
        self.settings.key_check_requested.connect(self._check_key)
        self.settings.key_guide_requested.connect(self._open_key_guide)
        self.today.commute_refresh_requested.connect(self._refresh_commute)
        self.today.commute.departure_changed.connect(self._commute_departure_changed)
        self.today.commute_setup_requested.connect(
            lambda: self._navigation.drill_to(SETTINGS_SCREEN)
        )

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)

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

        # Signing a browser in without a password has worked since 0.9 and was reachable
        # from two buttons buried in a day drawer and a Records header — places you find
        # only if you already know they are there. The sidebar is the one surface visible
        # from every screen, so it is where "take me to the portal" belongs.
        self._portal = QPushButton("Open SpineHR")
        self._portal.setObjectName("SidebarButton")
        self._portal.setToolTip(
            "Opens the portal in your browser, already signed in.\n"
            "CerePulse pauses while you have it — press Refresh when you are done."
        )
        self._portal.clicked.connect(lambda: self._open_portal(pages.HOME))
        layout.addWidget(self._portal)

        # The status is the way in to per-scope sync, so it is a button rather than a
        # label: the one place that says how fresh things are should also be where you go
        # to do something about it.
        self._status = QPushButton("Starting…")
        self._status.setObjectName("SidebarButton")
        self._status.clicked.connect(self._open_sync_panel)
        layout.addWidget(self._status)
        self._set_status("Starting…")
        return sidebar

    def _set_status(self, text: str) -> None:
        """Set the sidebar status, elided to fit, with the whole of it as the tooltip.

        The button has about 152 px of text width — roughly 23 characters — and the strings
        it is given run to 36: "Synced 12 min ago · 4 day(s) pending" arrived clipped mid
        word. A QPushButton neither wraps nor elides on its own, and the tooltip used to be
        a fixed sentence, so the clipped part was simply unrecoverable. Every caller goes
        through here; setting the text directly is what let nine call sites each be wrong.
        """
        self._status_text = text
        metrics = self._status.fontMetrics()
        shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight, STATUS_TEXT_WIDTH)
        self._status.setText(shown)
        self._status.setToolTip(f"{text}\n\nClick for what has been synced, and when")

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

        # Say a TomTom key is on file. The field itself never shows the key back — but
        # without this the difference between "configured" and "never set up" was invisible,
        # and a stored key read as a bug.
        from cerepulse.core.secrets import TOMTOM_KEY, get_secret

        self.settings.show_stored_key(bool(get_secret(TOMTOM_KEY)))

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
        self._sync.ledger_ready.connect(self._apply_ledger)
        self._sync.swipes_ready.connect(self._apply_swipes)
        self._sync.applications_ready.connect(self._apply_applications)
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
        self._set_status("Signing in…")
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
            self._set_status("Not signed in — showing cached data only.")

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
                # Quiet callers are not all timers. Changing month or opening a day also
                # come through here, and returning silently made those look broken rather
                # than paused — the screen simply did not fill in, with nothing said.
                self._set_status("Paused — signed in elsewhere")
                return
            self._resume()
        if not quiet:
            self._set_status("Refreshing…")
        self._sync.refresh(force=force)

    def _refresh_records(self) -> None:
        """Records' own Refresh, which must also be able to end a pause."""
        if self._paused:
            self._resume()
        self._set_status("Refreshing…")
        self._sync.refresh_leave()

    def _refresh_insights(self) -> None:
        """Insights reads the cache rather than fetching, so a pause does not block it.

        It still ends one: the user pressing Refresh anywhere means the same thing, and
        having it mean something different here is the kind of inconsistency that makes a
        button feel unreliable.
        """
        if self._paused:
            self._resume()
            self._sync.refresh(force=True)
        self._sync.refresh_trends()

    def _open_portal(self, page: str) -> None:
        """Open SpineHR in the browser, signed in, and hand the session over.

        The browser lands on the portal's own home page: the login form is posted at the top
        level so the cookies it issues are first-party, and that means control passes to the
        portal the moment it responds. ``page`` is only the fallback destination for when
        signing in could not be arranged at all.

        It is a handover, not a share — a cookie cannot be injected into a browser from
        outside it, so this creates a second session and the portal ends ours. Standing
        down first is what stops the two fighting over it.
        """
        self._set_status("Opening SpineHR…")

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

        self._sync.open_in_browser(on_ready=ready, on_error=failed)

    def _stand_down(self, message: str = "") -> None:
        """Stop using the portal because something else is signed in as the same user.

        SpineHR allows one session, so the alternative — signing straight back in — takes it
        off whatever else has it. When that is the user's own browser the two trade it back
        and forth all afternoon, each breaking the other, and the app is the one that should
        give way: it has a cache to fall back on and the browser does not.
        """
        self._paused = True
        self._auto.stop()

        # The session is gone, so stop pretending otherwise. Left alone, the cookie jar
        # keeps a dead `.ASPXFORMSAUTH`, `is_authenticated` reports True, and every later
        # request spends a round trip rediscovering that — while the menu it cached still
        # holds privilege tokens minted for a session that no longer exists, which come
        # back as a privileges error rather than an honest sign-in prompt.
        self._context.client.clear_cookies()
        self._context.gateway.forget_menu()
        logger.info("Paused: the portal session is in use elsewhere")

        self._set_status("Paused — signed in elsewhere")
        text = message or (
            "SpineHR is signed in somewhere else, so CerePulse has paused rather than "
            "taking the session back. Showing cached data. Press Refresh when you are "
            "done in the browser."
        )
        for banner in self._session_banners:
            banner.show_message(text, Severity.WARNING, key="session")

    def _resume(self) -> None:
        """Take the session back, because the user just asked for it.

        Clearing the flag is not enough on its own, and used not to be: the very next
        request would bounce to the login page, the auth layer would read that as a second
        eviction, and the app would pause again before the user had finished reading the
        banner clearing. Authorising one sign-in is what turns Refresh from a gesture into
        the thing it says it is.
        """
        self._paused = False
        for banner in self._session_banners:
            banner.clear_message("session")
        self._context.auth.expect_reclaim()
        self._auto.start()
        logger.info("Resuming: reclaiming the portal session at the user's request")

    @property
    def _session_banners(self) -> tuple[Banner, ...]:
        """Every banner a session message goes on.

        One screen is not enough. The pause is most often triggered from Records or
        Attendance — those are the screens with a portal button — and writing the
        explanation only to Today put it where the user demonstrably was not looking.
        """
        return (self.today.banner, self.records.banner, self.attendance.banner)

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
        """Fetch one day, ending a pause if there is one.

        Asking for a specific day is the user asking for the session back just as plainly as
        pressing Refresh is. Without this, pressing it while paused raised
        ``SessionTakenError`` and did nothing at all — and the way out was to press a
        different button first, which nothing on screen said.
        """
        if self._paused:
            self._resume()
        self._set_status(f"Fetching {fmt.day_label(day)}…")
        self._sync.sync_day(day)

    def _on_scopes_ready(self, statuses: list[object]) -> None:
        self._scopes = statuses
        if self._sync_panel is not None and self._sync_panel.isVisible():
            self._sync_panel.show_statuses(statuses, busy=self._busy_scope)  # type: ignore[arg-type]

    def _on_scope_started(self, scope: Scope) -> None:
        self._busy_scope = scope
        self._set_status(f"Fetching {scope.label.lower()}…")
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
        year, month = self._sync.period
        # A payload for a month that is no longer current is dropped, not rendered. The
        # runner serialises, so a load queued before a second period change delivers late —
        # and rendering it made the table lag the picker by one stale month.
        if (view.analysis.year, view.analysis.month) != (year, month):
            logger.debug(
                "Dropping a stale month view for {}-{:02d}", view.analysis.year, view.analysis.month
            )
            return
        self._month_view = view

        # Offer every month the portal can serve, not only what is cached, so history is
        # reachable from the picker. The fetchable list needs the portal, so fall back to
        # the cache when offline.
        # Synced, not merely non-empty: a month before the employee joined is genuinely
        # empty and should not read as "not synced" forever.
        cached = self._context.attendance.synced_months() | set(
            self._context.attendance.cached_months(self._employee_code)
        )
        # Kept for _change_month, which must not pay a database read per step.
        self._synced_months = cached
        months = sorted(cached | {(year, month)}, reverse=True)
        if self._fetchable_months:
            months = sorted(set(self._fetchable_months) | cached | {(year, month)}, reverse=True)
        self.attendance.set_available_months(months, (year, month), cached=cached)
        self.attendance.show_month(view)

        self._render_week(view)
        self._rebuild_records()
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

    def _notify_today(self) -> None:
        """Let the tray judge today even while the screen is showing another day.

        Reading Tuesday's punches should not switch the day's alerts off. The month view
        already holds an analysis per day, so this needs no fetch — and going through the
        same policy means quiet hours and once-per-day still apply.
        """
        if self._tray is None or self._month_view is None:
            return
        analysis = self._month_view.analyses.get(date.today())
        if analysis is not None:
            self._tray.notify_insights(list(analysis.insights))

    def _render_week(self, view: MonthView) -> None:
        week = analyze_week(
            list(view.month.days),
            week_start=self._week_start,
            policy=self._context.attendance.policy,
            today=date.today(),
            holidays=view.holidays,
        )
        # The outstanding items come from the month view rather than being recomputed, so
        # the week cannot disagree with the Attendance highlight about what needs doing.
        self.week.show_week(
            week,
            self._context.attendance.policy.work_target,
            attention=view.attention,
            # Already computed for the month rollup, so the week's timelines cost nothing
            # beyond passing them along.
            analyses=view.analyses,
        )

    def _apply_day(self, analysis: DayAnalysis, is_today: bool) -> None:
        self.today.show_analysis(analysis, is_today=is_today)
        self.today.set_back_target(self._navigation.origin_name)
        self._analysis = analysis
        self._maybe_estimate_commute(analysis, is_today=is_today)
        if self._tray is not None and is_today:
            self._tray.set_analysis(analysis)
            self._tray.notify_insights(list(analysis.insights))
        elif self._tray is not None:
            # Reading a past date must not switch notifications off. `_show_current_day`
            # loads the day being *viewed* rather than today, so parking the screen on last
            # Tuesday meant every refresh for the rest of the week arrived here with
            # `is_today` False and told the tray nothing at all.
            self._notify_today()

    def _render_status(self, view: MonthView) -> None:
        synced = fmt.relative_time(view.last_synced)
        pending = f" · {view.pending_detail} day(s) pending" if view.pending_detail else ""
        self._set_status(f"Synced {synced}{pending}")

        if view.from_cache and view.last_synced is None:
            self.today.banner.show_message(
                "Showing cached data — not synced yet.", Severity.WARNING, key="cache"
            )
        else:
            self.today.banner.clear_message("cache")

    def _apply_leave(self, data: LeaveData) -> None:
        self.records.show_leave(data)
        if self._tray is not None:
            # Expiry warnings are worth a toast; the policy caps them to once a day.
            self._tray.notify_insights(data.insights)

    def _apply_swipes(self, requests: list[object]) -> None:
        self._swipes = list(requests)
        self.records.show_requests(requests)
        self._rebuild_records()

    def _apply_ledger(self, transactions: list[object]) -> None:
        self._ledger = list(transactions)
        self._rebuild_records()

    def _apply_applications(self, applications: list[object]) -> None:
        self._applications = list(applications)
        self._rebuild_records()

    def _rebuild_records(self) -> None:
        """Ask for a rebuild; the work happens once, after the current burst has landed.

        The four sources arrive on their own schedules — the month with a sync, then the
        requests, the ledger and the filed applications behind it — so each calls this rather
        than waiting for all four, because a partial timeline is more useful than none.

        The rebuild itself is deferred to a zero-delay timer so a sync that delivers all four
        within one turn of the event loop assembles the timeline once instead of four times.
        Three of those four renders were always thrown away by the next one.
        """
        self._records_pending.start(0)

    def _rebuild_records_now(self) -> None:
        """Load the period's days off the GUI thread, then assemble the timeline.

        The days come from the cache by date range rather than from the month on the
        Attendance picker. They used to share it — which meant "the timeline" was one month
        of leave and absence beside *every* request ever filed, and changing the displayed
        month silently changed what Records claimed had happened.
        """
        self.records.show_holidays(
            self._month_view.holidays if self._month_view else [],
            today=date.today(),
        )
        today = date.today()
        start = self.records.period_start(today=today)
        # "Everything" is bounded only by the cache; any date before the portal's own
        # records serves as no floor at all.
        floor = start or date(2000, 1, 1)
        self._runner.submit(
            "records-window",
            lambda: self._context.attendance.days_between(
                self._employee_code, start=floor, end=today
            ),
            on_success=lambda days: self._render_records(list(days), start),
            on_error=lambda exc: logger.warning("Could not load the records window: {}", exc),
        )

    def _render_records(self, days: list[object], start: date | None) -> None:
        from cerepulse.intelligence.records import build_records

        records = build_records(
            days=days,  # type: ignore[arg-type]
            requests=self._swipes,  # type: ignore[arg-type]
            transactions=self._ledger,  # type: ignore[arg-type]
            applications=self._applications,  # type: ignore[arg-type]
        )
        if start is not None:
            # The other sources are all-time in memory; the window has to bound them too,
            # or "last 2 months" would show two months of leave beside years of requests.
            records = [record for record in records if record.day >= start]
        self.records.show_records(records)

    def _on_swipes_decided(self, changes: list[object]) -> None:
        """News the portal never volunteers: a request has been approved or turned down.

        Worth a toast because the alternative is re-opening SpineHR every day to find out,
        which is exactly the errand this app exists to remove.
        """
        from cerepulse.intelligence.attention import decision_insight

        insight = decision_insight(changes)  # type: ignore[arg-type]
        if insight is None:
            return
        self.records.banner.show_message(
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
        """Move the period. A step is a cache read, not a sync.

        Every step used to start a full five-scope sync on the single-slot runner, so
        stepping twice queued the second month's load behind the first month's network
        round trip — the table lagged the picker by however long the portal took, which
        read as months not switching at all. A cached month renders instantly; only a
        month never synced costs a fetch, and only its own attendance scope.
        """
        self._sync.period = (year, month)
        self._sync.load_from_cache()
        if (year, month) not in self._synced_months:
            self._sync.sync_scope(Scope.ATTENDANCE)

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
            self._copy_request_date(getattr(action, "on", None))
            self._open_portal(pages.SWIPE_REQUESTS)
        elif kind is ActionKind.OPEN_ATTENDANCE:
            self._open_portal(pages.ATTENDANCE_REPORT)

    def _copy_request_date(self, day: date | None) -> None:
        """Put the request date on the clipboard, in the portal's own format.

        The form itself cannot be pre-filled: Add New is a postback on the list page, so the
        Type, Category and Request Date controls do not exist until the user clicks it, and
        no URL addresses them. Type and Category are dropdowns anyway. The date is the one
        field that has to be typed, and it is the one thing the app already knows — so it
        goes on the clipboard rather than being read off one window and retyped into
        another. ``14-Jul-26`` is the portal's own rendering, zero-padded, so it can be
        pasted without editing.
        """
        if day is None:
            return
        clipboard = QApplication.clipboard()
        if clipboard is None:  # offscreen and some remote sessions have none
            return
        stamp = day.strftime("%d-%b-%y")
        clipboard.setText(stamp)
        self._set_status(f"Copied {stamp}")

    def _save_config(self, config: object) -> None:
        from cerepulse.core.config import save_config

        try:
            save_config(config)  # type: ignore[arg-type]
        except CerePulseError as exc:
            # Next to the Save button, not the top-of-page banner: the save bar is pinned
            # outside the scroll area, so on a long page the banner and the button were
            # never visible together and a failed save looked like nothing happening.
            self.settings.show_save_result(f"Could not save: {exc}", failed=True)
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
        self.settings.show_save_result(
            f"Saved. Switching tray mode or theme fully applies on the next start.{note}"
        )

    # --- the journey home -----------------------------------------------------------

    def _maybe_estimate_commute(self, analysis: DayAnalysis, *, is_today: bool) -> None:
        """Render the card, and spend a call only when one could change the answer.

        On any day but today the card is hidden outright — an arrival time for a journey
        already made answers nothing, and an inert card with a dash in it reads as broken
        rather than as not applicable.
        """
        leaving = self._departure_for(analysis) if is_today else None
        if leaving is None:
            self.today.commute.setVisible(False)
            return
        self.today.commute.setVisible(True)

        service = self._context.commute
        if service.should_ask(leaving, now=datetime.now()):
            self._refresh_commute(force=False)
            return
        # Nothing to fetch, but the held estimate still wants painting against the current
        # departure — the exit time drifts as punches land, and the arrival must drift with
        # it rather than describing a departure that has moved on.
        self.today.show_commute(service.estimate(leaving))

    def _departure_for(self, analysis: DayAnalysis) -> datetime | None:
        """Which departure the card is asking about, from its own selector.

        Three bases: the predicted exit (the default question), right now ("how long if I
        left this minute?"), or a time the user typed. The basis only changes the departure
        the service is asked for — the freshness floor, the in-flight lock and the budget
        neither know nor care which question is being asked.
        """
        basis = self.today.commute.departure_basis()
        if basis == "now":
            return datetime.now()
        if basis == "custom":
            picked = self.today.commute.custom_departure()
            return datetime.combine(date.today(), picked)
        return analysis.leave_at

    def _commute_departure_changed(self) -> None:
        analysis = self._analysis
        if analysis is None:
            return
        leaving = self._departure_for(analysis)
        if leaving is None:
            return
        # From the held estimate when the bucket is unchanged; a new bucket is a new
        # question and fetches through the ordinary guards.
        self._runner.submit(
            "commute",
            lambda: self._context.commute.estimate(leaving),
            on_success=lambda view: self.today.show_commute(view),
            on_error=lambda exc: logger.warning("Commute refresh failed: {}", exc),
        )

    def _find_address(self, end: str, text: str) -> None:
        """Resolve whatever was pasted for one end of the journey.

        The three paths, in the order they are tried:

        * **A pinned point** — coordinates in any form Google copies. Exact by definition,
          so it saves as soon as the reverse lookup has named it. Nothing to choose.
        * **A short link** — the phone's Share button. Expanded by asking Google for the
          redirect target (a header-only request, no key, no page), then parsed again.
        * **An address** — a search, whose best answer is a guess. The top matches come
          back as candidates and nothing is saved until the user picks one.
        """
        from cerepulse.commute.locations import (
            coordinates_in,
            describe_paste,
            is_short_link,
        )
        from cerepulse.commute.tomtom import TomTomClient, expand_short_link

        field = self.settings.address_field(end)
        typed = text.strip()
        if not typed:
            field.show_status("Type or paste something first.")
            return
        key = self._tomtom_key()
        if not key:
            field.show_status("Add a TomTom key first — the lookup needs one.")
            return

        commute = self._context.config.commute
        office = (commute.origin_lat, commute.origin_lon)
        near = office if office != (0.0, 0.0) else None

        pinned = coordinates_in(typed, near=near)
        if pinned is not None:
            field.show_status("Naming that point…")
            self._runner.submit(
                "locate",
                lambda: TomTomClient(key).locate(*pinned),
                on_success=lambda place: self._save_place(end, place),
                on_error=lambda exc: field.show_status(
                    f"Could not name that point: {_message_for(exc)}"
                ),
            )
            return

        if is_short_link(typed):
            field.show_status("Expanding the short link…")

            def resolve_link() -> object:
                expanded = expand_short_link(typed)
                if expanded is None:
                    return None
                point = coordinates_in(expanded, near=near)
                return TomTomClient(key).locate(*point) if point else None

            self._runner.submit(
                "expand-link",
                resolve_link,
                on_success=lambda place: (
                    self._save_place(end, place)
                    if place is not None
                    else field.show_status(
                        "Could not read that link. Open it in a browser and copy the full "
                        "address bar instead."
                    )
                ),
                on_error=lambda exc: field.show_status(
                    f"Could not expand that link: {_message_for(exc)}"
                ),
            )
            return

        from cerepulse.commute.locations import looks_like_maps_url

        if looks_like_maps_url(typed):
            # A Maps URL with no point in it. Searching TomTom for a URL would return
            # *something*, which is exactly the silent wrong answer this field must not give.
            field.show_status(describe_paste(typed))
            return

        field.show_status("Searching…")
        self._runner.submit(
            "geocode",
            lambda: TomTomClient(key).search(typed),
            on_success=lambda places: self._offer_places(end, places),
            on_error=lambda exc: field.show_status(f"Could not look that up: {_message_for(exc)}"),
        )

    def _offer_places(self, end: str, places: object) -> None:
        field = self.settings.address_field(end)
        found = list(places)  # type: ignore[call-overload]
        if not found:
            field.show_status("Nothing matched that. Try adding the area or city.")
            return
        field.show_candidates(found)

    def _place_chosen(self, end: str, place: object) -> None:
        self._save_place(end, place)

    def _save_place(self, end: str, place: object) -> None:
        """Write a confirmed point into config. The only path that stores coordinates."""
        from cerepulse.core.config import save_config

        resolved = str(place.resolved)  # type: ignore[attr-defined]
        latitude = float(place.latitude)  # type: ignore[attr-defined]
        longitude = float(place.longitude)  # type: ignore[attr-defined]
        commute = self._context.config.commute
        if end == "office":
            commute = replace(commute, origin=resolved, origin_lat=latitude, origin_lon=longitude)
        else:
            commute = replace(
                commute,
                destination=resolved,
                destination_lat=latitude,
                destination_lon=longitude,
            )
        config = replace(self._context.config, commute=commute)
        try:
            save_config(config)
        except CerePulseError as exc:
            self.settings.address_field(end).show_status(f"Found it, but could not save: {exc}")
            return
        self._context.apply_config(config)
        self.settings.set_config(config)
        self.settings.address_field(end).show_status(
            f"Saved: {resolved} ({latitude:.5f}, {longitude:.5f}). "
            "Check it on the map if in doubt.",
            located=True,
        )

    def _open_in_maps(self, end: str) -> None:
        """Show the saved point in the browser — the one confirmation that is conclusive.

        Free and keyless: it is a plain Google Maps URL, so checking the pin costs nothing
        and works whether or not TomTom is reachable.
        """
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        commute = self._context.config.commute
        latitude, longitude = (
            (commute.origin_lat, commute.origin_lon)
            if end == "office"
            else (commute.destination_lat, commute.destination_lon)
        )
        if latitude == 0.0 and longitude == 0.0:
            self.settings.address_field(end).show_status("Nothing saved to show yet.")
            return
        QDesktopServices.openUrl(QUrl(f"https://www.google.com/maps?q={latitude},{longitude}"))

    def _check_key(self, key: str) -> None:
        """Validate a pasted key, and store it only when that means something.

        The four outcomes are not decoration. A rejected key must not be stored, or the
        feature fails silently on the one evening somebody relies on it — and an unreachable
        TomTom must not block storing a perfectly good key, because "your key is wrong" is
        an unfixable message when the real problem is the wifi.
        """
        typed = key.strip()
        if not typed:
            self.settings.show_key_result("Paste a key first.")
            return

        self.settings.show_key_result("Checking with TomTom…")
        self._runner.submit(
            "key-check",
            lambda: self._context.commute.validate_key(typed),
            on_success=lambda check: self._key_checked(check, typed),
            on_error=lambda exc: self.settings.show_key_result(
                f"Could not check the key: {_message_for(exc)}"
            ),
        )

    def _key_checked(self, check: object, key: str) -> None:
        from cerepulse.core.secrets import TOMTOM_KEY, store_secret

        message = check.message  # type: ignore[attr-defined]
        if check.is_usable or check.is_uncertain:  # type: ignore[attr-defined]
            store_secret(TOMTOM_KEY, key)
            self._context.commute.use_key(key)
            self.settings.show_stored_key(True)
            if check.is_uncertain:  # type: ignore[attr-defined]
                message += " Saved anyway — it may well be fine."
            self._refresh_commute()
        self.settings.show_key_result(message)

    def _open_key_guide(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(KEY_GUIDE_URL))
        self.settings.show_key_result(
            "Register free (no card), create a project, then copy its key and paste it here."
        )

    def _tomtom_key(self) -> str:
        from cerepulse.core.secrets import TOMTOM_KEY, get_secret

        return self.settings.typed_key() or get_secret(TOMTOM_KEY)

    def _refresh_commute(self, *, force: bool = True) -> None:
        """Ask for an arrival estimate. The button never refuses; the service decides cost."""
        analysis = self._analysis
        if analysis is None:
            return
        leaving = self._departure_for(analysis)
        if leaving is None:
            return
        self._runner.submit(
            "commute",
            lambda: self._context.commute.estimate(leaving, force=force),
            on_success=lambda view: self.today.show_commute(view),
            # A maps outage costs this one card. Nothing about attendance depends on it, so
            # it must never reach the shared banner or look like a portal problem.
            on_error=lambda exc: logger.warning("Commute refresh failed: {}", exc),
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
        self._set_status(f"{label}…{waiting}")

    def _on_degraded(self, scope: str, exc: BaseException) -> None:
        """A part of a screen is missing, but the screen still works.

        These used to be debug lines and nothing else, so a leave ledger that never loaded
        looked exactly like a leave ledger with no rows.

        Losing the session is not a degradation of one scope, whichever scope happened to
        notice first — nothing else will load either. It goes to the same place a failed
        sync does, or the app reports a missing swipe list and keeps trying everything else
        against a session somebody else is using.
        """
        if isinstance(exc, SessionExpiredError | AuthenticationError):
            self._on_error(exc)
            return
        logger.warning("Could not load {}: {}", scope, exc)
        banner = {
            "insights": self.insights.banner,
            "leave ledger": self.records.banner,
            "swipe requests": self.records.banner,
        }.get(scope, self.today.banner)
        banner.show_message(
            f"Could not load {scope}: {_message_for(exc)}",
            Severity.WARNING,
            key=scope,
            offer_logs=True,
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
            # The generic message is the one that says "check the logs" and, until now,
            # left the user to find out where those are.
            offer_logs=not isinstance(exc, CerePulseError),
        )


def _message_for(exc: BaseException) -> str:
    """User-facing text. Internals stay in the logs (Chapter 14 section 10)."""
    if isinstance(exc, CerePulseError):
        return exc.user_message if type(exc).user_message else str(exc)
    return "Something went wrong. Check the logs for details."
