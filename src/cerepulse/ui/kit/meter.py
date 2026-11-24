"""A meter: how far through something is, as one thin bar of one or more parts.

Progress was drawn with a stylesheet gradient whose two stops had to straddle the boundary,
re-written per bar and per screen, and a bar that wanted two fills — the week's finished
days and today's hours so far — could not have them. This paints, in tones, so it follows
the theme like every other kit widget and holds as many parts as the figure has.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPaintEvent
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from cerepulse.ui.kit.text import Text
from cerepulse.ui.theme import TONES, Palette, tone_colour

#: (share of the whole, tone, opacity). Parts are laid end to end from the left.
Part = tuple[float, str, float]


class Meter(QWidget):
    """A rounded track with its parts filled in from the left, each in a tone."""

    def __init__(self, palette: Palette, parent: QWidget | None = None, *, height: int = 8):
        super().__init__(parent)
        self._palette = palette
        self._parts: tuple[Part, ...] = ()
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    @property
    def parts(self) -> tuple[Part, ...]:
        return self._parts

    def set_fraction(self, fraction: float, tone: str = "work") -> None:
        self.set_parts((fraction, tone, 1.0))

    def set_parts(self, *parts: Part) -> None:
        """Fill the track with these, in order. A share past the end is cut at the end."""
        for _, tone, _ in parts:
            if tone not in TONES:
                raise ValueError(f"unknown tone {tone!r}")
        self._parts = tuple((max(0.0, share), tone, opacity) for share, tone, opacity in parts)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        track = QRectF(self.rect())
        radius = track.height() / 2
        shape = QPainterPath()
        shape.addRoundedRect(track, radius, radius)
        painter.fillPath(shape, QColor(self._palette.border))
        # Clipped to the track, so the ends are round and the joins between parts square.
        painter.setClipPath(shape)
        x = track.left()
        for share, tone, opacity in self._parts:
            width = min(track.right() - x, track.width() * share)
            if width <= 0:
                continue
            colour = QColor(tone_colour(self._palette, tone))
            colour.setAlphaF(opacity)
            painter.fillRect(QRectF(x, track.top(), width, track.height()), colour)
            x += width
        painter.end()


class Progress(QWidget):
    """A meter and the sentence under it: how far through a target, and what that means.

    Past the target the whole bar is filled in the good tone rather than clamped silently at
    full in the working one, so a day that is done reads as done.
    """

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.meter = Meter(palette, self)
        self.caption = Text(parent=self, role="caption", tone="muted", wrap=True)
        layout.addWidget(self.meter)
        layout.addWidget(self.caption)

    def set_progress(self, fraction: float, caption: str = "") -> None:
        self.meter.set_fraction(min(1.0, fraction), "good" if fraction >= 1.0 else "work")
        self.caption.setText(caption)
        self.caption.setVisible(bool(caption))
