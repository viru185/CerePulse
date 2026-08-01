"""The sync panel — what is fresh, what is not, and a way to fix one thing.

Refresh has always been all-or-nothing, and the sidebar reported one timestamp: the current
month's. Leave, swipe requests and the holiday calendar each had their own freshness that
nothing showed, and the holiday calendar had no refresh path from the UI at all — a wrong
one meant waiting a day or clearing the entire cache.

Each row here is one thing, with its own age, its own reason to be stale, and its own
button. The point is not that this gets used often; it is that when something looks wrong
there is somewhere to look and something to press.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cerepulse.services.scopes import Scope, ScopeStatus
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import Palette, Space


class SyncPanel(QDialog):
    """A small window listing every syncable thing and its age."""

    scope_requested = Signal(object)  # Scope
    refresh_all_requested = Signal()

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self.setWindowTitle("Sync")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.SECTION, Space.GAP, Space.SECTION, Space.GAP)
        layout.setSpacing(Space.ROW)

        heading = QLabel("What CerePulse has, and how old it is")
        heading.setStyleSheet("font-size: 15px; font-weight: 700;")
        layout.addWidget(heading)

        self._rows = QVBoxLayout()
        self._rows.setSpacing(Space.SNUG)
        host = QWidget()
        host.setLayout(self._rows)
        layout.addWidget(host)

        self._note = QLabel()
        self._note.setObjectName("CardCaption")
        self._note.setWordWrap(True)
        layout.addWidget(self._note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        everything = QPushButton("Refresh everything")
        everything.setObjectName("Primary")
        everything.clicked.connect(self.refresh_all_requested)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        buttons.addWidget(everything)
        layout.addLayout(buttons)

    def show_statuses(self, statuses: list[ScopeStatus], *, busy: Scope | None = None) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

        for status in statuses:
            row = _ScopeRow(status, self._palette, busy=busy is status.scope)
            row.requested.connect(self.scope_requested)
            self._rows.addWidget(row)

        outstanding = sum(status.pending for status in statuses)
        self._note.setText(
            f"{outstanding} day(s) in this month are still missing their punch log. "
            f"Fetching them costs one portal request each, which is why they arrive "
            f"gradually rather than all at once."
            if outstanding
            else "Everything in this month has its punch detail."
        )


class _ScopeRow(QWidget):
    """One thing: what it is, when it last arrived, and a button to fetch it again."""

    requested = Signal(object)  # Scope

    def __init__(
        self,
        status: ScopeStatus,
        palette: Palette,
        *,
        busy: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.ROW)

        text = QVBoxLayout()
        text.setSpacing(0)
        name = QLabel(status.scope.label)
        name.setStyleSheet("font-weight: 600;")
        detail = QLabel(_age_text(status))
        detail.setObjectName("CardCaption")
        text.addWidget(name)
        text.addWidget(detail)
        layout.addLayout(text, 1)

        # The explanation is the answer to "why is this one behind?", which is the reason
        # anyone opens this panel.
        self.setToolTip(status.scope.explanation)

        button = QPushButton("Fetching…" if busy else "Refresh")
        button.setEnabled(not busy)
        button.setFixedWidth(96)
        button.clicked.connect(lambda: self.requested.emit(status.scope))
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)


def _age_text(status: ScopeStatus) -> str:
    if status.never_synced:
        return "Never fetched"
    age = fmt.relative_time(status.last_synced)
    if status.scope is Scope.DAY_DETAIL and status.pending:
        return f"{age} · {status.pending} day(s) still to fetch"
    return age


__all__ = ["SyncPanel"]
