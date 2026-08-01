"""Checking for a newer release, and telling the user about it.

Kept apart from the window because the update story is about to grow considerably —
background download, channels, rollback — and none of that belongs in a class whose other
job is laying out a sidebar.

Two checks, deliberately different. The startup one is silent on failure: nobody opened the
app to be told GitHub was unreachable. The one from the About screen reports both outcomes,
because a user who clicked "Check for updates" is owed an answer either way.
"""

from __future__ import annotations

from collections.abc import Callable

from loguru import logger
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QMessageBox, QWidget

from cerepulse import __about__ as about
from cerepulse.intelligence.insights import Insight, InsightKind, Severity
from cerepulse.ui.whats_new import UpdateAvailableDialog, WhatsNewDialog
from cerepulse.ui.workers import TaskRunner
from cerepulse.update import Release, check_for_update, mark_seen, should_show_whats_new

#: Delay before the startup check, so it never competes with the first paint or the sign-in.
STARTUP_CHECK_DELAY_MS = 4000
WHATS_NEW_DELAY_MS = 400


class UpdateController(QObject):
    """Runs update checks off the GUI thread and presents the result."""

    def __init__(
        self,
        *,
        runner: TaskRunner,
        window: QWidget,
        notifier: Callable[[Insight], bool] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner = runner
        self._window = window
        #: Returns whether a toast was actually delivered. Absent means no tray.
        self._notifier = notifier

    def schedule_startup_checks(self, *, check_for_updates: bool) -> None:
        """Defer both startup jobs so neither blocks the window appearing."""
        QTimer.singleShot(WHATS_NEW_DELAY_MS, self.show_whats_new_if_updated)
        if check_for_updates:
            QTimer.singleShot(STARTUP_CHECK_DELAY_MS, self.check_quietly)

    def show_whats_new_if_updated(self) -> None:
        """Show release notes once, the first time a new version runs."""
        if not should_show_whats_new():
            return
        WhatsNewDialog(version=about.VERSION, parent=self._window).exec()
        mark_seen()

    def check_quietly(self) -> None:
        """Look for a newer release. Failures stay silent — this is never urgent."""
        self._runner.submit(
            "update-check",
            check_for_update,
            on_success=self._on_found,
            on_error=lambda exc: logger.debug("Update check failed: {}", exc),
        )

    def check_now(self, on_error: Callable[[BaseException], None]) -> None:
        """Explicit check from About. Unlike the startup one, this reports "up to date"."""
        self._runner.submit(
            "update-check-manual",
            check_for_update,
            on_success=self._on_manual,
            on_error=on_error,
        )

    # --- presentation -----------------------------------------------------------------

    def _on_manual(self, release: Release | None) -> None:
        if release is None:
            QMessageBox.information(
                self._window,
                "Up to date",
                f"{about.NAME} {about.VERSION} is the latest version.",
            )
            return
        UpdateAvailableDialog(release, parent=self._window).exec()

    def _on_found(self, release: Release | None) -> None:
        if release is None:
            return
        if self._notifier is not None and not self._window.isVisible():
            # Minimised to the tray: a toast is less intrusive than stealing focus.
            delivered = self._notifier(
                Insight(
                    InsightKind.ON_TRACK,
                    Severity.INFO,
                    f"{about.NAME} {release.version} is available",
                    "Open CerePulse to see what changed.",
                )
            )
            if delivered:
                return
        UpdateAvailableDialog(release, parent=self._window).exec()


__all__ = ["STARTUP_CHECK_DELAY_MS", "WHATS_NEW_DELAY_MS", "UpdateController"]
