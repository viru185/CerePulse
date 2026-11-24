"""About — what this build is, where its files are, and how to report a problem.

Three jobs, kept apart because they are asked at different times: who made it, what state
this installation is in, and what to do when something is wrong. The diagnostics section is
the reason the screen exists at all — "it does not work" is unanswerable without the version,
the channel and the mode, and asking a user to find those by hand is asking too much.

Everything in the diagnostics report is safe to paste in public. Nothing there names the
employer, the employee or anything out of the cache.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QVBoxLayout, QWidget

from cerepulse import __about__ as about
from cerepulse.core import paths
from cerepulse.core.diagnostics import Diagnostics, collect, human_size
from cerepulse.intelligence.insights import Severity
from cerepulse.ui import formatting as fmt
from cerepulse.ui import kit
from cerepulse.ui.theme import Space
from cerepulse.ui.widgets import Banner, FlowLayout, card_row
from cerepulse.update import UpdateEvent, rollback_candidates, update_history

#: Update events shown on this page. The store keeps twenty; nobody reads twenty.
HISTORY_SHOWN = 5

HISTORY_COLUMNS = ("Version", "When", "Outcome", "Note")


class AboutView(QWidget):
    """Credits, build state, diagnostics and update history."""

    update_check_requested = Signal()
    rollback_requested = Signal()
    test_connection_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page = kit.ScrollArea(spacing=Space.GAP)
        outer.addWidget(page)
        layout = page.body

        layout.addLayout(self._build_identity())

        self.banner = Banner()
        layout.addWidget(self.banner)

        layout.addWidget(kit.SectionHeader("This build"))
        layout.addWidget(self._build_cards())
        self._paths = kit.Text(role="caption", tone="muted", wrap=True)
        self._paths.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._paths)

        layout.addWidget(kit.SectionHeader("Updates"))
        layout.addWidget(self._build_update_buttons())
        self.history = kit.TableWidget(HISTORY_COLUMNS, fit_rows=True)
        layout.addWidget(self.history)
        self._history_note = kit.Text(role="caption", tone="muted", wrap=True)
        layout.addWidget(self._history_note)

        layout.addWidget(kit.SectionHeader("Diagnostics"))
        layout.addWidget(self._build_diagnostic_buttons())

        layout.addWidget(kit.SectionHeader("Developer"))
        layout.addLayout(self._build_credits())

        layout.addStretch(1)
        self.refresh()

    # --- construction -----------------------------------------------------------------

    def _build_identity(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(2)
        column.addWidget(kit.Text(about.NAME, role="title"))
        column.addWidget(kit.Text(about.TAGLINE, role="subtitle", tone="muted"))
        column.addSpacing(Space.TIGHT)
        column.addWidget(kit.Text(about.SUMMARY, tone="muted", wrap=True))
        return column

    def _build_cards(self) -> QWidget:
        self.version_card = kit.StatCard("Version")
        self.channel_card = kit.StatCard("Channel")
        self.storage_card = kit.StatCard("Local data")
        self.checked_card = kit.StatCard("Last checked")
        return card_row(self.version_card, self.channel_card, self.storage_card, self.checked_card)

    def _build_update_buttons(self) -> QWidget:
        # A flow, so the row wraps rather than pushing the page sideways at 175%.
        row = kit.Box()
        flow = FlowLayout(row, spacing=Space.SNUG)
        flow.addWidget(
            kit.Button(
                "Check for updates", variant="primary", on_click=self.update_check_requested.emit
            )
        )
        flow.addWidget(kit.Button("What's new", on_click=self._show_whats_new))
        flow.addWidget(kit.Button("Full changelog", on_click=self._show_changelog))
        # Never disabled: see :meth:`refresh`.
        self._rollback = kit.Button("Roll back", variant="quiet", on_click=self._request_rollback)
        flow.addWidget(self._rollback)
        return row

    def _build_diagnostic_buttons(self) -> QWidget:
        row = kit.Box()
        flow = FlowLayout(row, spacing=Space.SNUG)
        flow.addWidget(
            kit.Button(
                "Copy diagnostics",
                tooltip="Version, sizes and platform. Nothing identifying.",
                on_click=self._copy_diagnostics,
            )
        )
        flow.addWidget(kit.Button("Test connection", on_click=self.test_connection_requested.emit))
        flow.addWidget(_folder_button("Open data folder", str(paths.data_root())))
        flow.addWidget(
            kit.Button(
                "Open logs",
                tooltip=f"Opens the newest log in {paths.logs_dir()}",
                on_click=open_logs,
            )
        )
        return row

    def _build_credits(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(Space.SNUG)
        column.addWidget(kit.Text(about.AUTHOR, role="subtitle"))

        links = kit.Box()
        flow = FlowLayout(links, spacing=Space.SNUG)
        flow.addWidget(_link("GitHub", about.GITHUB_URL))
        flow.addWidget(_link("LinkedIn", about.LINKEDIN_URL))
        flow.addWidget(_link("Repository", about.REPO_URL))
        flow.addWidget(_link("Report an issue", f"{about.REPO_URL}/issues"))
        column.addWidget(links)

        column.addWidget(
            kit.Text(
                "Built on " + ", ".join(name for name, _url, _why in about.CREDITS),
                role="caption",
                tone="muted",
                wrap=True,
            )
        )

        # The credits the providers ask for. They used to sit in the sidebar under the
        # picture of the day; this is where paperwork belongs.
        column.addWidget(kit.Text("Data from", role="caption", tone="muted"))
        providers = kit.Box()
        flow = FlowLayout(providers, spacing=Space.SNUG)
        for name, url, why in about.PROVIDERS:
            button = _link(name, url)
            button.setToolTip(f"{why} — {url}")
            flow.addWidget(button)
        column.addWidget(providers)
        return column

    # --- rendering --------------------------------------------------------------------

    def refresh(self, *, channel: str = "stable") -> None:
        """Recompute everything. Sizes are measured on demand, not tracked."""
        self._diagnostics = collect(channel=channel)
        self._render_cards(self._diagnostics)
        self._render_history(update_history())

        # The log directory is named here, not only in a tooltip. "Check the logs" is
        # advice nobody can follow without knowing where they are, and the app's own
        # `paths` command prints into os.devnull in the windowed build, so this is the
        # only place the answer is available at all.
        self._paths.setText(
            f"Data directory: {self._diagnostics.data_root}\n"
            f"Logs: {paths.logs_dir()}\n"
            f"Database {human_size(self._diagnostics.database_bytes)}  ·  "
            f"logs {human_size(self._diagnostics.logs_bytes)}  ·  "
            f"staged updates {human_size(self._diagnostics.updates_bytes)}"
        )

        # Never disabled. Whether the previous installer is still on disk is the updater's
        # problem — it downloads the release when cleanup has lost the file — and a greyed
        # button reads as "this does not work", which for three releases it did not.
        candidates = rollback_candidates()
        self._rollback.setToolTip(
            f"Go back to {candidates[0]}"
            if candidates
            else "Go back to the previous release (downloaded if needed)"
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

        # The store is already bounded — `HISTORY_LIMIT` trims it on every write, so it can
        # never grow without end. What was wrong is that all twenty rows were rendered, and
        # twenty rows of "updated successfully" is a lot of page for something read once.
        # Newest first, and the cut says so rather than silently ending the list.
        newest = list(reversed(events))
        shown = newest[:HISTORY_SHOWN]
        self.history.setRowCount(len(shown))
        for row, event in enumerate(shown):
            cells = (
                event.version,
                event.at.strftime("%d %b %Y, %H:%M").lstrip("0"),
                event.outcome.replace("-", " ").capitalize(),
                event.note,
            )
            for column, text in enumerate(cells):
                self.history.setItem(row, column, QTableWidgetItem(text))

        self.history.setVisible(bool(shown))
        if not events:
            self._history_note.setText(
                "No updates recorded yet — this is the first version installed."
            )
        elif len(newest) > len(shown):
            self._history_note.setText(
                f"Showing the {len(shown)} most recent of {len(newest)} recorded updates."
            )
        else:
            self._history_note.setText("")

    # --- actions ----------------------------------------------------------------------

    def _copy_diagnostics(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self._diagnostics.as_text())
        self.banner.show_message(
            "Diagnostics copied. Safe to paste — it names no employer, employee or data.",
            Severity.SUCCESS,
        )

    def _request_rollback(self) -> None:
        self.rollback_requested.emit()

    def _show_whats_new(self) -> None:
        from cerepulse.ui.whats_new import WhatsNewDialog

        WhatsNewDialog(version=about.VERSION, parent=self).exec()

    def _show_changelog(self) -> None:
        """The same dialog, opened straight onto the whole history."""
        from cerepulse.ui.whats_new import WhatsNewDialog

        dialog = WhatsNewDialog(version=about.VERSION, parent=self)
        dialog.show_full_changelog()
        dialog.exec()


def _link(text: str, url: str) -> kit.Button:
    return kit.Button(
        text, variant="quiet", tooltip=url, on_click=lambda: QDesktopServices.openUrl(QUrl(url))
    )


def _folder_button(text: str, path: str) -> kit.Button:
    return kit.Button(
        text, tooltip=path, on_click=lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    )


def newest_log() -> Path | None:
    """The log file currently being written, or the most recent one that was.

    Logs rotate daily, so a folder holds a fortnight of them and the one anybody wants is
    almost always the newest. Opening the folder and leaving the user to sort by date is a
    step that never had a reason to exist.
    """
    try:
        logs = sorted(paths.logs_dir().glob("cerepulse_*.log"))
    except OSError:
        return None
    return logs[-1] if logs else None


def open_logs() -> None:
    """Open the newest log file, falling back to its folder when there is none yet."""
    target = newest_log() or paths.logs_dir()
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
