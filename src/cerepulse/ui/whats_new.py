"""What's New — release notes, shown once after an update and on demand from About.

Notes come from the GitHub release body, which is Markdown. Rather than pull in a Markdown
library for a handful of headings and bullets, a small converter handles the subset that
release notes actually use. Anything it does not recognise passes through as escaped text,
so a release note can never inject markup into the dialog.
"""

from __future__ import annotations

import html
import re

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from cerepulse import __about__ as about
from cerepulse.update.checker import Release

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


class WhatsNewDialog(QDialog):
    """Shows one release's notes."""

    def __init__(
        self,
        release: Release | None = None,
        *,
        version: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        shown_version = version or (release.version if release else about.VERSION)

        self.setWindowTitle(f"What's new in {about.NAME}")
        self.setModal(True)
        self.resize(560, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        heading = QLabel(release.display_name if release else f"Version {shown_version}")
        heading.setStyleSheet("font-size: 19px; font-weight: 700;")
        layout.addWidget(heading)

        if release is not None and release.published_at is not None:
            published = QLabel(release.published_at.strftime("Released %d %B %Y"))
            published.setObjectName("CardCaption")
            layout.addWidget(published)

        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setHtml(render_notes(release.notes if release else ""))
        layout.addWidget(notes, 1)

        buttons = QHBoxLayout()
        if release is not None:
            releases = QPushButton("View on GitHub")
            releases.clicked.connect(lambda: _open(release.url))
            buttons.addWidget(releases)
        buttons.addStretch(1)

        close = QPushButton("Close")
        close.setObjectName("Primary")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)


class UpdateAvailableDialog(QDialog):
    """Offers an available update. The app never installs anything on its own."""

    def __init__(self, release: Release, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Update available — {about.NAME}")
        self.setModal(True)
        self.resize(560, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        heading = QLabel(f"{release.display_name} is available")
        heading.setStyleSheet("font-size: 19px; font-weight: 700;")
        layout.addWidget(heading)

        current = QLabel(f"You are running {about.VERSION}.")
        current.setObjectName("CardCaption")
        layout.addWidget(current)

        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setHtml(render_notes(release.notes))
        layout.addWidget(notes, 1)

        buttons = QHBoxLayout()
        later = QPushButton("Later")
        later.clicked.connect(self.reject)
        buttons.addWidget(later)
        buttons.addStretch(1)

        def download_and_close() -> None:
            _open(release.installer_url or release.url)
            self.accept()

        download = QPushButton("Download")
        download.setObjectName("Primary")
        download.setDefault(True)
        download.clicked.connect(download_and_close)
        buttons.addWidget(download)
        layout.addLayout(buttons)


def render_notes(markdown: str) -> str:
    """Convert the Markdown subset release notes use into HTML.

    Everything is escaped first, so notes are rendered as text rather than trusted markup.
    """
    if not markdown.strip():
        return "<p>No release notes were published for this version.</p>"

    parts: list[str] = []
    in_list = False

    for raw in markdown.splitlines():
        line = raw.rstrip()

        heading = _HEADING.match(line)
        if heading:
            if in_list:
                parts.append("</ul>")
                in_list = False
            level = min(len(heading.group(1)) + 2, 6)
            parts.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue

        bullet = _BULLET.match(line)
        if bullet:
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{_inline(bullet.group(1))}</li>")
            continue

        if in_list:
            parts.append("</ul>")
            in_list = False
        if line.strip():
            parts.append(f"<p>{_inline(line)}</p>")

    if in_list:
        parts.append("</ul>")
    return "\n".join(parts)


def _inline(text: str) -> str:
    """Escape, then re-introduce only the inline markup we support."""
    escaped = html.escape(text)
    escaped = _LINK.sub(r'<a href="\2">\1</a>', escaped)
    escaped = _BOLD.sub(r"<b>\1</b>", escaped)
    return _CODE.sub(r"<code>\1</code>", escaped)


def _open(url: str) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    if url:
        QDesktopServices.openUrl(QUrl(url))
