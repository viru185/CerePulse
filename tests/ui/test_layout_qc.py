"""Every screen fits the room a real laptop gives it, and nothing flashes open at launch.

The screenshots ``tools/qc_screens.py`` takes are for a person to judge; these are the parts
of that judgement a machine can make. Sizes are logical pixels — what Qt lays out in — for a
1920x1080 panel at each common Windows scale, less the taskbar and the title bar. Laying out
at those sizes at 100% is the same layout the app gets at that scale; only the pixel density
differs, and that is the screenshots' job.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QWidget

from tests.qc import app as qc_app
from tests.qc import clock, layout, seed

#: (label, width, height) — the client area of a maximised window on a 1920x1080 panel.
SCREENS_AT_SCALE = (
    ("100%", 1920, 1000),
    ("125%", 1536, 784),
    ("150%", 1280, 640),
    ("175%", 1097, 537),
)

#: Defects this suite found on its first run, each with the change that removes it. Strict,
#: so the fix fails here until its entry is deleted — the list can only shrink honestly.
KNOWN: dict[tuple[str, str], str] = {
    ("minimum", "175%"): "the window's 900x600 minimum is taller than 537",
    ("settings", "175%"): "Settings' two 380px columns need more width than 1097",
    # Every screen, because the sidebar is on every screen.
    ("*", "150%"): "the sidebar cannot scroll, so a short window squeezes the daily tile",
    ("*", "175%"): "the sidebar cannot scroll, so a short window squeezes the daily tile",
}


def _expect_known(request: pytest.FixtureRequest, *key: str) -> None:
    scale = key[1] if len(key) > 1 else ""
    reason = KNOWN.get((key[0], scale)) or (
        KNOWN.get(("*", scale)) if key[0] in qc_app.SCREENS else None
    )
    if reason:
        request.applymarker(pytest.mark.xfail(strict=True, reason=reason))


pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="measures text in the Windows fonts the layout is designed for"
)


class _ShowWatcher(QObject):
    """Records every top-level window that gets shown — the "popups" users see flash."""

    def __init__(self) -> None:
        super().__init__()
        self.shown: list[str] = []

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 — Qt override
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget) and watched.isWindow():
            self.shown.append(layout.describe(watched))
        return False


@pytest.fixture(scope="module")
def built(qapp: QApplication, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Any]:
    root: Path = tmp_path_factory.mktemp("qc")
    previous = os.environ.get("CEREPULSE_DATA_DIR")
    os.environ["CEREPULSE_DATA_DIR"] = str(root)
    watcher = _ShowWatcher()
    unstyle = qc_app.style_like_the_app(qapp)
    qapp.installEventFilter(watcher)
    with clock.frozen(seed.NOW):
        try:
            window = qc_app.build_window(root)
        finally:
            qapp.removeEventFilter(watcher)
        window.flashed = list(watcher.shown)
        # Layouts only run for a shown window; unshown, every widget sits at Qt's
        # default 640x480 and every check below would be measuring that.
        window.show()
        qc_app.settle(window, at_least=0.1)
        yield window
        qc_app.close(window)
    unstyle()
    if previous is None:
        os.environ.pop("CEREPULSE_DATA_DIR", None)
    else:
        os.environ["CEREPULSE_DATA_DIR"] = previous


def test_nothing_flashes_open_while_the_window_is_built(
    built: Any, request: pytest.FixtureRequest
) -> None:
    """A widget made visible before it has a parent is a top-level window for that moment:
    it opens, and closes again when a layout adopts it. Five of them did, at every launch."""
    _expect_known(request, "flash")
    assert built.flashed == []


@pytest.mark.parametrize(("scale", "width", "height"), SCREENS_AT_SCALE)
def test_the_window_can_be_as_small_as_the_screen(
    built: Any, request: pytest.FixtureRequest, scale: str, width: int, height: int
) -> None:
    """A minimum size larger than the screen pushes the window's edge off it."""
    _expect_known(request, "minimum", scale)
    minimum = built.minimumSize()
    fits = minimum.width() <= width and minimum.height() <= height
    assert fits, f"minimum {minimum.width()}x{minimum.height()} exceeds {width}x{height} at {scale}"


@pytest.mark.parametrize(("scale", "width", "height"), SCREENS_AT_SCALE)
@pytest.mark.parametrize("screen", qc_app.SCREENS)
def test_every_screen_lays_out_without_cropping(
    built: Any, request: pytest.FixtureRequest, scale: str, width: int, height: int, screen: str
) -> None:
    _expect_known(request, screen, scale)
    built.resize(width, height)
    built._navigation.select(qc_app.SCREENS.index(screen))
    qc_app.settle(built, at_least=0.05)
    issues = [str(issue) for issue in layout.find_issues(built)]
    assert issues == [], f"{screen} at {scale}:\n" + "\n".join(issues)
