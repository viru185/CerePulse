"""GUI entry point."""

from __future__ import annotations

import sys

from loguru import logger
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from cerepulse import __about__ as about
from cerepulse.app import build_app
from cerepulse.core.config import AppConfig
from cerepulse.core.errors import CerePulseError
from cerepulse.ui.assets import app_icon
from cerepulse.ui.main_window import MainWindow
from cerepulse.ui.theme import palette_for, stylesheet

#: Identifies the app to the Windows shell. Toast notifications are attributed to the
#: process AUMID, and taskbar grouping and jump lists key off it too.
APP_USER_MODEL_ID = "VirenHirpara.CerePulse"


def set_app_user_model_id(model_id: str = APP_USER_MODEL_ID) -> bool:
    """Give the process a stable identity in the Windows shell. True when it took.

    Without this, Windows infers an identity from the executable — which differs between a
    ``python.exe`` run from a virtualenv and an installed ``CerePulse.exe``. The visible
    consequence is that the app may not appear under Settings → System → Notifications at
    all, so a user whose toasts have been switched off has nothing to switch back on.

    Must run before the first window exists, hence before ``QApplication``.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(model_id)
    except (AttributeError, OSError) as exc:
        logger.warning("Could not set the app model id; notifications may be anonymous: {}", exc)
        return False
    return True


def run_app(config: AppConfig) -> int:
    """Build the application, show the window, and run the event loop."""
    set_app_user_model_id()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    application = QApplication(sys.argv)
    application.setApplicationName(about.NAME)
    application.setApplicationVersion(about.VERSION)
    application.setOrganizationName(about.AUTHOR)
    application.setWindowIcon(app_icon())
    application.setStyleSheet(stylesheet(palette_for(config.ui.theme)))

    # In tray mode the window closing must not end the process.
    application.setQuitOnLastWindowClosed(config.ui.background_mode != "tray")

    try:
        context = build_app(config=config)
    except CerePulseError as exc:
        logger.error("Could not start: {}", exc)
        QMessageBox.critical(None, f"{about.NAME} could not start", str(exc))
        return 1

    with context:
        window = MainWindow(context)
        window.show()
        window.start()
        return application.exec()
