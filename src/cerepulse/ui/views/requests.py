"""Requests — filed swipe requests, and the days that look like they need one.

CerePulse is read-only against the HR system, so this screen never files anything. It
identifies the need and deep-links to the portal page where the user can act, which keeps
the app honest while removing the navigation burden.

What the portal's grid carries is the hard limit on this screen. Its columns are For Date,
Mode, In, Out, Remark, Approve Date, Status and Category — there is no request id, no filed
date and no approver name anywhere. So request age, approval turnaround and "who has it"
are not merely unimplemented, they are unavailable, and the screen does not pretend
otherwise. Duplicate detection works because it needs only the date and the mode.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cerepulse.intelligence.attention import Attention, duplicate_requests
from cerepulse.intelligence.insights import Severity
from cerepulse.models.swipe import SwipeRequest, SwipeStatus
from cerepulse.ui import formatting as fmt
from cerepulse.ui.theme import Palette, Space
from cerepulse.ui.widgets import Banner, EmptyState, SectionTitle, data_table

COLUMNS = ("For date", "Mode", "In", "Out", "Status", "Approved", "Remark")

#: Filters, in the order offered. Each reads one request plus the duplicate set.
FILTERS: tuple[tuple[str, Callable[[SwipeRequest, bool], bool]], ...] = (
    ("All requests", lambda request, duplicated: True),
    ("Awaiting approval", lambda request, duplicated: request.status is SwipeStatus.IN_PROCESS),
    ("Approved", lambda request, duplicated: request.status is SwipeStatus.APPROVED),
    ("Rejected", lambda request, duplicated: request.status is SwipeStatus.REJECTED),
    ("Cancelled", lambda request, duplicated: request.status is SwipeStatus.CANCELLED),
    ("Possible duplicates", lambda request, duplicated: duplicated),
)


class RequestsView(QWidget):
    """Swipe requests, plus the short days with no request filed and why."""

    open_portal = Signal()
    refresh_requested = Signal()
    day_selected = Signal(object)  # date

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._requests: list[SwipeRequest] = []
        self._duplicated: set[int] = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.SECTION, 20, Space.SECTION, Space.SECTION)
        layout.setSpacing(Space.ROW + 2)

        header = QHBoxLayout()
        header.setSpacing(Space.SNUG)
        header.addWidget(SectionTitle("Swipe requests"))
        self._filter = QComboBox()
        self._filter.setMinimumWidth(170)
        for label, _predicate in FILTERS:
            self._filter.addItem(label)
        self._filter.currentIndexChanged.connect(lambda _index: self._apply_filter())
        header.addWidget(self._filter)
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

        layout.addWidget(SectionTitle("Days that need one"))
        self._needed = data_table(("Day", "Why"), fit_rows=True, selectable=True)
        self._needed.setMaximumHeight(180)
        self._needed.cellDoubleClicked.connect(self._emit_needed_day)
        layout.addWidget(self._needed)

        self._nothing_needed = EmptyState(
            "Nothing needs a request.",
            "Every short day in the loaded month is either accounted for or already filed.",
        )
        layout.addWidget(self._nothing_needed)

        layout.addWidget(SectionTitle("Filed"))
        self.table = data_table(COLUMNS, selectable=True)
        layout.addWidget(self.table, 1)

        self._flagged: list[Attention] = []

    # --- rendering ------------------------------------------------------------------

    def show_requests(
        self,
        requests: list[SwipeRequest],
        *,
        needing: list[Attention] | None = None,
    ) -> None:
        self._requests = requests
        self._flagged = list(needing or [])

        duplicates = duplicate_requests(requests)
        # Identity, not equality: two duplicate rows are equal by value, and a set of the
        # requests themselves would collapse the very pair being reported.
        self._duplicated = {id(entry) for group in duplicates.values() for entry in group}

        self._render_needed()
        self._render_table()
        self._apply_filter()
        self._render_banner(duplicates)

    def _render_banner(self, duplicates: dict[tuple[date, str], list[SwipeRequest]]) -> None:
        if duplicates:
            days = ", ".join(
                f"{day.strftime('%d %b').lstrip('0')} ({mode})"
                for day, mode in sorted(duplicates)[:4]
            )
            more = f" and {len(duplicates) - 4} more" if len(duplicates) > 4 else ""
            self.banner.show_message(
                f"{len(duplicates)} day(s) carry more than one live request for the same "
                f"punch: {days}{more}. Cancelling the extra keeps the approver's queue honest.",
                Severity.WARNING,
                key="duplicates",
            )
        else:
            self.banner.clear_message("duplicates")

    def _render_needed(self) -> None:
        """The flagged days and the reason each was flagged.

        A count alone — "4 days fell short" — leaves the user to work out which four and
        why, on a different screen. The reason comes from ``intelligence/attention.py``, so
        it is the same sentence the Attendance row tooltip shows.
        """
        self._needed.setRowCount(0)
        self._needed.setVisible(bool(self._flagged))
        self._nothing_needed.setVisible(not self._flagged)

        for row, attention in enumerate(sorted(self._flagged, key=lambda item: item.day)):
            self._needed.insertRow(row)
            label = QTableWidgetItem(attention.day.strftime("%a %d %b").lstrip("0"))
            label.setData(Qt.ItemDataRole.UserRole, attention.day)
            label.setForeground(QColor(self._palette.bad))
            self._needed.setItem(row, 0, label)
            self._needed.setItem(row, 1, QTableWidgetItem(attention.reason))

    def _render_table(self) -> None:
        self.table.setRowCount(len(self._requests))
        for row, request in enumerate(self._requests):
            duplicated = id(request) in self._duplicated
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
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, request.for_date)
                    if duplicated:
                        item.setForeground(QColor(self._palette.rest))
                        item.setToolTip("Another live request is filed for this day and punch.")
                if column == 4:
                    # Tinted as well as coloured: at a glance the column reads as a set of
                    # states rather than as one word someone chose to emphasise.
                    colour = _status_colour(request.status, self._palette)
                    item.setForeground(QColor(colour))
                    item.setBackground(_tint(colour))
                if 2 <= column <= 3:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(row, column, item)

    def _apply_filter(self) -> None:
        label, predicate = FILTERS[max(0, self._filter.currentIndex())]
        shown = 0
        for row, request in enumerate(self._requests):
            keep = predicate(request, id(request) in self._duplicated)
            shown += int(keep)
            self.table.setRowHidden(row, not keep)

        if self._filter.currentIndex() == 0:
            self.banner.clear_message("filter")
        elif not shown:
            self.banner.show_message(f"No requests match “{label}”.", Severity.INFO, key="filter")
        else:
            self.banner.show_message(
                f"Showing {shown} of {len(self._requests)} requests — {label.lower()}.",
                Severity.INFO,
                key="filter",
            )

    def _emit_needed_day(self, row: int, _column: int) -> None:
        item = self._needed.item(row, 0)
        if item is not None and item.data(Qt.ItemDataRole.UserRole):
            self.day_selected.emit(item.data(Qt.ItemDataRole.UserRole))


def open_url(url: str) -> None:
    """Open a portal page in the default browser."""
    QDesktopServices.openUrl(QUrl(url))


def _tint(colour: str) -> QColor:
    value = QColor(colour)
    value.setAlpha(34)
    return value


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
