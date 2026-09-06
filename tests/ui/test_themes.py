"""A theme change applies now, to every painted widget, not on the next start."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from cerepulse.core.config import AppConfig


@pytest.fixture
def window(  # type: ignore[no-untyped-def]
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[object]:
    monkeypatch.setenv("CEREPULSE_DATA_DIR", str(tmp_path))
    from cerepulse.app import build_app
    from cerepulse.core import paths
    from cerepulse.ui.main_window import MainWindow

    paths.data_root.cache_clear()
    context = build_app(config=AppConfig(), database_path=tmp_path / "test.db")
    main = MainWindow(context)
    main._auto.stop()
    yield main
    main._auto.stop()
    main.close()
    context.close()
    paths.data_root.cache_clear()


def test_switching_theme_reaches_the_painted_widgets(window) -> None:  # type: ignore[no-untyped-def]
    """Re-styling the sheet alone left the timelines and rings on the old palette beside a
    note saying the theme applies on the next start — half-repainted, and told it did
    nothing. Every widget that keeps a palette is handed the new one."""
    window._apply_theme("sakura", rerender=False)

    assert window._palette.name == "sakura"
    assert window.today._palette.name == "sakura"
    assert window.today._timeline._palette.name == "sakura"
    assert window.attendance.heatmap._palette.name == "sakura"


def test_every_shipped_theme_can_be_applied(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The palette walk, six times over. The application-wide sheet is stubbed: applying it
    restyles every widget the whole test session has created, which is forty seconds of
    Qt doing nothing this test is about."""
    from cerepulse.ui.theme import PALETTES

    monkeypatch.setattr(QApplication.instance(), "setStyleSheet", lambda _sheet: None)
    for name in PALETTES:
        window._apply_theme(name, rerender=False)
        assert window.week._palette.name == name
