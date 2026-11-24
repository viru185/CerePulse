"""Scrolling pages and tables, made once.

Every screen that can be taller than the window scrolls rather than squeezes — a squeezed
layout draws its parts into each other — and none scrolls sideways. Every table is the same
table: no grid, alternating rows, a header that stays put, rows that fit when it sits inside
a page that already scrolls.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from cerepulse.ui.theme import Space


class Box(QWidget):
    """A container that paints nothing of its own.

    A plain ``QWidget`` paints the page's surface colour — the sheet's ``QWidget`` rule
    matches it — so one used to group labels inside a card drew a page-coloured box on the
    card. Qt paints a sheet background only for plain ``QWidget`` instances, not
    subclasses, and this is one.
    """


class ScrollArea(QScrollArea):
    """A page that scrolls up and down and never sideways. ``body`` is its layout."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        margins: tuple[int, int, int, int] = (Space.PAGE, 28, Space.PAGE, Space.PAGE),
        spacing: int = Space.SECTION,
    ) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.setWidget(self.content)
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(spacing)


class TableWidget(QTableWidget):
    """The one table. ``fit_rows`` shows every row instead of scrolling inside a page."""

    def __init__(
        self,
        columns: Sequence[str],
        parent: QWidget | None = None,
        *,
        stretch_last: bool = True,
        fit_rows: bool = False,
        selectable: bool = False,
    ) -> None:
        super().__init__(0, len(columns), parent)
        self.setHorizontalHeaderLabels(list(columns))
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
            if selectable
            else QAbstractItemView.SelectionMode.NoSelection
        )
        self.setShowGrid(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(stretch_last)
        header.setFixedHeight(34)
        if fit_rows:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setSizeAdjustPolicy(QAbstractItemView.SizeAdjustPolicy.AdjustToContents)
