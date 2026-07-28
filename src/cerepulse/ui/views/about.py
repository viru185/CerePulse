"""About — version, developer credit, and where the app keeps its files."""

from __future__ import annotations

import platform
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cerepulse import __about__ as about
from cerepulse.core import paths
from cerepulse.repository.schema import SCHEMA_VERSION
from cerepulse.ui.widgets import Card, SectionTitle, card_row


class AboutView(QWidget):
    """Who made this, what version it is, and where its data lives."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(14)

        title = QLabel(about.NAME)
        title.setStyleSheet("font-size: 30px; font-weight: 700;")
        tagline = QLabel(about.TAGLINE)
        tagline.setObjectName("HeroCaption")
        version = QLabel(f"Version {about.VERSION}")
        version.setObjectName("CardCaption")
        layout.addWidget(title)
        layout.addWidget(tagline)
        layout.addWidget(version)

        summary = QLabel(about.SUMMARY)
        summary.setWordWrap(True)
        summary.setObjectName("CardCaption")
        layout.addWidget(summary)

        layout.addWidget(SectionTitle("Developer"))
        developer = QLabel(about.AUTHOR)
        developer.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(developer)

        links = QHBoxLayout()
        links.setSpacing(8)
        links.addWidget(_link("GitHub", about.GITHUB_URL))
        links.addWidget(_link("LinkedIn", about.LINKEDIN_URL))
        links.addWidget(_link("Repository", about.REPO_URL))
        links.addStretch(1)
        layout.addLayout(links)

        layout.addWidget(SectionTitle("Built on"))
        for name, url, description in about.CREDITS:
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(f"<b>{name}</b> — {description}")
            row.addWidget(label)
            row.addWidget(_link("Open", url))
            row.addStretch(1)
            layout.addLayout(row)

        layout.addWidget(SectionTitle("Environment"))
        layout.addWidget(
            card_row(
                Card("Python", value=platform.python_version()),
                Card("Qt", value=_qt_version()),
                Card("Cache schema", value=f"v{SCHEMA_VERSION}"),
                Card("Mode", value="Portable" if paths.is_portable() else "Installed"),
            )
        )

        data_dir = QLabel(f"Data directory: {paths.data_root()}")
        data_dir.setObjectName("CardCaption")
        data_dir.setTextInteractionFlags(data_dir.textInteractionFlags().TextSelectableByMouse)
        layout.addWidget(data_dir)

        buttons = QHBoxLayout()
        buttons.addWidget(_folder_button("Open data folder", str(paths.data_root())))
        buttons.addWidget(_folder_button("Open logs", str(paths.logs_dir())))
        buttons.addStretch(1)
        layout.addLayout(buttons)

        layout.addStretch(1)


def _link(text: str, url: str) -> QPushButton:
    button = QPushButton(text)
    button.setToolTip(url)
    button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
    return button


def _folder_button(text: str, path: str) -> QPushButton:
    button = QPushButton(text)
    button.setToolTip(path)
    button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
    return button


def _qt_version() -> str:
    try:
        from PySide6 import __version__

        return str(__version__)
    except ImportError:  # pragma: no cover — PySide6 is a hard dependency
        return sys.version.split()[0]
