"""Controllers — the coordination pulled out of MainWindow.

Navigation is the one with real logic in it, so it carries most of these. The rule it
encodes: a sidebar click is a deliberate jump and forgets where you were; a drill-down
remembers, so there is exactly one sensible thing "back" can mean.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QButtonGroup, QPushButton, QStackedWidget, QWidget

from cerepulse.ui.controllers import NavigationController

SCREENS = ("Today", "Week", "Attendance", "Insights")
TODAY, WEEK, ATTENDANCE, INSIGHTS = range(4)


@pytest.fixture
def navigation(qapp: QApplication) -> NavigationController:
    stack = QStackedWidget()
    buttons = QButtonGroup()
    buttons.setExclusive(True)
    for index, name in enumerate(SCREENS):
        stack.addWidget(QWidget())
        button = QPushButton(name)
        button.setCheckable(True)
        button.setChecked(index == 0)
        buttons.addButton(button, index)
    return NavigationController(stack, buttons, SCREENS)


# --- moving about -----------------------------------------------------------------------


def test_selecting_a_screen_shows_it_and_checks_its_button(
    navigation: NavigationController,
) -> None:
    navigation.select(ATTENDANCE)

    assert navigation.current == ATTENDANCE
    assert navigation._buttons.button(ATTENDANCE).isChecked()


def test_an_out_of_range_screen_is_ignored(navigation: NavigationController) -> None:
    navigation.select(99)
    assert navigation.current == TODAY


# --- drilling in and back ----------------------------------------------------------------


def test_a_drill_down_remembers_where_it_came_from(navigation: NavigationController) -> None:
    """Double-clicking a date in Attendance used to be a one-way trip."""
    navigation.select(ATTENDANCE)
    navigation.drill_to(TODAY)

    assert navigation.current == TODAY
    assert navigation.can_go_back
    assert navigation.origin_name == "Attendance"


def test_back_returns_to_the_origin(navigation: NavigationController) -> None:
    navigation.select(ATTENDANCE)
    navigation.drill_to(TODAY)

    assert navigation.back()
    assert navigation.current == ATTENDANCE
    assert not navigation.can_go_back


def test_back_with_nowhere_to_go_says_so(navigation: NavigationController) -> None:
    """The window falls back to "back to today" on False, so this has to be honest."""
    assert not navigation.back()
    assert navigation.current == TODAY


def test_a_deliberate_jump_forgets_the_drill_down(navigation: NavigationController) -> None:
    """Having navigated away on purpose, an offer to go "back" is no longer wanted."""
    navigation.select(ATTENDANCE)
    navigation.drill_to(TODAY)
    navigation.select(INSIGHTS)

    assert not navigation.can_go_back
    assert navigation.origin_name is None


def test_drilling_into_the_screen_you_are_on_records_nothing(
    navigation: NavigationController,
) -> None:
    navigation.select(TODAY)
    navigation.drill_to(TODAY)

    assert not navigation.can_go_back


def test_screen_changed_fires_for_every_move(navigation: NavigationController) -> None:
    seen: list[int] = []
    navigation.screen_changed.connect(seen.append)

    navigation.select(WEEK)
    navigation.drill_to(TODAY)
    navigation.back()

    assert seen == [WEEK, TODAY, WEEK]
