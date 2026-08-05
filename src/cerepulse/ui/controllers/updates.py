"""Checking for a newer release, fetching it, and handing over to the installer.

The chosen flow: **download silently, ask before installing.** Fetching 70 MB in the
background costs the user nothing and makes the eventual yes instant; replacing the running
application is a decision, so it waits for one.

Two checks, deliberately different. The startup one is silent on failure — nobody opened the
app to be told GitHub was unreachable. The one from About reports both outcomes, because
someone who clicked "Check for updates" is owed an answer either way.

Nothing is executed that this module did not download and verify itself.
"""

from __future__ import annotations

from collections.abc import Callable

from loguru import logger
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QMessageBox, QWidget

from cerepulse import __about__ as about
from cerepulse.intelligence.insights import Insight, InsightKind, Severity
from cerepulse.ui.whats_new import UpdateAvailableDialog, WhatsNewDialog
from cerepulse.ui.workers import TaskRunner
from cerepulse.update import (
    Channel,
    Download,
    Release,
    apply_update,
    check_for_update,
    download_installer,
    fetch_checksum,
    is_installed_build,
    mark_checked,
    mark_seen,
    record_update,
    should_show_whats_new,
)

#: Delay before the startup check, so it never competes with the first paint or the sign-in.
STARTUP_CHECK_DELAY_MS = 4000
WHATS_NEW_DELAY_MS = 400

#: Published alongside the installers so a download can be verified before it is run.
CHECKSUM_ASSET = "SHA256SUMS.txt"


class UpdateController(QObject):
    """Runs the update lifecycle off the GUI thread and presents each step."""

    #: A newer release exists. Carries the Release.
    update_found = Signal(object)
    #: Download progress, 0..1.
    download_progress = Signal(float)
    #: The installer is on disk and verified. Carries the Release.
    update_ready = Signal(object)
    update_failed = Signal(str)
    #: About to quit and hand over. The window should close cleanly.
    handover = Signal()

    def __init__(
        self,
        *,
        runner: TaskRunner,
        window: QWidget,
        channel: Channel = Channel.STABLE,
        download_automatically: bool = True,
        notifier: Callable[[Insight], bool] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner = runner
        self._window = window
        self._channel = channel
        self._auto_download = download_automatically
        #: Returns whether a toast was actually delivered. Absent means no tray.
        self._notifier = notifier
        self._pending: Release | None = None
        self._downloaded: Download | None = None

        # Housekeeping, not policy: every installer this build has already superseded is
        # dead weight (~48 MB each), and nothing else ever looks at that directory again.
        # Installers newer than this build are pending updates and are left alone.
        from cerepulse import __about__ as about
        from cerepulse.update.downloader import clear_spent_installers

        clear_spent_installers(about.VERSION)

    def use_config(self, *, channel: Channel, download_automatically: bool) -> None:
        self._channel = channel
        self._auto_download = download_automatically

    @property
    def pending(self) -> Release | None:
        return self._pending

    @property
    def ready(self) -> Download | None:
        return self._downloaded

    # --- checking ---------------------------------------------------------------------

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
            lambda: check_for_update(channel=self._channel),
            on_success=self._on_found,
            on_error=lambda exc: logger.debug("Update check failed: {}", exc),
        )

    def check_now(self, on_error: Callable[[BaseException], None]) -> None:
        """Explicit check from About. Unlike the startup one, this reports "up to date"."""
        self._runner.submit(
            "update-check-manual",
            lambda: check_for_update(channel=self._channel),
            on_success=self._on_manual,
            on_error=on_error,
        )

    def _on_manual(self, release: Release | None) -> None:
        mark_checked()
        if release is None:
            QMessageBox.information(
                self._window,
                "Up to date",
                f"{about.NAME} {about.VERSION} is the latest version on the "
                f"{self._channel.label.lower()} channel.",
            )
            return
        self._adopt(release)
        self.offer(release)

    def _on_found(self, release: Release | None) -> None:
        mark_checked()
        if release is None:
            return
        self._adopt(release)

        if self._notifier is not None and not self._window.isVisible():
            # Minimised to the tray: a toast is less intrusive than stealing focus.
            delivered = self._notifier(
                Insight(
                    InsightKind.ON_TRACK,
                    Severity.INFO,
                    f"{about.NAME} {release.version} is available",
                    "Open CerePulse to install it.",
                )
            )
            if delivered:
                return
        self.offer(release)

    def _adopt(self, release: Release) -> None:
        self._pending = release
        self.update_found.emit(release)
        if self._auto_download and release.is_installable and is_installed_build():
            self.download(release)

    # --- downloading ------------------------------------------------------------------

    def download(self, release: Release | None = None) -> None:
        """Fetch the installer in the background, verifying it against the checksum."""
        target = release or self._pending
        if target is None or not target.is_installable:
            return

        def run() -> Download:
            base, _, asset = target.installer_url.rpartition("/")
            expected = fetch_checksum(f"{base}/{CHECKSUM_ASSET}", asset)
            return download_installer(
                target.installer_url,
                target.version,
                expected_sha256=expected,
                on_progress=self._report_progress,
            )

        def done(result: Download) -> None:
            self._downloaded = result
            logger.info("Update {} ready to install (verified={})", result.version, result.verified)
            self.update_ready.emit(target)

        self._runner.submit(
            "update-download",
            run,
            on_success=done,
            on_error=lambda exc: self.update_failed.emit(str(exc)),
        )

    def _report_progress(self, fraction: float) -> bool:
        # Worker thread: emitting a signal is safe, touching a widget would not be.
        self.download_progress.emit(fraction)
        return True

    # --- installing -------------------------------------------------------------------

    def offer(self, release: Release | None = None) -> None:
        """Show the release notes and let the user decide."""
        target = release or self._pending
        if target is None:
            return

        dialog = UpdateAvailableDialog(
            target,
            downloaded=self._downloaded is not None,
            can_install=is_installed_build(),
            parent=self._window,
        )
        self.download_progress.connect(dialog.set_progress)
        self.update_ready.connect(dialog.set_ready)
        dialog.install_requested.connect(self.install)
        dialog.download_requested.connect(lambda: self.download(target))
        dialog.exec()

    def install(self) -> None:
        """Quit and hand over to the installer.

        The app goes first: the installer would otherwise force it closed mid-write, and a
        clean shutdown is what releases the database and the single-instance lock.
        """
        if self._pending is None:
            return
        version = self._pending.version
        try:
            apply_update(version)
        except Exception as exc:  # noqa: BLE001 — reported, never fatal
            record_update(version, "failed", str(exc))
            self.update_failed.emit(str(exc))
            return
        self.handover.emit()


__all__ = ["CHECKSUM_ASSET", "STARTUP_CHECK_DELAY_MS", "WHATS_NEW_DELAY_MS", "UpdateController"]
