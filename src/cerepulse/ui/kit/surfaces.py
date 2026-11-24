"""Surfaces: the card a figure sits on, a row in a list, the heading over a section.

Three shapes cover nearly every screen. A :class:`StatCard` is a value and what it is
measured against; a :class:`ListRow` is an icon, what happened, why, and its state; a
:class:`SectionHeader` is a title with its context and its actions on one line. Each is one
class, so every card, row and heading in the app is the same distance from its neighbours.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from cerepulse.ui.kit.icons import IconLabel
from cerepulse.ui.kit.text import Pill, Text
from cerepulse.ui.theme import Palette, Space


class Surface(QFrame):
    """A raised area: ``card`` (8 px corners) or ``panel`` (12 px, for larger groups)."""

    def __init__(self, parent: QWidget | None = None, *, variant: str = "card") -> None:
        super().__init__(parent)
        if variant not in ("card", "panel", "row"):
            raise ValueError(f"unknown surface variant {variant!r}")
        self.setProperty("variant", variant)


class Divider(QFrame):
    """A one-pixel rule between rows."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("variant", "divider")


class StatCard(Surface):
    """A figure, what it is, and what it is measured against.

    The grammar every card follows: the title says what it is, the value is what has been
    done, the caption names the target and the verdict. ``clicked`` is for the cards whose
    figure can explain itself.
    """

    clicked = Signal()

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        value: str = "—",
        caption: str = "",
        tone: str | None = None,
        clickable: bool = False,
        large: bool = False,
    ) -> None:
        super().__init__(parent, variant="card")
        self._clickable = clickable
        if clickable:
            self.setProperty("clickable", True)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.GAP, 14, Space.GAP, 14)
        layout.setSpacing(2)
        # Parented at construction: a label shown before it has a parent is a top-level
        # window for that moment, and five of them once flashed open at every launch.
        self.title = Text(title, self, role="caption", tone="muted")
        self.value = Text(value, self, role="large-title" if large else "title", tone=tone)
        self.caption = Text(caption, self, role="caption", tone="muted", wrap=True)
        self.caption.setVisible(bool(caption))
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.caption)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def set_value(self, value: str, *, tone: str | None = None) -> None:
        self.value.setText(value)
        self.value.set_tone(tone)

    def set_caption(self, caption: str) -> None:
        self.caption.setText(caption)
        self.caption.setVisible(bool(caption))

    def add_detail(self, widget: QWidget) -> None:
        layout = self.layout()
        if layout is not None:
            layout.addWidget(widget)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt override
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ListRow(Surface):
    """One thing in a list: icon, title, why, a line in its own voice, then its state.

    ``kind`` is the quiet word before the pill ("Comp-off", "Swipe"); ``state`` the pill.
    Either can be empty. Clickable rows say so on hover.
    """

    clicked = Signal()

    def __init__(
        self,
        palette: Palette,
        *,
        icon: str,
        icon_tone: str = "muted",
        title: str,
        detail: str = "",
        aside: str = "",
        aside_tone: str = "muted",
        kind: str = "",
        state: str = "",
        state_tone: str = "muted",
        tooltip: str = "",
        clickable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, variant="row")
        self._clickable = clickable
        if clickable:
            self.setProperty("clickable", True)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.SNUG, 6, Space.SNUG, 6)
        layout.setSpacing(Space.ROW)

        self.icon = IconLabel(icon, palette, tone=icon_tone, parent=self)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)

        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(0)
        self.title = Text(title, self, role="body-strong", wrap=True)
        self.detail = Text(detail, self, role="caption", tone="muted", wrap=True)
        self.detail.setVisible(bool(detail))
        self.aside = Text(aside, self, role="caption", tone=aside_tone, wrap=True)
        self.aside.setVisible(bool(aside))
        words.addWidget(self.title)
        words.addWidget(self.detail)
        words.addWidget(self.aside)
        layout.addLayout(words, 1)

        self.kind = Text(kind, self, role="caption", tone="faint")
        self.kind.setVisible(bool(kind))
        layout.addWidget(self.kind, 0, Qt.AlignmentFlag.AlignTop)
        self.state = Pill(state, self, tone=state_tone)
        self.state.setVisible(bool(state))
        layout.addWidget(self.state, 0, Qt.AlignmentFlag.AlignTop)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt override
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class SectionHeader(QWidget):
    """A section's title, its context in a word or two, and its actions on the right."""

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        meta: str = "",
        role: str = "subtitle",
    ) -> None:
        super().__init__(parent)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(Space.ROW)
        self.title = Text(title, self, role=role)
        self.meta = Text(meta, self, role="caption", tone="muted")
        self.meta.setVisible(bool(meta))
        self._row.addWidget(self.title, 0, Qt.AlignmentFlag.AlignBaseline)
        self._row.addWidget(self.meta, 0, Qt.AlignmentFlag.AlignBaseline)
        self._row.addStretch(1)

    def set_meta(self, meta: str) -> None:
        self.meta.setText(meta)
        self.meta.setVisible(bool(meta))

    def add_action(self, widget: QWidget) -> None:
        self._row.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
