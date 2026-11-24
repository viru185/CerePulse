"""The full-year download, as the window runs it: when it starts, what it says, how it stops.

It must start by itself — on a fresh install and on one that has used the app for months —
but only after a sync has worked, never while the session is someone else's, never twice at
once, and not again once the year is in. Stop has to mean stop for this session.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from cerepulse.core import app_state
from cerepulse.core.config import AppConfig
from cerepulse.core.errors import SessionTakenError
from cerepulse.services.attendance import BackfillStep
from cerepulse.ui.controllers.sync import YearBackfill
from cerepulse.ui.main_window import YEAR_BACKFILL_KEY


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


def started(window, monkeypatch) -> list[bool]:  # type: ignore[no-untyped-def]
    """Record start requests instead of making them."""
    calls: list[bool] = []
    monkeypatch.setattr(
        window._sync, "start_year_backfill", lambda *, force=False: calls.append(force) or True
    )
    return calls


def fire(window) -> None:  # type: ignore[no-untyped-def]
    """What the delay's timer does when it runs out, without waiting two minutes."""
    if window._year_timer.isActive():
        window._year_timer.stop()
        window._start_year_backfill()


def test_a_clean_sync_starts_it_once(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = started(window, monkeypatch)

    window._on_sync_finished([])
    window._on_sync_finished([])  # a second sync inside the delay does not queue another
    assert window._year_timer.isActive() and calls == []
    fire(window)

    assert calls == [False]
    assert "Downloading this year's attendance and punches in the background" in (
        window.toasts.messages
    )
    assert app_state.get(YEAR_BACKFILL_KEY) == {"started": True, "complete": False}


def test_a_sync_with_failures_does_not(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = started(window, monkeypatch)
    window._on_sync_finished(["leave: the portal answered 500"])
    assert not window._year_timer.isActive()
    assert calls == []


def test_once_the_year_is_in_it_is_not_started_again(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app_state.put(YEAR_BACKFILL_KEY, {"started": True, "complete": True})
    calls = started(window, monkeypatch)

    window._on_sync_finished([])

    assert not window._year_timer.isActive()
    assert calls == []


def test_a_resumed_download_does_not_announce_itself_again(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app_state.put(YEAR_BACKFILL_KEY, {"started": True, "complete": False})
    calls = started(window, monkeypatch)

    window._on_sync_finished([])
    fire(window)

    assert calls == [False]
    assert window.toasts.messages == []


def test_paused_it_waits(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = started(window, monkeypatch)
    monkeypatch.setattr(window._sync, "refresh", lambda *a, **k: None)
    window._on_error(SessionTakenError("signed in elsewhere"))

    window._start_year_backfill()

    assert calls == []


def test_stop_holds_it_for_the_session(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = started(window, monkeypatch)

    window.cancel_history()
    window._on_sync_finished([])
    assert not window._year_timer.isActive()
    assert calls == []

    window._download_year_again()  # asking again overrides the hold
    assert calls == [True]


def test_finishing_records_it_and_says_so(window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    for name in ("load_fetchable_months", "reload_month", "refresh_trends"):
        monkeypatch.setattr(window._sync, name, lambda: None)
    run = YearBackfill(periods=[(2026, m) for m in range(11, 0, -1)], grids=6, days=120)

    window._year_backfill_ended(run, True)

    state = app_state.get(YEAR_BACKFILL_KEY)
    assert isinstance(state, dict)
    assert state["complete"] is True and state["months"] == 11 and state["days"] == 120
    assert window.settings._year_status.text().startswith("This year is downloaded — 11 months")


# --- the controller's chain ---------------------------------------------------------------


class _Attendance:
    def __init__(self, steps: list[BackfillStep]) -> None:
        self.steps = iter(steps)
        self.skips: list[frozenset[date]] = []

    def fetchable_months(self, **_kwargs: object) -> list[tuple[int, int]]:
        return [(2026, 11), (2026, 10)]

    def year_backfill_step(self, *_a: object, skip: frozenset[date], **_k: object) -> BackfillStep:
        self.skips.append(skip)
        return next(self.steps)


class _Coordinator:
    def __init__(self) -> None:
        self.runs = 0

    def run(self, operation):  # type: ignore[no-untyped-def]
        self.runs += 1
        return operation()


def test_the_controller_chains_units_through_the_coordinator(qapp, monkeypatch, qtbot) -> None:  # type: ignore[no-untyped-def]
    """Each unit is its own task, through ``sync.run`` (so an expired session is replayed
    once), with a pause between — and it stops at the step that says there is nothing left."""
    from types import SimpleNamespace

    from cerepulse.ui.controllers import sync as controller
    from cerepulse.ui.workers import TaskRunner

    monkeypatch.setattr(controller, "YEAR_BACKFILL_GAP_MS", 1)
    attendance = _Attendance(
        [
            BackfillStep((2026, 11), grid=True),
            BackfillStep((2026, 10), days=(date(2026, 10, 30),), missed=(date(2026, 10, 29),)),
            BackfillStep(None),
        ]
    )
    coordinator = _Coordinator()
    context = SimpleNamespace(attendance=attendance, sync=coordinator)
    sync = controller.SyncController(
        context=context,  # type: ignore[arg-type]
        runner=TaskRunner(),
        employee_code="QC0001",
    )
    progress: list[int] = []
    ended: list[tuple[YearBackfill, bool]] = []
    sync.year_backfill_progress.connect(lambda run: progress.append(run.month_number))
    sync.year_backfill_ended.connect(lambda run, done: ended.append((run, done)))

    assert sync.start_year_backfill()
    assert not sync.start_year_backfill(), "one run at a time"
    qtbot.waitUntil(lambda: bool(ended), timeout=3000)

    run, finished = ended[0]
    assert finished
    assert (run.grids, run.days) == (1, 1)
    assert progress == [1, 2]
    assert coordinator.runs == 3
    assert attendance.skips[-1] == {date(2026, 10, 30), date(2026, 10, 29)}
    assert sync.year_backfill is None


def test_a_paused_controller_ends_the_run_without_asking(qapp, qtbot) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    from cerepulse.ui.controllers import sync as controller
    from cerepulse.ui.workers import TaskRunner

    attendance = _Attendance([])
    context = SimpleNamespace(attendance=attendance, sync=_Coordinator())
    sync = controller.SyncController(context=context, runner=TaskRunner(), employee_code="x")  # type: ignore[arg-type]
    ended: list[bool] = []
    sync.year_backfill_ended.connect(lambda _run, done: ended.append(done))
    sync.paused = True

    sync.start_year_backfill()

    assert ended == [False]
    assert attendance.skips == []
