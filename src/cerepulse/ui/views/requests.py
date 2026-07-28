"""Requests — filed swipe requests and the days that look like they need one.

CerePulse is read-only against the HR system, so this screen never files anything. It
identifies the need and deep-links to the portal page where the user can act, which keeps
the app honest while removing the navigation burden.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cerepulse.models.swipe import SwipeRequest, SwipeStatus
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import Palette
from cerepulse.ui.widgets import Banner, SectionTitle

COLUMNS = ("For date", "Mode", "In", "Out", "Status", "Approved", "Remark")


class RequestsView(QWidget):
    """Swipe requests, plus a list of short days with no request filed."""

    open_portal = Signal()
    refresh_requested = Signal()

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.addWidget(SectionTitle("Swipe requests"))
        header.addStretch(1)
        portal = QPushButton("Raise in SpineHR")
        portal.setObjectName("Primary")
        portal.clicked.connect(self.open_portal)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_requested)
        header.addWidget(refresh)
        header.addWidget(portal)
        layout.addLayout(header)

        self.banner = Banner()
        layout.addWidget(self.banner)

        self._needed = QLabel()
        self._needed.setObjectName("CardCaption")
        self._needed.setWordWrap(True)
        layout.addWidget(self._needed)

        self.table = self._build_table()
        layout.addWidget(self.table, 1)

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(0, len(COLUMNS))
        table.setHorizontalHeaderLabels(COLUMNS)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return table

    # --- rendering ------------------------------------------------------------------

    def show_requests(self, requests: list[SwipeRequest], *, needing: list[date]) -> None:
        open_count = sum(1 for request in requests if request.is_open)
        if needing:
            days = ", ".join(day.strftime("%d %b").lstrip("0") for day in needing[:8])
            more = f" and {len(needing) - 8} more" if len(needing) > 8 else ""
            self._needed.setText(
                f"{len(needing)} day(s) fell short with no request filed: {days}{more}."
            )
        elif open_count:
            self._needed.setText(f"{open_count} request(s) awaiting approval.")
        else:
            self._needed.setText("No days are currently flagged as needing a request.")

        self.table.setRowCount(len(requests))
        for row, request in enumerate(requests):
            values = (
                request.for_date.strftime("%d %b %Y").lstrip("0"),
                request.direction,
                fmt.clock_time(request.in_time),
                fmt.clock_time(request.out_time),
                _status_text(request.status),
                fmt.day_label(request.approve_date) if request.approve_date else "",
                request.remark,
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 4:
                    item.setForeground(QColor(_status_colour(request.status, self._palette)))
                if 2 <= column <= 3:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(row, column, item)


def open_url(url: str) -> None:
    """Open a portal page in the default browser."""
    QDesktopServices.openUrl(QUrl(url))


def _status_text(status: SwipeStatus) -> str:
    return {
        SwipeStatus.IN_PROCESS: "In process",
        SwipeStatus.APPROVED: "Approved",
        SwipeStatus.REJECTED: "Rejected",
        SwipeStatus.CANCELLED: "Cancelled",
        SwipeStatus.UNKNOWN: "—",
    }[status]


def _status_colour(status: SwipeStatus, palette: Palette) -> str:
    return {
        SwipeStatus.IN_PROCESS: palette.rest,
        SwipeStatus.APPROVED: palette.good,
        SwipeStatus.REJECTED: palette.bad,
        SwipeStatus.CANCELLED: palette.text_faint,
    }.get(status, palette.text_muted)
