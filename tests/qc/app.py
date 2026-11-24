"""The whole window, built against the synthetic year, offline, ready to screenshot."""

from __future__ import annotations

import time as wallclock
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter

from cerepulse.core.config import AppConfig
from cerepulse.core.errors import TransportError
from cerepulse.daily.models import Quote
from tests.qc import seed

#: Sidebar order, as the window lays it out.
SCREENS = ("today", "week", "attendance", "insights", "records", "pay", "settings", "about")

#: Screens with more than one view behind tabs: (file suffix, view index) for each view
#: after the first, which the screen opens on.
VIEWS: dict[str, tuple[tuple[str, int], ...]] = {
    "attendance": (("-day", 1),),
    "records": (("-balances", 1), ("-calendar", 2)),
}


def config_for(theme: str = "dark") -> AppConfig:
    """Everything visible, nothing that reaches outside the process."""
    base = AppConfig()
    return replace(
        base,
        ui=replace(base.ui, theme=theme, background_mode="foreground", background_enabled=False),
        notifications=replace(base.notifications, enabled=False),
        updates=replace(base.updates, check_on_startup=False),
        pay=replace(base.pay, enabled=True),
        portal=replace(base.portal, username=seed.EMPLOYEE.code),
    )


def _offline(method: str, url: str, **_kwargs: Any) -> Any:
    raise TransportError(f"{method} {url} failed: offline (QC harness)")


def _quote(day: date) -> Quote:
    return Quote(
        text="Simplicity is not the goal. It is the by-product of a good idea and modest "
        "expectations.",
        author="Paul Rand",
        day=day,
    )


def _anime(day: date) -> Quote:
    return Quote(
        text="Whatever happens, happens.",
        author="Spike Spiegel",
        day=day,
        credit="Anime quotes provided by Animechan",
        credit_url="https://animechan.io/",
        work="Cowboy Bebop",
        source="anime",
    )


def _picture(day: date, into: Path) -> tuple[Path, str, str, str]:
    """A dusk gradient standing in for the picture of the day."""
    into.mkdir(parents=True, exist_ok=True)
    image = QImage(1920, 1080, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    gradient = QLinearGradient(0, 0, 1920, 1080)
    gradient.setColorAt(0.0, QColor("#1d2b64"))
    gradient.setColorAt(0.55, QColor("#c06c84"))
    gradient.setColorAt(1.0, QColor("#f8b195"))
    painter.fillRect(image.rect(), gradient)
    painter.end()
    path = into / f"picture-{day:%Y%m%d}.jpg"
    image.save(str(path), "JPG", 85)
    return path, "Evening over the river", "Synthetic gradient (QC harness)", ""


def style_like_the_app(application: Any, theme: str = "dark") -> Any:
    """Give the application the font and sheet ``run_app`` gives it; returns an undo.

    Without them every label measures in Qt's default font, which is smaller than the app's,
    and a screen that is too tight for the real thing passes. Undone afterwards because the
    sheet is application-wide and the rest of a test session must not inherit it.
    """
    from cerepulse.ui.theme import apply_font, palette_for, stylesheet

    previous_font = application.font()
    previous_sheet = application.styleSheet()
    apply_font(application)
    application.setStyleSheet(stylesheet(palette_for(theme)))

    def undo() -> None:
        application.setStyleSheet(previous_sheet)
        application.setFont(previous_font)

    return undo


def build_window(data_root: Path, *, theme: str = "dark") -> Any:
    """The real ``MainWindow`` over the seeded cache. Call inside ``clock.frozen``."""
    from cerepulse.app import build_app
    from cerepulse.core import paths
    from cerepulse.services.daily import DailyService
    from cerepulse.ui.main_window import MainWindow

    paths.data_root.cache_clear()
    config = config_for(theme)
    context = build_app(config=config, database_path=data_root / "qc.db")
    context.client.request = _offline  # type: ignore[method-assign]
    context.daily = DailyService(
        config=config,
        cache_dir=paths.cache_dir(),
        fetch_quote=_quote,
        fetch_anime=_anime,
        fetch_picture=_picture,
    )
    seed.seed(context)

    window = MainWindow(context)
    window._auto.stop()
    window._rollover.stop()
    window._sync.load_from_cache()
    window._sync.refresh_leave()
    window._sync.refresh_trends()
    window._refresh_daily()
    window._load_pay()
    settle(window)
    return window


def settle(window: Any, *, at_least: float = 0.4, at_most: float = 20.0) -> None:
    """Let queued work finish and its results render.

    Idle means the runner has been empty for a while *and* the records rebuild has fired:
    results arrive as queued signals, and each can submit more work.
    """
    started = wallclock.monotonic()
    quiet_since: float | None = None
    while wallclock.monotonic() - started < at_most:
        QCoreApplication.processEvents()
        busy = window._runner.busy or window._records_pending.isActive()
        now = wallclock.monotonic()
        if busy:
            quiet_since = None
        elif quiet_since is None:
            quiet_since = now
        elif now - quiet_since >= 0.25 and now - started >= at_least:
            break
        wallclock.sleep(0.01)
    QCoreApplication.processEvents()


def close(window: Any) -> None:
    context = window._context
    window._auto.stop()
    window._rollover.stop()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    window.close()
    context.close()
