"""Buttons: one class per kind, so every button in the app looks and behaves the same.

The look lives in the one stylesheet (``QPushButton[variant="…"]`` in
:func:`cerepulse.ui.theme._kit_rules`); these classes only set the variant and the behaviour
every button shares — a pointing cursor, an accessible name, and never being a dialog's
"default" button, so Enter in a field cannot press something by accident.

=============  =============================================================================
variant        use
=============  =============================================================================
``secondary``  the ordinary button (the default)
``primary``    the one main action of a screen or dialog (Open in SpineHR, Save)
``quiet``      a low-key action that should not compete (Copy date, Save as PDF)
``danger``     destructive (Clear cached data, Sign out and forget password)
``segment``    one part of a :class:`Tabs` control
``nav``        a sidebar entry
``step``       a square ‹ › stepper
=============  =============================================================================
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QToolButton,
    QWidget,
)

from cerepulse.ui.kit.icons import toned
from cerepulse.ui.theme import BUTTON_VARIANTS, Palette

#: A stepper's width: square, and wide enough that the glyph is not clipped by padding.
STEP_WIDTH = 34


def repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a property it selects on has changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_variant(button: QAbstractButton, variant: str) -> None:
    """Give any button — one a ``QDialogButtonBox`` made, say — a kit variant."""
    if variant not in BUTTON_VARIANTS:
        raise ValueError(f"unknown button variant {variant!r}")
    button.setProperty("variant", variant)
    repolish(button)


class Button(QPushButton):
    """A push button with a kit ``variant`` (see the module table).

    ``on_click`` saves the connect line every call site used to write; ``small`` makes it
    compact for inline row actions.
    """

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        *,
        variant: str = "secondary",
        tooltip: str = "",
        small: bool = False,
        on_click: Callable[[], object] | None = None,
    ) -> None:
        # A single "&" is a keyboard mnemonic to Qt: "Balances & comp-off" drew as
        # "Balances _comp-off". Every label here is literal text, so it is escaped once, here.
        super().__init__(text.replace("&", "&&"), parent)
        if variant not in BUTTON_VARIANTS:
            raise ValueError(f"unknown button variant {variant!r}")
        self.setProperty("variant", variant)
        if small:
            self.setProperty("small", True)
        if tooltip:
            self.setToolTip(tooltip)
        self.setAccessibleName(text.replace("…", "").strip() or tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoDefault(False)
        if on_click is not None:
            self.clicked.connect(lambda _checked=False: on_click())

    def set_variant(self, variant: str) -> None:
        set_variant(self, variant)


class StepButton(Button):
    """A square ‹ or › stepper.

    The width and the sheet's padding have to be chosen together — set apart, every one of
    these once ended up with no room to draw its glyph in — so both live in the kit.
    """

    def __init__(
        self,
        glyph: str,
        tooltip: str,
        parent: QWidget | None = None,
        *,
        on_click: Callable[[], object] | None = None,
    ) -> None:
        super().__init__(glyph, parent, variant="step", tooltip=tooltip, on_click=on_click)
        self.setAccessibleName(tooltip)
        self.setFixedWidth(STEP_WIDTH)


class SegmentButton(Button):
    """One checkable part of a :class:`Tabs` control."""

    def __init__(self, text: str, parent: QWidget | None = None, *, tooltip: str = "") -> None:
        super().__init__(text, parent, variant="segment", tooltip=tooltip)
        self.setCheckable(True)


class Tabs(QFrame):
    """A few views of one screen, one of them showing: What happened | Balances | Holidays.

    ``changed`` carries the index picked. Exclusive, and never empty — the first is
    selected until something else is.
    """

    changed = Signal(int)

    def __init__(self, labels: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("variant", "tabs")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: list[SegmentButton] = []
        for index, label in enumerate(labels):
            button = SegmentButton(label, self)
            self._group.addButton(button, index)
            layout.addWidget(button)
            self.buttons.append(button)
        if self.buttons:
            self.buttons[0].setChecked(True)
        self._group.idClicked.connect(self.changed)

    def current(self) -> int:
        return max(0, self._group.checkedId())

    def set_current(self, index: int) -> None:
        if 0 <= index < len(self.buttons) and index != self.current():
            self.buttons[index].setChecked(True)
            self.changed.emit(index)


class NavButton(Button):
    """A sidebar entry: an icon in the palette's muted tone, the screen's name, checkable."""

    def __init__(
        self, text: str, icon_name: str, palette: Palette, parent: QWidget | None = None
    ) -> None:
        super().__init__(text, parent, variant="nav")
        self.setCheckable(True)
        self._icon_name = icon_name
        self._palette_value = palette
        self.setIconSize(QSize(20, 20))
        self._redraw()

    @property
    def _palette(self) -> Palette:
        return self._palette_value

    @_palette.setter
    def _palette(self, palette: Palette) -> None:
        self._palette_value = palette
        self._redraw()

    def _redraw(self) -> None:
        self.setIcon(toned(self._icon_name, self._palette_value, "muted"))


class IconButton(QToolButton):
    """A small square button showing only an icon, with its meaning as the tooltip."""

    def __init__(
        self,
        icon_name: str,
        tooltip: str,
        palette: Palette,
        parent: QWidget | None = None,
        *,
        tone: str = "muted",
        size: int = 30,
        on_click: Callable[[], object] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("variant", "icon")
        self._icon_name, self._tone = icon_name, tone
        self._palette_value = palette
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setFixedSize(size, size)
        self.setIconSize(QSize(18, 18))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._redraw()
        if on_click is not None:
            self.clicked.connect(lambda _checked=False: on_click())

    @property
    def _palette(self) -> Palette:
        return self._palette_value

    @_palette.setter
    def _palette(self, palette: Palette) -> None:
        self._palette_value = palette
        self._redraw()

    def _redraw(self) -> None:
        self.setIcon(toned(self._icon_name, self._palette_value, self._tone, size=18))
