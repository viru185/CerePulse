"""About — what this build is, where its files are, and how to report a problem.

Three jobs, kept apart because they are asked at different times: who made it, what state
this installation is in, and what to do when something is wrong. The diagnostics section is
the reason the screen exists at all — "it does not work" is unanswerable without the version,
the channel and the mode, and asking a user to find those by hand is asking too much.

Everything in the diagnostics report is safe to paste in public. Nothing there names the
employer, the employee or anything out of the cache.
"""

from __future__ import annotations

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from cerepulse import __about__ as about
from cerepulse.core import paths
from cerepulse.core.diagnostics import Diagnostics, collect, human_size
from cerepulse.intelligence.insights import Severity
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import Space
from cerepulse.ui.widgets import Banner, Card, SectionTitle, card_row, data_table
from cerepulse.update import UpdateEvent, rollback_candidates, update_history

HISTORY_COLUMNS = ("Version", "When", "Outcome", "Note")


class AboutView(QWidget):
    """Credits, build state, diagnostics and update history."""

    update_check_requested = Signal()
    rollback_requested = Signal(str)
    test_connection_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(scroll.horizontalScrollBarPolicy().ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(Space.SECTION, 20, Space.SECTION, Space.SECTION)
        layout.setSpacing(Space.GAP)

        self.banner = Banner()
        layout.addWidget(self.banner)

        layout.addLayout(self._build_identity())
        layout.addWidget(SectionTitle("This build"))
        layout.addWidget(self._build_cards())
        self._paths = QLabel()
        self._paths.setObjectName("CardCaption")
        self._paths.setTextInteractionFlags(
            self._paths.textInteractionFlags().TextSelectableByMouse
        )
        self._paths.setWordWrap(True)
        layout.addWidget(self._paths)

        layout.addWidget(SectionTitle("Updates"))
        layout.addLayout(self._build_update_buttons())
        self.history = data_table(HISTORY_COLUMNS, fit_rows=True)
        layout.addWidget(self.history)
        self._history_note = QLabel()
        self._history_note.setObjectName("CardCaption")
        layout.addWidget(self._history_note)

        layout.addWidget(SectionTitle("Diagnostics"))
        layout.addLayout(self._build_diagnostic_buttons())

        layout.addWidget(SectionTitle("Developer"))
        layout.addLayout(self._build_credits())

        layout.addStretch(1)
        self.refresh()

    # --- construction -----------------------------------------------------------------

    def _build_identity(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(2)

        title = QLabel(about.NAME)
        title.setStyleSheet("font-size: 30px; font-weight: 700;")
        tagline = QLabel(about.TAGLINE)
        tagline.setObjectName("HeroCaption")
        summary = QLabel(about.SUMMARY)
        summary.setWordWrap(True)
        summary.setObjectName("CardCaption")

        column.addWidget(title)
        column.addWidget(tagline)
        column.addWidget(summary)
        return column

    def _build_cards(self) -> QWidget:
        self.version_card = Card("Version")
        self.channel_card = Card("Channel")
        self.storage_card = Card("Local data")
        self.checked_card = Card("Last checked")
        return card_row(self.version_card, self.channel_card, self.storage_card, self.checked_card)

    def _build_update_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(Space.SNUG)

        check = QPushButton("Check for updates")
        check.setObjectName("Primary")
        check.clicked.connect(self.update_check_requested)
        row.addWidget(check)

        whats_new = QPushButton("What's new")
        whats_new.clicked.connect(self._show_whats_new)
        row.addWidget(whats_new)

        # Only offered when there is genuinely something to go back to — a button that
        # explains it cannot do anything is worse than no button.
        self._rollback = QPushButton("Roll back")
        self._rollback.clicked.connect(self._request_rollback)
        row.addWidget(self._rollback)

        row.addStretch(1)
        return row

    def _build_diagnostic_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(Space.SNUG)

        copy = QPushButton("Copy diagnostics")
        copy.setToolTip("Version, sizes and platform. Nothing identifying.")
        copy.clicked.connect(self._copy_diagnostics)
        row.addWidget(copy)

        test = QPushButton("Test connection")
        test.clicked.connect(self.test_connection_requested)
        row.addWidget(test)

        row.addWidget(_folder_button("Open data folder", str(paths.data_root())))
        row.addWidget(_folder_button("Open logs", str(paths.logs_dir())))
        row.addStretch(1)
        return row

    def _build_credits(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(Space.SNUG)

        developer = QLabel(about.AUTHOR)
        developer.setStyleSheet("font-size: 15px; font-weight: 600;")
        column.addWidget(developer)

        links = QHBoxLayout()
        links.setSpacing(Space.SNUG)
        links.addWidget(_link("GitHub", about.GITHUB_URL))
        links.addWidget(_link("LinkedIn", about.LINKEDIN_URL))
        links.addWidget(_link("Repository", about.REPO_URL))
        links.addWidget(_link("Report an issue", f"{about.REPO_URL}/issues"))
        links.addStretch(1)
        column.addLayout(links)

        built_on = QLabel("Built on " + ", ".join(name for name, _url, _why in about.CREDITS))
        built_on.setObjectName("CardCaption")
        column.addWidget(built_on)
        return column

    # --- rendering --------------------------------------------------------------------

    def refresh(self, *, channel: str = "stable") -> None:
        """Recompute everything. Sizes are measured on demand, not tracked."""
        self._diagnostics = collect(channel=channel)
        self._render_cards(self._diagnostics)
        self._render_history(update_history())

        self._paths.setText(
            f"Data directory: {self._diagnostics.data_root}\n"
            f"Database {human_size(self._diagnostics.database_bytes)}  ·  "
            f"logs {human_size(self._diagnostics.logs_bytes)}  ·  "
            f"staged updates {human_size(self._diagnostics.updates_bytes)}"
        )

        candidates = rollback_candidates()
        self._rollback.setEnabled(bool(candidates))
        self._rollback.setToolTip(
            f"Reinstall {candidates[0]}" if candidates else "No earlier version is kept locally"
        )

    def _render_cards(self, diagnostics: Diagnostics) -> None:
        self.version_card.set_value(diagnostics.version)
        self.version_card.set_caption(diagnostics.mode.lower())
        self.channel_card.set_value(diagnostics.channel.capitalize())
        self.channel_card.set_caption("change in Settings")
        self.storage_card.set_value(human_size(diagnostics.cache_bytes))
        self.storage_card.set_caption("cache and database")
        self.checked_card.set_value(
            fmt.relative_time(diagnostics.last_checked) if diagnostics.last_checked else "never"
        )
        self.checked_card.set_caption("for updates")

    def _render_history(self, events: list[UpdateEvent]) -> None:
        from PySide6.QtWidgets import QTableWidgetItem

        self.history.setRowCount(len(events))
        for row, event in enumerate(events):
            cells = (
                event.version,
                event.at.strftime("%d %b %Y, %H:%M").lstrip("0"),
                event.outcome.replace("-", " ").capitalize(),
                event.note,
            )
            for column, text in enumerate(cells):
                self.history.setItem(row, column, QTableWidgetItem(text))

        self.history.setVisible(bool(events))
        self._history_note.setText(
            "" if events else "No updates recorded yet — this is the first version installed."
        )

    # --- actions ----------------------------------------------------------------------

    def _copy_diagnostics(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self._diagnostics.as_text())
        self.banner.show_message(
            "Diagnostics copied. Safe to paste — it names no employer, employee or data.",
            Severity.SUCCESS,
        )

    def _request_rollback(self) -> None:
        candidates = rollback_candidates()
        if candidates:
            self.rollback_requested.emit(candidates[0])

    def _show_whats_new(self) -> None:
        from cerepulse.ui.whats_new import WhatsNewDialog

        WhatsNewDialog(version=about.VERSION, parent=self).exec()


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
