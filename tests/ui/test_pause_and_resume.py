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
    """Refresh means the same thing on every screen — ask the portal, then re-read — or the
    button stops feeling reliable. Insights used to rebuild from the cache and fetch nothing,
    on the one screen whose numbers most obviously depend on how much history is cached."""
    asked = _silence_sync(window, monkeypatch)
    window._on_error(SessionTakenError("signed in elsewhere"))

    window.insights.refresh_requested.emit()

    assert not window._paused
    assert "refresh" in asked


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


# --- every fetch button ends a pause; nothing quiet does ---------------------------------
#
# 23 Nov: the browser had the session, the user pressed Pay's Refresh, and Pay went straight
# to its service. Three requests, no session, three "signed in somewhere else", and the only
# way out was a restart. Every control that fetches now asks for the session back first.


def test_pay_refresh_ends_a_pause(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from dataclasses import replace

    _silence_sync(window, monkeypatch)
    config = window._context.config
    window._context.pay.use_config(replace(config, pay=replace(config.pay, enabled=True)))
    submitted: list[str] = []
    monkeypatch.setattr(window._runner, "submit", lambda name, *a, **k: submitted.append(name))
    window._on_error(SessionTakenError("signed in elsewhere"))

    window.pay.refresh_requested.emit()

    assert not window._paused
    assert window._context.auth._reclaiming
    assert submitted == ["pay"]


@pytest.mark.parametrize("control", ["fetch detail", "sync panel", "history"])
def test_every_fetch_button_ends_a_pause(window, monkeypatch, control) -> None:  # type: ignore[no-untyped-def]
    from cerepulse.services.scopes import Scope

    _silence_sync(window, monkeypatch)
    for name in ("sync_scope", "sync_history"):
        monkeypatch.setattr(window._sync, name, lambda *a, **k: None)
    window._on_error(SessionTakenError("signed in elsewhere"))

    {
        "fetch detail": window.attendance.fetch_detail_requested.emit,
        "sync panel": lambda: window._sync_scope(Scope.LEAVE),
        "history": window.sync_history,
    }[control]()

    assert not window._paused
    assert window._context.auth._reclaiming


def test_stepping_to_an_unsynced_month_while_paused_fetches_nothing(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _silence_sync(window, monkeypatch)
    fetched: list[object] = []
    monkeypatch.setattr(window._sync, "sync_scope", lambda scope: fetched.append(scope))
    monkeypatch.setattr(window._sync, "load_from_cache", lambda: None)
    window._on_error(SessionTakenError("signed in elsewhere"))

    window._change_month(2026, 2)

    assert fetched == []
    assert window._paused


def test_the_controller_is_told_about_the_pause(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """It decides whether a stale month or a due day may be fetched, so it has to know."""
    _silence_sync(window, monkeypatch)
    window._on_error(SessionTakenError("signed in elsewhere"))
    assert window._sync.paused
    window.refresh(force=True)
    assert not window._sync.paused


# --- after a blip, the screens say so no longer ------------------------------------------


def test_a_clean_sync_clears_the_problems_earlier_fetches_reported(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """ "Could not load day: Network unavailable" stayed on three screens after the network
    came back, until the app was restarted — most of why it looked as if it needed one."""
    window._on_degraded("day", TransportError("offline"))
    for banner in (window.today.banner, window.week.banner, window.attendance.banner):
        assert banner.isVisibleTo(banner.parentWidget())

    window._on_sync_finished([])

    for banner in (window.today.banner, window.week.banner, window.attendance.banner):
        assert not banner.isVisibleTo(banner.parentWidget())


def test_a_clean_sync_leaves_the_session_notice_alone(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _silence_sync(window, monkeypatch)
    window._on_error(SessionTakenError("signed in elsewhere"))
    window._on_sync_finished([])
    assert window.today.banner.isVisibleTo(window.today.banner.parentWidget())


# --- prompts -------------------------------------------------------------------------------


def test_only_one_sign_in_dialog_at_a_time(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`exec` runs a nested loop, so each queued task failing on the same lost session
    opened another dialog over the first."""
    opened: list[int] = []

    def open_one() -> None:
        opened.append(1)
        window.prompt_sign_in()  # a second failure arriving while the first dialog is up

    monkeypatch.setattr(window, "_prompt_sign_in", open_one)
    window.prompt_sign_in()

    assert opened == [1]
    assert not window._signing_in


def test_being_offline_at_launch_asks_for_no_password(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cerepulse.core.errors import AuthenticationError, NetworkUnreachableError

    prompted: list[int] = []
    monkeypatch.setattr(window, "prompt_sign_in", lambda: prompted.append(1))

    window._silent_sign_in_failed(NetworkUnreachableError("getaddrinfo failed"))
    assert prompted == []
    assert "Offline" in window._status_text

    window._silent_sign_in_failed(AuthenticationError("rejected"))
    assert prompted == [1]


# --- waking up -----------------------------------------------------------------------------


def test_waking_from_sleep_catches_up_at_once(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cerepulse.ui import main_window as module

    asked = _silence_sync(window, monkeypatch)
    daily: list[int] = []
    monkeypatch.setattr(window, "_refresh_daily", lambda: daily.append(1))
    monkeypatch.setattr(module.time, "time", lambda: window._beat + 3 * 3600)

    window._heartbeat()

    assert asked == ["refresh"]
    assert daily == [1], "the quote fetched before the Wi-Fi came back is retried"


def test_waking_while_paused_stays_paused(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cerepulse.ui import main_window as module

    asked = _silence_sync(window, monkeypatch)
    monkeypatch.setattr(window, "_refresh_daily", lambda: None)
    window._on_error(SessionTakenError("signed in elsewhere"))
    monkeypatch.setattr(module.time, "time", lambda: window._beat + 3 * 3600)

    window._heartbeat()

    assert asked == []
    assert window._paused


def test_an_ordinary_minute_is_not_a_wake(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cerepulse.ui import main_window as module

    asked = _silence_sync(window, monkeypatch)
    monkeypatch.setattr(module.time, "time", lambda: window._beat + 61)
    window._heartbeat()
    assert asked == []
