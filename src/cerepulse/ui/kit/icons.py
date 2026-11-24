"""The app's icons: one 20 px stroke set, drawn sharp at every display scale.

Drawn here as SVG path data rather than bundled from an icon library, so nothing is
downloaded and nothing carries a licence to track. Each is a 1.5 px line on a 20x20 grid —
the weight Windows 11's own Regular icons use — and is rendered once per common pixel ratio,
so Qt picks a sharp bitmap at 125%, 150% and 175% rather than stretching a 20-pixel one.

Colour is a tone name, never a hex value: an icon is redrawn from the palette when the theme
changes (see :class:`IconLabel`).
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QLabel, QWidget

from cerepulse.ui.theme import Palette, tone_colour

#: Every icon, as path data on a 20x20 grid.
PATHS: dict[str, str] = {
    # The screens.
    "today": "M10 3.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM10 6.5V10l2.5 1.5",
    "week": "M3 5h14v11H3zM3 8.5h14M7 3v3M13 3v3",
    "attendance": "M4 15V9M8 15V5M12 15v-4M16 15V7",
    "insights": "M3 14l4-5 3 3 5-7M15 5h2v2",
    "records": "M5 3h7l3 3v11H5zM8 9h5M8 12h5",
    "pay": "M3 6h14v9H3zM3 9h14M6 12.5h3",
    "settings": (
        "M10 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2"
        "M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M4.7 15.3l1.4-1.4M13.9 6.1l1.4-1.4"
    ),
    "about": "M10 3.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM10 9v4.5M10 6.5v.5",
    # Actions.
    "external": "M11 3h6v6M17 3l-8 8M8 4H4v12h12v-4",
    "refresh": "M16 10a6 6 0 1 1-1.8-4.3M16 3.5V7h-3.5",
    "copy": "M7 7h9v10H7zM4 13V3h9",
    "chevron-left": "M12.5 4.5L7 10l5.5 5.5",
    "chevron-right": "M7.5 4.5L13 10l-5.5 5.5",
    # What happened.
    "leave": "M3.5 5h13v11.5h-13zM3.5 8.5h13M7 3v3M13 3v3M7.5 12.5h5",
    "earned": "M10 4v12M4 10h12",
    "lapse": "M10 3.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM6.5 6.5l7 7",
    "swipe": "M4 10h11M11 6l4 4-4 4",
    "duty": "M10 17s5-4.5 5-8.5a5 5 0 0 0-10 0c0 4 5 8.5 5 8.5zM10 7.5v2",
    "absence": "M10 3.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM10 6.5v4M10 13.5v.5",
    # States.
    "check": "M10 3.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM7 10.2l2.1 2.1 4-4.3",
    "warning": "M10 3.5l7 12.5H3zM10 8.5v3.5M10 14v.5",
    "info": "M10 3.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM10 9v4.5M10 6.5v.5",
}

#: The ratios a bitmap is drawn at. Windows offers 100–200% in 25% steps, and 300% exists.
RATIOS = (1.0, 1.25, 1.5, 1.75, 2.0, 3.0)


def _svg(name: str, colour: str) -> bytes:
    try:
        path = PATHS[name]
    except KeyError as exc:
        raise KeyError(f"no icon called {name!r}") from exc
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">'
        f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ).encode()


def pixmap(name: str, colour: str, *, size: int = 20, ratio: float = 1.0) -> QPixmap:
    """One icon drawn at ``size`` logical pixels and ``ratio`` device pixels per pixel."""
    side = max(1, round(size * ratio))
    image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(_svg(name, colour)))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    drawn = QPixmap.fromImage(image)
    drawn.setDevicePixelRatio(ratio)
    return drawn


@lru_cache(maxsize=512)
def icon(name: str, colour: str, *, size: int = 20) -> QIcon:
    """An icon in a colour, holding a bitmap for every common pixel ratio."""
    made = QIcon()
    for ratio in RATIOS:
        made.addPixmap(pixmap(name, colour, size=size, ratio=ratio))
    return made


def toned(name: str, palette: Palette, tone: str = "muted", *, size: int = 20) -> QIcon:
    """An icon in one of the palette's tones."""
    return icon(name, tone_colour(palette, tone), size=size)


class IconLabel(QLabel):
    """An icon on its own, in a tone, redrawn when the theme changes."""

    def __init__(
        self,
        name: str,
        palette: Palette,
        *,
        tone: str = "muted",
        size: int = 20,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._name, self._tone, self._size = name, tone, size
        self._palette_value = palette
        self.setFixedSize(QSize(size, size))
        self._redraw()

    @property
    def _palette(self) -> Palette:
        return self._palette_value

    @_palette.setter
    def _palette(self, palette: Palette) -> None:
        # The theme walk sets this; a stored bitmap would keep the old colour.
        self._palette_value = palette
        self._redraw()

    def set_icon(self, name: str, *, tone: str | None = None) -> None:
        self._name = name
        if tone is not None:
            self._tone = tone
        self._redraw()

    def _redraw(self) -> None:
        colour = tone_colour(self._palette_value, self._tone)
        ratio = self.devicePixelRatioF() or 1.0
        self.setPixmap(pixmap(self._name, colour, size=self._size, ratio=ratio))
