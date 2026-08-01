"""Which screen is showing, and how to get back to the last one.

Drilling into a day from Attendance switches to Today. Until now that was a one-way trip:
the stack index changed, the sidebar button changed, and nothing recorded where the user had
come from — so the only way back to the month they were reading was to find it again by
hand.

The history here is deliberately shallow. A sidebar click is a *deliberate* jump and clears
it; only a drill-down pushes. So "back" always means the one thing a user could reasonably
expect it to mean, and there is never a stack of half-remembered screens to unwind.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QButtonGroup, QStackedWidget


class NavigationController(QObject):
    """Owns the screen stack, the sidebar selection, and a one-deep drill-down history."""

    screen_changed = Signal(int)

    def __init__(
        self,
        stack: QStackedWidget,
        buttons: QButtonGroup,
        names: tuple[str, ...],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._stack = stack
        self._buttons = buttons
        self._names = names
        self._history: list[int] = []

    # --- state ------------------------------------------------------------------------

    @property
    def current(self) -> int:
        return self._stack.currentIndex()

    @property
    def can_go_back(self) -> bool:
        return bool(self._history)

    @property
    def origin_name(self) -> str | None:
        """The screen ``back()`` would return to, for labelling the button honestly."""
        if not self._history:
            return None
        index = self._history[-1]
        return self._names[index] if 0 <= index < len(self._names) else None

    # --- movement ---------------------------------------------------------------------

    def select(self, index: int) -> None:
        """Jump to a screen deliberately, as a sidebar click does.

        Clears the history: having navigated away on purpose, an offer to go "back" to
        wherever a drill-down started is no longer something the user is thinking about.
        """
        self._history.clear()
        self._show(index)

    def drill_to(self, index: int) -> None:
        """Follow a link into another screen, remembering the one being left."""
        current = self.current
        if current != index:
            self._history.append(current)
        self._show(index)

    def back(self) -> bool:
        """Return to the screen a drill-down came from. False when there is nowhere to go."""
        if not self._history:
            return False
        self._show(self._history.pop())
        return True

    def _show(self, index: int) -> None:
        if not 0 <= index < self._stack.count():
            return
        self._stack.setCurrentIndex(index)
        button = self._buttons.button(index)
        if button is not None:
            button.setChecked(True)
        self.screen_changed.emit(index)


__all__ = ["NavigationController"]
