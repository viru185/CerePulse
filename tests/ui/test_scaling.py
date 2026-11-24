"""Fitting the window and its pages to the room a laptop gives them, at any display scale.

The layout QC (``test_layout_qc.py``) asserts the end result at each scale; these pin the
mechanisms behind it, so a regression names the part that broke: the window's size and
memory, the sidebar that scrolls instead of squeezing, the Settings grid that drops to one
column, a row of buttons that wraps, and a picture drawn at the screen's pixel ratio.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from cerepulse.core import app_state
from cerepulse.core.config import AppConfig
from cerepulse.ui.theme import DARK
from cerepulse.ui.widgets import DailyTile, FlowLayout


@pytest.fixture
def window(qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CEREPULSE_DATA_DIR", str(tmp_path))
    from cerepulse.app import build_app
    from cerepulse.core import paths
    from cerepulse.ui.main_window import MainWindow

    paths.data_root.cache_clear()
    context = build_app(config=AppConfig(), database_path=tmp_path / "test.db")
    main = MainWindow(context)
    main._auto.stop()
    yield main
    main.close()
    context.close()
    paths.data_root.cache_clear()


# --- the window -------------------------------------------------------------------------------


def test_the_minimum_fits_a_maximised_window_at_175_percent(window) -> None:  # type: ignore[no-untyped-def]
    minimum = window.minimumSize()
    assert minimum.width() <= 1097 and minimum.height() <= 537


class _Screen:
    """A 1080p laptop at 125%, less the taskbar — the offscreen screen is 800x800."""

    def availableGeometry(self) -> QRect:  # noqa: N802 — Qt's name
        return QRect(0, 0, 1536, 824)


def test_a_first_launch_opens_inside_the_screen_it_is_on(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(window, "screen", lambda: _Screen())
    room = _Screen().availableGeometry()
    window._fit_to_screen()
    placed = window.geometry()
    assert room.contains(placed), (room, placed)
    assert placed.width() <= room.width() and placed.height() <= room.height()


def test_the_window_reopens_where_it_was_left(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cerepulse.ui.main_window import GEOMETRY_KEY

    window._save_geometry()
    saved = app_state.get(GEOMETRY_KEY)
    assert isinstance(saved, str) and saved

    restored: list[bytes] = []
    monkeypatch.setattr(
        window, "restoreGeometry", lambda data: restored.append(bytes(data.data())) or True
    )
    monkeypatch.setattr(window, "_title_bar_is_on_a_screen", lambda: True)
    monkeypatch.setattr(window, "_fit_to_screen", lambda: restored.append(b"fitted"))
    window._restore_geometry()

    assert restored == [bytes(window.saveGeometry().data())]


def test_a_saved_place_on_a_monitor_that_has_gone_is_not_used(window) -> None:  # type: ignore[no-untyped-def]
    window.setGeometry(QRect(-50_000, -50_000, 1000, 600))
    assert not window._title_bar_is_on_a_screen()


# --- the sidebar ------------------------------------------------------------------------------


def test_the_sidebar_scrolls_rather_than_squeezing(window) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QScrollArea

    scroll = window.findChild(QScrollArea, "SidebarScroll")
    assert scroll is not None
    assert scroll.widget().isAncestorOf(window._daily)
    assert not scroll.widget().isAncestorOf(window._status), "the status stays pinned"
    assert not scroll.widget().isAncestorOf(window._portal), "so does Open SpineHR"


def _picture(tmp_path: Path) -> Path:
    image = QPixmap(1920, 1080)
    image.fill(QColor("#336699"))
    target = tmp_path / "picture.png"
    image.save(str(target))
    return target


def test_the_daily_picture_takes_the_width_it_is_given(qapp, tmp_path) -> None:  # type: ignore[no-untyped-def]
    tile = DailyTile(DARK)
    tile.show_day("Words", "Someone", _picture(tmp_path))
    tile.resize(150, 200)
    tile.show()
    qapp.processEvents()

    frame = tile._frame()
    assert frame.width() == 150 and frame.height() == round(150 * 101 / 180)
    assert tile._image.minimumWidth() <= 1, "a fixed width held the sidebar too wide"


def test_the_daily_picture_is_drawn_at_the_screens_pixel_ratio(qapp) -> None:  # type: ignore[no-untyped-def]
    """Qt converts a label's pixmap to the label's own ratio, which offscreen is 1, so the
    rendering is checked where it happens rather than read back off the label."""
    from cerepulse.ui.widgets import cover_pixmap

    source = QPixmap(1920, 1080)
    source.fill(QColor("#336699"))
    drawn = cover_pixmap(source, QSize(180, 101), 1.75)

    assert drawn.devicePixelRatio() == pytest.approx(1.75)
    assert (drawn.width(), drawn.height()) == (315, 177)
    assert drawn.deviceIndependentSize().toSize() == QSize(180, 101)


# --- Settings ---------------------------------------------------------------------------------


def test_settings_drops_to_one_column_when_two_do_not_fit(qapp) -> None:  # type: ignore[no-untyped-def]
    from cerepulse.ui.views.settings import SettingsView

    view = SettingsView(AppConfig())
    view.show()
    view.resize(1400, 700)
    qapp.processEvents()
    assert view._columns == 2

    view.resize(901, 537)  # a maximised window at 175%, less the sidebar
    qapp.processEvents()
    assert view._columns == 1
    for grid, cards in view._grids:
        for card, *_rest in cards:
            _row, column, _rows, _span = grid.getItemPosition(grid.indexOf(card))
            assert column == 0

    view.resize(1400, 700)
    qapp.processEvents()
    assert view._columns == 2


def test_a_row_of_buttons_wraps_instead_of_widening_the_page(qapp) -> None:  # type: ignore[no-untyped-def]
    host = QWidget()
    flow = FlowLayout(host, spacing=8)
    buttons = [QPushButton(f"Section number {index}") for index in range(8)]
    for button in buttons:
        flow.addWidget(button)
    host.resize(300, flow.heightForWidth(300))
    host.show()
    qapp.processEvents()

    assert host.minimumSizeHint().width() < 300
    assert max(button.geometry().right() for button in buttons) <= 300
    assert len({button.geometry().top() for button in buttons}) > 1, "it wrapped"
