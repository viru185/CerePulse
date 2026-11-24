"""Short notes that an action happened, which fade away by themselves.

Before these, saving Settings changed one line beside the Save button to the same text every
time, a history sync started from Insights reported to a banner on Settings, and "Sync this
day" on Today reported to Attendance. The user asked for the app to say "done", briefly,
wherever they are.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from cerepulse.core.config import AppConfig
from cerepulse.intelligence.insights import Severity
from cerepulse.ui.theme import DARK, PALETTES
from cerepulse.ui.widgets import Toast, ToastStack


@pytest.fixture
def stack(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> Iterator[ToastStack]:
    monkeypatch.setattr(Toast, "FADE_IN", 1)
    monkeypatch.setattr(Toast, "FADE_OUT", 1)
    monkeypatch.setattr(Toast, "HOLD", dict.fromkeys(Severity, 40))
    host = QWidget()
    host.resize(900, 600)
    host.show()
    yield ToastStack(DARK, host)
    host.close()


def test_a_note_appears_in_the_corner(stack: ToastStack) -> None:
    stack.show_message("Settings saved")
    assert stack.messages == ["Settings saved"]
    assert stack.isVisible()
    host = stack.parentWidget()
    assert host is not None
    assert stack.geometry().right() <= host.width()
    assert stack.geometry().bottom() <= host.height()


def test_it_fades_away_by_itself(stack: ToastStack, qtbot) -> None:  # type: ignore[no-untyped-def]
    stack.show_message("Settings saved")
    qtbot.waitUntil(lambda: stack.messages == [], timeout=2000)
    qtbot.waitUntil(lambda: not stack.isVisible(), timeout=2000)


def test_the_same_note_twice_is_one_note_kept_up(stack: ToastStack) -> None:
    stack.show_message("Settings saved")
    stack.show_message("Settings saved")
    assert stack.messages == ["Settings saved"]


def test_no_more_than_three_at_once(stack: ToastStack) -> None:
    for text in ("one", "two", "three", "four"):
        stack.show_message(text)
    assert stack.messages == ["two", "three", "four"]


def test_text_is_shown_as_text_never_as_markup(stack: ToastStack) -> None:
    """Portal text reaches these, and it is not trusted markup."""
    stack.show_message("<b>bold</b> & <a href='x'>link</a>")
    toast = stack.findChildren(Toast)[0]
    from PySide6.QtCore import Qt

    assert toast._label.textFormat() == Qt.TextFormat.PlainText


def test_a_theme_change_reaches_a_toast_already_up(stack: ToastStack) -> None:
    stack.show_message("Settings saved")
    toast = stack.findChildren(Toast)[0]
    toast._palette = PALETTES["light"]  # what `_apply_theme`'s walk does
    assert PALETTES["light"].elevated in toast.styleSheet()


# --- every action answers -----------------------------------------------------------------


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


def test_saving_settings_says_so_every_time(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(window._sync, "load_today", lambda *_a: None)
    monkeypatch.setattr(window, "_refresh_daily", lambda: None)
    window._save_config(window._context.config)
    assert "Settings saved" in window.toasts.messages


def test_a_refresh_someone_pressed_says_when_it_is_done(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(window._sync, "refresh", lambda **_k: None)
    window.refresh(force=True)
    window._on_sync_finished([])
    assert "Everything is up to date" in window.toasts.messages


def test_the_background_tick_says_nothing(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(window._sync, "refresh", lambda **_k: None)
    window.refresh(force=True, quiet=True)
    window._on_sync_finished([])
    assert window.toasts.messages == []


def test_fetching_a_day_reports_where_the_user_is(window) -> None:  # type: ignore[no-untyped-def]
    """ "Sync this day" is pressed on Today and used to report to Attendance's banner."""
    from cerepulse.services.scopes import Scope

    window._on_scope_finished(Scope.DAY_DETAIL, 1)
    assert window.toasts.messages == ["Fetched punch detail for 1 day"]


def test_the_year_download_finishing_is_said_on_any_screen(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cerepulse.ui.controllers.sync import YearBackfill

    for name in ("load_fetchable_months", "reload_month", "refresh_trends"):
        monkeypatch.setattr(window._sync, name, lambda: None)

    run = YearBackfill(periods=[(2026, 11), (2026, 10), (2026, 9)], grids=2, days=41)
    window._year_backfill_ended(run, True)
    assert "This year is downloaded: 3 months, 41 days of punches" in window.toasts.messages
