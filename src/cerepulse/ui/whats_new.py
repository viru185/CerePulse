"""What's New — release notes, shown once after an update and on demand from About.

Notes come from the GitHub release body, which is Markdown. Rather than pull in a Markdown
library for a handful of headings and bullets, a small converter handles the subset that
release notes actually use. Anything it does not recognise passes through as escaped text,
so a release note can never inject markup into the dialog.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
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
        # Falls back to the bundled changelog. Both call sites open this without a Release,
        # so until now it always rendered "No release notes were published" — the dialog
        # shown after every single update said nothing at all.
        notes.setHtml(render_notes(release.notes if release else changelog_section(shown_version)))
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
    """Offers an available update, and installs it once the user says so.

    The install button replaces the download link the app used to offer. Handing someone a
    browser download and leaving them to find it, close the running copy and run it
    themselves was three steps of work for something the app can do in one.
    """

    install_requested = Signal()
    download_requested = Signal()

    def __init__(
        self,
        release: Release,
        *,
        downloaded: bool = False,
        can_install: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._release = release
        self._can_install = can_install
        self.setWindowTitle(f"Update available — {about.NAME}")
        self.setModal(True)
        self.resize(560, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        heading = QLabel(f"{release.display_name} is available")
        heading.setStyleSheet("font-size: 19px; font-weight: 700;")
        layout.addWidget(heading)

        channel = " · beta" if release.prerelease else ""
        current = QLabel(f"You are running {about.VERSION}{channel}.")
        current.setObjectName("CardCaption")
        layout.addWidget(current)

        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setHtml(render_notes(release.notes))
        layout.addWidget(notes, 1)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setTextVisible(True)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._status = QLabel()
        self._status.setObjectName("CardCaption")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        buttons = QHBoxLayout()
        later = QPushButton("Later")
        later.clicked.connect(self.reject)
        buttons.addWidget(later)

        site = QPushButton("View on GitHub")
        site.clicked.connect(lambda: _open(release.url))
        buttons.addWidget(site)
        buttons.addStretch(1)

        self._action = QPushButton()
        self._action.setObjectName("Primary")
        self._action.setDefault(True)
        self._action.clicked.connect(self._act)
        buttons.addWidget(self._action)
        layout.addLayout(buttons)

        self._ready = downloaded
        self._refresh()

    # --- state ------------------------------------------------------------------------

    def set_progress(self, fraction: float) -> None:
        self._progress.setVisible(True)
        self._progress.setValue(int(fraction * 100))
        self._status.setText("Downloading in the background. You can keep working.")

    def set_ready(self, _release: object = None) -> None:
        self._ready = True
        self._progress.setVisible(False)
        self._refresh()

    def _refresh(self) -> None:
        if not self._can_install:
            # A source run or a portable copy has no installer to hand over to, and
            # overwriting a portable copy where the user put it would be presumptuous.
            self._action.setText("Download")
            self._status.setText(
                "This copy is portable or run from source, so it cannot update itself. "
                "The download opens in your browser."
            )
            return

        self._action.setText("Install and restart" if self._ready else "Download now")
        self._status.setText(
            "Downloaded and verified. CerePulse will close, update, and reopen."
            if self._ready
            else "Not downloaded yet."
        )

    def _act(self) -> None:
        if not self._can_install:
            _open(self._release.installer_url or self._release.url)
            self.accept()
            return
        if self._ready:
            self.install_requested.emit()
            self.accept()
            return
        self.download_requested.emit()
        self._status.setText("Starting download…")


def changelog_section(version: str) -> str:
    """The bundled changelog's entry for one version, or an empty string.

    Shipped with the app so the post-update dialog works offline and without a round trip
    to GitHub — it runs four hundred milliseconds after launch, which is no time to be
    waiting on a network call.

    A pre-release falls back to its base version: ``0.9.0-beta.1`` looks for ``0.9.0`` when
    it finds nothing of its own, because a beta is usually cut before the release section
    it belongs to has a heading. The fallback says so rather than passing the release's
    notes off as the beta's — showing the wrong version's changes silently is worse than
    showing none.
    """
    text = _bundled_changelog()
    if not text:
        return ""

    wanted = version.lstrip("vV")
    found = _section(text, wanted)
    if found:
        return found

    base = wanted.split("-", 1)[0]
    if base == wanted:
        return ""
    fallback = _section(text, base)
    return f"_Notes for {base}; this build is {wanted}._\n\n{fallback}" if fallback else ""


def _section(text: str, version: str) -> str:
    wanted = f"## [{version}]"
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith(wanted):
            continue
        body: list[str] = []
        for following in lines[index + 1 :]:
            if following.startswith("## ["):
                break
            body.append(following)
        return "\n".join(body).strip()
    return ""


def _bundled_changelog() -> str:
    """CHANGELOG.md, from the source tree or from inside the frozen build."""
    candidates = [Path(__file__).resolve().parents[3] / "CHANGELOG.md"]
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates.insert(0, Path(base) / "CHANGELOG.md")

    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        except OSError:
            continue
    return ""


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
