"""A chart's key: a sample of each mark it draws, and what the mark means.

Week's lanes and the Attendance calendar each had their own: one a column of sentences
("Green = target met", "Red outline = needs attention"), the other nothing at all. A key is
read against the chart beside it, so it shows the mark itself rather than naming its colour —
and it is one class, so every key in the app reads the same way.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QHBoxLayout, QWidget

from cerepulse.ui.kit.text import Text
from cerepulse.ui.kit.views import Box
from cerepulse.ui.theme import TONES, Palette, tone_colour


class Swatch(QWidget):
    """A small sample of one mark, in tones: a fill, an outline, a line, hatching."""

    WIDTH, HEIGHT = 16, 12

    def __init__(
        self,
        palette: Palette,
        parent: QWidget | None = None,
        *,
        fill: str | None = None,
        opacity: float = 1.0,
        outline: str | None = None,
        dashed: bool = False,
        hatched: bool = False,
        line: bool = False,
    ) -> None:
        super().__init__(parent)
        for tone in (fill, outline):
            if tone is not None and tone not in TONES:
                raise ValueError(f"unknown tone {tone!r}")
        self._palette = palette
        self._fill, self._opacity, self._outline = fill, opacity, outline
        self._dashed, self._hatched, self._line = dashed, hatched, line
        self.setFixedSize(self.WIDTH, self.HEIGHT)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(1, 1, self.WIDTH - 2, self.HEIGHT - 2)
        if self._line and self._fill is not None:
            # A line, not a block: how a chart draws a gap or a break.
            painter.fillRect(
                QRectF(1, self.HEIGHT / 2 - 1.25, self.WIDTH - 2, 2.5),
                QColor(tone_colour(self._palette, self._fill)),
            )
            painter.end()
            return
        if self._fill is not None:
            colour = QColor(tone_colour(self._palette, self._fill))
            colour.setAlphaF(self._opacity)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawRoundedRect(box, 3, 3)
            if self._hatched:
                painter.save()
                painter.setClipRect(box)
                painter.setPen(QPen(QColor(self._palette.surface), 1))
                for x in range(-self.HEIGHT, self.WIDTH, 4):
                    painter.drawLine(QPointF(x, self.HEIGHT), QPointF(x + self.HEIGHT, 0))
                painter.restore()
        if self._outline is not None:
            pen = QPen(QColor(tone_colour(self._palette, self._outline)), 1.5)
            if self._dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        painter.end()


class LegendItem(Box):
    """A swatch and what it means, as one item for a key's flow layout."""

    def __init__(self, swatch: Swatch, words: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        swatch.setParent(self)
        row.addWidget(swatch)
        self.words = Text(words, self, role="caption", tone="muted")
        row.addWidget(self.words)
