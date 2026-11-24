"""Inputs: the fields, made once, with the behaviour every one of them needs.

The one shared behaviour that matters is the wheel. Settings is a long scrolling page full of
spin boxes and dropdowns, and a stock Qt field takes the wheel the moment the pointer passes
over it — so scrolling the page quietly changed the shift span, or the theme, under the
pointer. A kit field ignores the wheel unless it has the keyboard focus, and the page
scrolls instead.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QCalendarWidget,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QLineEdit,
    QSpinBox,
    QTimeEdit,
    QWidget,
)


def _wheel_needs_focus(widget: QWidget) -> None:
    widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


class ComboBox(QComboBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _wheel_needs_focus(self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 — Qt override
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()

    def select_data(self, value: object) -> bool:
        """Select the entry carrying ``value``, matched by equality.

        ``findData`` matches by identity for Python objects, so a tuple stored as user data
        can never be found with an equal tuple built elsewhere — the bug that hid behind
        index 0 for eight releases. Returns whether anything matched.
        """
        for index in range(self.count()):
            if self.itemData(index) == value:
                self.setCurrentIndex(index)
                return True
        return False


class SpinBox(QSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _wheel_needs_focus(self)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 — Qt override
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class DoubleSpinBox(QDoubleSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _wheel_needs_focus(self)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 — Qt override
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class TimeEdit(QTimeEdit):
    def __init__(self, parent: QWidget | None = None, *, display: str = "h:mm AP") -> None:
        super().__init__(parent)
        _wheel_needs_focus(self)
        self.setDisplayFormat(display)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 — Qt override
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class DateEdit(QDateEdit):
    """A date with a calendar popup that fits a week of short day names.

    The popup inherits its width from the field, which is sized for "5 Aug 2026" — so its
    day-name header had nowhere to draw and elided to "T…". Short names and a width that
    fits seven of them is the whole fix; the sheet styles the rest.
    """

    CALENDAR_WIDTH = 268

    def __init__(self, parent: QWidget | None = None, *, display: str = "d MMM yyyy") -> None:
        super().__init__(parent)
        _wheel_needs_focus(self)
        self.setCalendarPopup(True)
        self.setDisplayFormat(display)
        calendar = self.calendarWidget()
        if calendar is not None:
            calendar.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
            calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
            calendar.setGridVisible(False)
            calendar.setMinimumWidth(self.CALENDAR_WIDTH)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 — Qt override
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class LineEdit(QLineEdit):
    def __init__(
        self, text: str = "", parent: QWidget | None = None, *, placeholder: str = ""
    ) -> None:
        super().__init__(text, parent)
        if placeholder:
            self.setPlaceholderText(placeholder)


class CheckBox(QCheckBox):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
