"""System tray icon: the live "when can I leave?" readout without opening the window.

The tooltip is the point of this whole component. It carries the countdown so the answer is
one hover away, and it updates on a timer independently of any refresh — the target time
does not change between syncs, only the distance to it does.
"""

from __future__ import annotations

from datetime import datetime

from loguru import logger
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from cerepulse import __about__ as about
from cerepulse.core.config import NotificationConfig
from cerepulse.intelligence.day import DayAnalysis, DayState
from cerepulse.intelligence.insights import Insight, Severity
from cerepulse.notify.policy import NotificationPolicy, notification_title
from cerepulse.ui import formatting as fmt

#: How often the countdown in the tooltip is recomputed.
TICK_MS = 30_000

_ICONS = {
    Severity.SUCCESS: QSystemTrayIcon.MessageIcon.Information,
    Severity.INFO: QSystemTrayIcon.MessageIcon.Information,
    Severity.WARNING: QSystemTrayIcon.MessageIcon.Warning,
    Severity.CRITICAL: QSystemTrayIcon.MessageIcon.Critical,
}


class Tray(QObject):
    """Wraps :class:`QSystemTrayIcon` with the countdown and notification policy."""

    open_requested = Signal()
    refresh_requested = Signal()
    quit_requested = Signal()

    def __init__(
        self,
        icon: QIcon,
        config: NotificationConfig,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.policy = NotificationPolicy(config)
        self._analysis: DayAnalysis | None = None

        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip(about.NAME)
        self._tray.activated.connect(self._on_activated)
        self._tray.setContextMenu(self._build_menu())

        self._ticker = QTimer(self)
        self._ticker.setInterval(TICK_MS)
        self._ticker.timeout.connect(self._refresh_tooltip)

    # --- lifecycle ------------------------------------------------------------------

    @staticmethod
    def is_available() -> bool:
        """Whether this desktop offers a tray at all. Rare on Windows, but not guaranteed."""
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        self._tray.show()
        self._ticker.start()

    def hide(self) -> None:
        self._ticker.stop()
        self._tray.hide()

    def _build_menu(self) -> QMenu:
        menu = QMenu()

        self._headline = QAction("Not signed in", menu)
        self._headline.setEnabled(False)
        menu.addAction(self._headline)
        menu.addSeparator()

        open_action = QAction(f"Open {about.NAME}", menu)
        open_action.triggered.connect(self.open_requested)
        menu.addAction(open_action)

        refresh_action = QAction("Refresh now", menu)
        refresh_action.triggered.connect(self.refresh_requested)
        menu.addAction(refresh_action)

        menu.addSeparator()
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self.quit_requested)
        menu.addAction(quit_action)
        return menu

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.open_requested.emit()

    # --- state ----------------------------------------------------------------------

    def set_analysis(self, analysis: DayAnalysis) -> None:
        """Adopt the current day and refresh the readout."""
        self._analysis = analysis
        self._refresh_tooltip()

    def set_status(self, text: str) -> None:
        """Show a transient status where the headline normally sits."""
        self._headline.setText(text)

    def _refresh_tooltip(self) -> None:
        text = self.tooltip_text(self._analysis, now=datetime.now())
        self._tray.setToolTip(text)
        self._headline.setText(text.splitlines()[0])

    @staticmethod
    def tooltip_text(analysis: DayAnalysis | None, *, now: datetime) -> str:
        """The whole point of the tray, as a string. Pure, so it can be tested directly."""
        if analysis is None:
            return f"{about.NAME}\nNot synced yet"

        if analysis.state is DayState.EMPTY:
            return f"{about.NAME}\nNo punches recorded today"

        worked = f"Worked {fmt.duration(analysis.worked)}"

        if analysis.state is DayState.INCOMPLETE:
            if analysis.work_remaining:
                remaining = fmt.countdown(analysis.leave_at, now=now)
                return f"Leave at {fmt.clock(analysis.leave_at)} — {remaining} to go\n{worked}"
            return f"Target met — you can leave\n{worked}"

        if analysis.early_exit:
            return f"Left at {fmt.clock(analysis.last_out)}\n{worked} — short of target"
        return f"Left at {fmt.clock(analysis.last_out)}\n{worked}"

    # --- notifications --------------------------------------------------------------

    def notify_insights(self, insights: list[Insight], *, now: datetime | None = None) -> int:
        """Show whichever insights the policy allows. Returns how many actually appeared.

        The record of what has been sent today is written only after a toast is delivered.
        Recording it up front — as this used to — meant a notification the tray dropped
        still consumed its once-a-day slot, so one failure silenced that kind until
        midnight and the count returned here was a lie.
        """
        shown = 0
        for insight in insights:
            if not self.policy.should_notify(insight, now=now):
                continue
            if self.notify(insight):
                self.policy.record_sent(insight, now=now)
                shown += 1
        return shown

    def notify(self, insight: Insight) -> bool:
        """Show one toast, bypassing the policy. Returns whether it was actually delivered.

        Callers need the answer: an update notice that was silently dropped should fall
        back to a dialog rather than vanishing.
        """
        reason = self.delivery_problem()
        if reason is not None:
            # INFO, not DEBUG. This is the line that explains an absent notification, and
            # at DEBUG it never reached a log anyone would read.
            logger.info("Notification {!r} not delivered: {}", insight.title, reason)
            return False

        self._tray.showMessage(
            notification_title(insight),
            insight.detail or insight.title,
            _ICONS.get(insight.severity, QSystemTrayIcon.MessageIcon.Information),
            8000,
        )
        logger.info("Notified: {}", insight.title)
        return True

    def delivery_problem(self) -> str | None:
        """What would stop a toast right now, or ``None`` if nothing would.

        Separate from :meth:`notify` so Settings can ask without sending anything.
        """
        if not self._tray.isVisible():
            return "the tray icon is not showing"
        if not self._tray.supportsMessages():
            return "this desktop does not support notifications"
        return None

    def self_test(self, *, now: datetime | None = None) -> str:
        """Try to show a test toast and report, in plain words, what happened.

        The commonest complaint about this feature is that nothing appears, and until now
        the app had no way to tell the difference between "Windows suppressed it", "the
        alert is switched off" and "it already fired this morning". This says which.
        """
        if not self.policy.config.enabled:
            return "Notifications are switched off in Settings."

        problem = self.delivery_problem()
        if problem is not None:
            return f"Could not show a notification — {problem}."

        moment = now or datetime.now()
        if self.policy.in_quiet_hours(moment):
            quiet = f"{self.policy.config.quiet_hours_start}–{self.policy.config.quiet_hours_end}"
            return (
                f"Sent, but you are inside quiet hours ({quiet}), so real alerts would be "
                f"held until morning."
            )

        self._tray.showMessage(
            f"{about.NAME} is set up",
            "Notifications are working. This is the only one you asked for.",
            QSystemTrayIcon.MessageIcon.Information,
            8000,
        )
        return (
            "Sent. If nothing appeared, check Windows Settings → System → Notifications "
            f"for {about.NAME}, and that Do Not Disturb is off."
        )

    def update_config(self, config: NotificationConfig) -> None:
        """Adopt changed settings and forget what was already sent."""
        self.policy.config = config
        self.policy.reset()
