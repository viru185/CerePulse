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
        """Show whichever insights the policy allows. Returns how many were shown."""
        shown = 0
        for insight in insights:
            if self.policy.should_notify(insight, now=now):
                self.notify(insight)
                shown += 1
        return shown

    def notify(self, insight: Insight) -> None:
        """Show one toast, bypassing the policy. Used for explicit user-facing events."""
        if not self._tray.isVisible():
            logger.debug("Tray hidden; skipping notification {!r}", insight.title)
            return
        self._tray.showMessage(
            notification_title(insight),
            insight.detail or insight.title,
            _ICONS.get(insight.severity, QSystemTrayIcon.MessageIcon.Information),
            8000,
        )

    def update_config(self, config: NotificationConfig) -> None:
        """Adopt changed settings and forget what was already sent."""
        self.policy.config = config
        self.policy.reset()
