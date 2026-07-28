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
from cerepulse.ui.main_window import MainWindow
from cerepulse.ui.theme import palette_for, stylesheet


def run_app(config: AppConfig) -> int:
    """Build the application, show the window, and run the event loop."""
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    application = QApplication(sys.argv)
    application.setApplicationName(about.NAME)
    application.setApplicationVersion(about.VERSION)
    application.setOrganizationName(about.AUTHOR)
    application.setStyleSheet(stylesheet(palette_for(config.ui.theme)))

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
