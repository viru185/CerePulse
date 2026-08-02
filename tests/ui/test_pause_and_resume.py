"""Standing down, and — the half that was missing — getting back.

The truce shipped in 0.9 and was armed in 0.10, so the app correctly pauses when SpineHR is
signed in elsewhere. Coming back did not work, and the unit tests could not see it: the auth
layer was right, `refresh()` was right, and the bug lived in the fact that nothing carried
"the user asked for this" from one to the other. These drive the window itself for that
reason.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from cerepulse.core.config import AppConfig
from cerepulse.core.errors import SessionTakenError, TransportError


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
    # Nothing here should reach the portal; the sync controller is stubbed per test.
    main._auto.stop()
    yield main
    main._auto.stop()
    main.close()
    context.close()
    paths.data_root.cache_clear()


def _silence_sync(main, monkeypatch: pytest.MonkeyPatch) -> list[str]:  # type: ignore[no-untyped-def]
    """Record what the window asked the sync controller to do, without doing any of it."""
    asked: list[str] = []
    for name in ("refresh", "refresh_leave", "refresh_trends"):
        monkeypatch.setattr(
            main._sync, name, lambda *a, _n=name, **k: asked.append(_n), raising=True
        )
    return asked


# --- standing down --------------------------------------------------------------------


def test_a_taken_session_pauses_the_app(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _silence_sync(window, monkeypatch)
    window._on_error(SessionTakenError("signed in elsewhere"))

    assert window._paused
    assert not window._auto.isActive(), "a paused app must not keep polling"


def test_the_pause_is_explained_on_every_screen_it_could_have_come_from(  # type: ignore[no-untyped-def]
    window, monkeypatch
) -> None:
    """It is triggered from Records and Attendance more often than Today, and writing the
    explanation only to Today put it where the user was demonstrably not looking."""
    _silence_sync(window, monkeypatch)
    window._on_error(SessionTakenError("signed in elsewhere"))

    for banner in (window.today.banner, window.records.banner, window.attendance.banner):
        assert banner.isVisibleTo(banner.parentWidget())
        assert "signed in somewhere else" in banner.toolTip()


def test_an_ordinary_failure_does_not_pause(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _silence_sync(window, monkeypatch)
    window._on_error(TransportError("the portal is down"))

    assert not window._paused


# --- and getting back -----------------------------------------------------------------


def test_refresh_authorises_one_sign_in(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The bug. Clearing the flag was never enough: the next request bounces to the login
    page, the auth layer reads that as a *second* eviction because the app has been idle
    only on account of being paused, and the app pauses again before the user can read the
    banner clearing."""
    asked = _silence_sync(window, monkeypatch)
    window._on_error(SessionTakenError("signed in elsewhere"))

    window.refresh(force=True)

    assert not window._paused
    assert window._context.auth._reclaiming, "the next expiry must be allowed to sign back in"
    assert asked == ["refresh"]
    assert window._auto.isActive(), "the background timer comes back with the session"


def test_refresh_clears_the_banner_everywhere_it_was_shown(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _silence_sync(window, monkeypatch)
    window._on_error(SessionTakenError("signed in elsewhere"))
    window.refresh(force=True)

    for banner in (window.today.banner, window.records.banner, window.attendance.banner):
        assert not banner.isVisibleTo(banner.parentWidget())


def test_records_refresh_can_end_a_pause(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Records is the screen the portal is opened from, so its Refresh is the one the user
    reaches for — and it used to call the service directly, resuming nothing."""
    asked = _silence_sync(window, monkeypatch)
    window._on_error(SessionTakenError("signed in elsewhere"))

    window.records.refresh_requested.emit()

    assert not window._paused
    assert window._context.auth._reclaiming
    assert asked == ["refresh_leave"]


def test_insights_refresh_can_end_a_pause(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Insights reads the cache rather than fetching, but Refresh has to mean the same
    thing on every screen or the button stops feeling reliable."""
    asked = _silence_sync(window, monkeypatch)
    window._on_error(SessionTakenError("signed in elsewhere"))

    window.insights.refresh_requested.emit()

    assert not window._paused
    assert "refresh_trends" in asked


def test_a_background_tick_does_not_take_the_session_back(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The whole point of the truce. A timer is not the user asking."""
    asked = _silence_sync(window, monkeypatch)
    window._on_error(SessionTakenError("signed in elsewhere"))

    window.refresh(force=True, quiet=True)

    assert window._paused
    assert not window._context.auth._reclaiming
    assert asked == []


def test_a_quiet_call_while_paused_says_so_rather_than_going_silent(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Changing month and opening a day both come through the quiet path. Returning with
    nothing said made those look broken rather than paused."""
    _silence_sync(window, monkeypatch)
    window._on_error(SessionTakenError("signed in elsewhere"))
    window._set_status("Refreshing…")

    window.refresh(force=True, quiet=True)

    assert "Paused" in window._status_text
