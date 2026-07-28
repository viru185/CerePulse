"""Shared widgets: cards, banners, insight chips, and the segmented day bar.

The segment bar is ninetofive's, repainted as a ``QWidget``. It is the one custom-painted
element because it carries information no stock widget does: the actual shape of the day —
where work happened, where breaks fell, which stretches were inferred from a missing punch.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from cerepulse.intelligence.insights import Insight, Severity
from cerepulse.intelligence.segments import WorkSegment
from cerepulse.ui.theme import Palette


class Card(QFrame):
    """A titled metric with an optional caption, and an optional click target.

    Clicking opens the "why this number?" explanation, so every number on screen can show
    the punches and arithmetic behind it.
    """

    clicked = Signal()

    def __init__(
        self,
        title: str,
        *,
        value: str = "—",
        caption: str = "",
        accent: str | None = None,
        clickable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._clickable = clickable
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        self._title = QLabel(title.upper())
        self._title.setObjectName("CardTitle")

        self._value = QLabel(value)
        self._value.setObjectName("CardValue")
        if accent:
            self._value.setStyleSheet(f"color: {accent};")

        self._caption = QLabel(caption)
        self._caption.setObjectName("CardCaption")
        self._caption.setWordWrap(True)
        self._caption.setVisible(bool(caption))

        layout.addWidget(self._title)
        layout.addWidget(self._value)
        layout.addWidget(self._caption)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def set_value(self, value: str, *, accent: str | None = None) -> None:
        self._value.setText(value)
        self._value.setStyleSheet(f"color: {accent};" if accent else "")

    def set_caption(self, caption: str) -> None:
        self._caption.setText(caption)
        self._caption.setVisible(bool(caption))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt override
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class Banner(QLabel):
    """A one-line status strip: offline, syncing, stale data, errors."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Banner")
        self.setWordWrap(True)
        self.setVisible(False)

    #: Object names matching the #Banner* rules in the stylesheet.
    _STYLES = {
        Severity.SUCCESS: "BannerInfo",
        Severity.INFO: "BannerInfo",
        Severity.WARNING: "BannerWarning",
        Severity.CRITICAL: "BannerError",
    }

    def show_message(self, text: str, severity: Severity = Severity.INFO) -> None:
        self.setText(text)
        self.setObjectName(self._STYLES[severity])
        # Qt caches the resolved style per object name, so it must be re-resolved by hand
        # when the name changes on a live widget.
        self.style().unpolish(self)
        self.style().polish(self)
        self.setVisible(True)

    def clear_message(self) -> None:
        self.setVisible(False)


class InsightChip(QFrame):
    """One insight, with its action button when it has one."""

    action_triggered = Signal(object)  # the Insight

    def __init__(self, insight: Insight, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._insight = insight

        colour = {
            Severity.SUCCESS: palette.good,
            Severity.INFO: palette.work,
            Severity.WARNING: palette.rest,
            Severity.CRITICAL: palette.bad,
        }[insight.severity]

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        stripe = QFrame()
        stripe.setFixedWidth(3)
        stripe.setStyleSheet(f"background-color: {colour}; border-radius: 2px;")
        layout.addWidget(stripe)

        text = QVBoxLayout()
        text.setSpacing(1)
        title = QLabel(insight.title)
        title.setStyleSheet(f"color: {colour}; font-weight: 600;")
        text.addWidget(title)
        if insight.detail:
            detail = QLabel(insight.detail)
            detail.setObjectName("CardCaption")
            detail.setWordWrap(True)
            text.addWidget(detail)
        layout.addLayout(text, 1)

        if insight.action is not None:
            button = QPushButton(insight.action.label)
            button.clicked.connect(lambda: self.action_triggered.emit(insight))
            layout.addWidget(button)


class InsightStrip(QWidget):
    """A vertical stack of insight chips, rebuilt on each refresh."""

    action_triggered = Signal(object)

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)

    def set_insights(self, insights: list[Insight]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

        for insight in insights:
            chip = InsightChip(insight, self._palette)
            chip.action_triggered.connect(self.action_triggered)
            self._layout.addWidget(chip)
        self.setVisible(bool(insights))


class SegmentBar(QWidget):
    """The day drawn to scale: work in aqua, breaks in amber, inferred stretches hatched.

    Ported from ninetofive's segmented bar. Hovering a band shows what it is; the hatch is
    what tells a user a stretch was reconstructed rather than recorded.
    """

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._segments: tuple[WorkSegment, ...] = ()
        self._start: datetime | None = None
        self._end: datetime | None = None
        self.setMinimumHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_segments(self, segments: tuple[WorkSegment, ...]) -> None:
        self._segments = segments
        if segments:
            self._start = segments[0].start
            self._end = segments[-1].end
            span = (self._end - self._start).total_seconds() / 3600
            self.setToolTip(f"{span:.1f}h from first in to last out")
        else:
            self._start = self._end = None
            self.setToolTip("No punches recorded")
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(self.rect()).adjusted(0, 6, 0, -6)
        radius = rect.height() / 2

        track = QPainterPath()
        track.addRoundedRect(rect, radius, radius)
        painter.fillPath(track, QColor(self._palette.border))

        if not self._segments or self._start is None or self._end is None:
            painter.setPen(QColor(self._palette.text_faint))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No punches recorded")
            return

        total = (self._end - self._start).total_seconds()
        if total <= 0:
            return

        painter.setClipPath(track)
        # Breaks are the gaps, so painting the track amber and work over it in aqua draws
        # both without computing the gaps separately.
        painter.fillRect(rect, QColor(self._palette.rest))

        for segment in self._segments:
            offset = (segment.start - self._start).total_seconds() / total
            width = (segment.end - segment.start).total_seconds() / total
            band = QRectF(
                rect.left() + offset * rect.width(),
                rect.top(),
                max(1.0, width * rect.width()),
                rect.height(),
            )
            painter.fillRect(band, QColor(self._palette.work))
            if segment.end_inferred:
                self._hatch(painter, band)

        painter.setClipping(False)
        painter.setPen(QPen(QColor(self._palette.border), 1))
        painter.drawPath(track)

    def _hatch(self, painter: QPainter, band: QRectF) -> None:
        """Diagonal hatching marks a stretch whose end was inferred, not punched."""
        painter.save()
        painter.setClipRect(band)
        pen = QPen(QColor(self._palette.surface))
        pen.setWidth(1)
        painter.setPen(pen)
        step = 6
        x = band.left() - band.height()
        while x < band.right():
            painter.drawLine(int(x), int(band.bottom()), int(x + band.height()), int(band.top()))
            x += step
        painter.restore()


class SectionTitle(QLabel):
    """A heading above a group of widgets."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        font = QFont()
        font.setPointSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        self.setFont(font)


def card_row(*cards: QWidget) -> QWidget:
    """Lay cards out in an evenly spaced row."""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    for card in cards:
        layout.addWidget(card)
    return container


def link_button(text: str, on_click: Callable[[], None]) -> QPushButton:
    button = QPushButton(text)
    button.clicked.connect(on_click)
    return button
