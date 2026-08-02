"""Shared widgets: cards, banners, insight chips, and the segmented day bar.

The segment bar is ninetofive's, repainted as a ``QWidget``. It is the one custom-painted
element because it carries information no stock widget does: the actual shape of the day —
where work happened, where breaks fell, which stretches were inferred from a missing punch.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from cerepulse.intelligence.insights import Action, Insight, Severity
from cerepulse.intelligence.month import DayRollup
from cerepulse.intelligence.next_action import NextAction
from cerepulse.intelligence.segments import WorkSegment
from cerepulse.models.values import Duration
from cerepulse.ui.theme import Palette, Space


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
    """A one-line status strip: offline, syncing, stale data, errors.

    Messages are keyed, and the strip shows the most severe one that is still current. A
    single slot meant the last writer won: a sync that finished cleanly cleared the sign-in
    failure above it, and a leave-ledger warning erased the error that explained why. Under
    keys, each source clears only its own message, and a second concurrent problem is
    counted rather than silently dropped.
    """

    #: Most severe first. Ties keep insertion order, so the oldest unresolved problem wins.
    _RANK = {
        Severity.CRITICAL: 0,
        Severity.WARNING: 1,
        Severity.INFO: 2,
        Severity.SUCCESS: 3,
    }

    #: Object names matching the #Banner* rules in the stylesheet.
    _STYLES = {
        Severity.SUCCESS: "BannerInfo",
        Severity.INFO: "BannerInfo",
        Severity.WARNING: "BannerWarning",
        Severity.CRITICAL: "BannerError",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Banner")
        self.setWordWrap(True)
        self.setVisible(False)
        self._messages: dict[str, tuple[str, Severity]] = {}

    def show_message(
        self, text: str, severity: Severity = Severity.INFO, *, key: str = "general"
    ) -> None:
        self._messages[key] = (text, severity)
        self._render()

    def clear_message(self, key: str | None = None) -> None:
        """Drop one source's message, or all of them when ``key`` is omitted."""
        if key is None:
            self._messages.clear()
        else:
            self._messages.pop(key, None)
        self._render()

    def _render(self) -> None:
        if not self._messages:
            self.setVisible(False)
            return

        text, severity = min(self._messages.values(), key=lambda entry: self._RANK.get(entry[1], 9))
        others = len(self._messages) - 1
        self.setText(f"{text}  ·  +{others} more" if others else text)
        # Everything current is on the tooltip, so a hidden second problem is still
        # reachable rather than merely counted.
        self.setToolTip("\n".join(message for message, _ in self._messages.values()))
        self.setObjectName(self._STYLES[severity])
        # Qt caches the resolved style per object name, so it must be re-resolved by hand
        # when the name changes on a live widget.
        self.style().unpolish(self)
        self.style().polish(self)
        self.setVisible(True)


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


class DayTimeline(QWidget):
    """The day on a clock axis: work, breaks, every punch, and where the finish line is.

    :class:`SegmentBar` draws the day to scale but anchored to itself — the bar starts at
    the first punch and ends at the last, so it shows the day's *shape* and cannot show its
    *position*. Two identical-looking bars can be a 7 AM start and a 2 PM one, and neither
    can show a finish time that has not been reached.

    Here the axis is wall-clock hours, so the bands sit where they happened, the punches are
    labelled with the times they actually were, and the moment you can leave is a marker on
    the same scale rather than a number somewhere else on the screen.
    """

    HEIGHT = 74
    PUNCH_ROW = 15  # baseline for punch labels, above the bar
    BAR_TOP = 22
    BAR_HEIGHT = 24
    AXIS_TOP = 54
    #: Minimum axis span. A day two hours old would otherwise be drawn at absurd magnification.
    MIN_HOURS = 5

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._segments: tuple[WorkSegment, ...] = ()
        self._leave_at: datetime | None = None
        self._now: datetime | None = None
        self._start: datetime | None = None
        self._end: datetime | None = None
        self.setMinimumHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_day(
        self,
        segments: tuple[WorkSegment, ...],
        *,
        leave_at: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        self._segments = segments
        self._leave_at = leave_at
        self._now = now

        if not segments:
            self._start = self._end = None
            self.setToolTip("No punches recorded")
            self.update()
            return

        latest = max(
            [segments[-1].end] + [moment for moment in (leave_at, now) if moment is not None]
        )
        start = segments[0].start.replace(minute=0, second=0, microsecond=0)
        end = (latest + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        if (end - start) < timedelta(hours=self.MIN_HOURS):
            end = start + timedelta(hours=self.MIN_HOURS)

        self._start, self._end = start, end
        self.setToolTip(
            "\n".join(
                f"{_clock_short(segment.start)} – {_clock_short(segment.end)}"
                f"  ({segment.duration})" + ("  · end inferred" if segment.end_inferred else "")
                for segment in segments
            )
        )
        self.update()

    # --- painting ---------------------------------------------------------------------

    def paintEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bar = QRectF(0, self.BAR_TOP, self.width(), self.BAR_HEIGHT)
        radius = self.BAR_HEIGHT / 2
        track = QPainterPath()
        track.addRoundedRect(bar, radius, radius)
        painter.fillPath(track, QColor(self._palette.border))

        if self._start is None or self._end is None:
            painter.setPen(QColor(self._palette.text_faint))
            painter.drawText(bar, Qt.AlignmentFlag.AlignCenter, "No punches recorded")
            return

        self._paint_hours(painter, bar)
        self._paint_bands(painter, bar, track)
        self._paint_markers(painter, bar)
        self._paint_punches(painter)

    def _x_for(self, moment: datetime) -> float:
        assert self._start is not None and self._end is not None
        total = (self._end - self._start).total_seconds()
        fraction = (moment - self._start).total_seconds() / total
        return min(max(fraction, 0.0), 1.0) * self.width()

    def _paint_hours(self, painter: QPainter, bar: QRectF) -> None:
        """Hour gridlines and labels — the thing that makes it a clock rather than a bar."""
        assert self._start is not None and self._end is not None
        hours = int((self._end - self._start).total_seconds() // 3600)
        # Label every hour only while they fit; past that, every second or third one.
        step = max(1, -(-hours // 8))

        painter.setPen(QPen(QColor(self._palette.border), 1))
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)

        for index in range(0, hours + 1, step):
            moment = self._start + timedelta(hours=index)
            x = self._x_for(moment)
            painter.setPen(QPen(QColor(self._palette.border), 1))
            painter.drawLine(int(x), int(bar.top()), int(x), int(bar.bottom()))
            painter.setPen(QColor(self._palette.text_faint))
            painter.drawText(
                QRectF(x - 24, self.AXIS_TOP, 48, 14),
                Qt.AlignmentFlag.AlignCenter,
                _hour_label(moment),
            )

    def _paint_bands(self, painter: QPainter, bar: QRectF, track: QPainterPath) -> None:
        painter.save()
        painter.setClipPath(track)
        # Breaks are the gaps between segments, so painting the span amber and the work over
        # it in aqua draws both without computing the gaps separately.
        first, last = self._segments[0].start, self._segments[-1].end
        painter.fillRect(
            QRectF(
                self._x_for(first), bar.top(), self._x_for(last) - self._x_for(first), bar.height()
            ),
            QColor(self._palette.rest),
        )

        for segment in self._segments:
            left = self._x_for(segment.start)
            band = QRectF(left, bar.top(), max(1.0, self._x_for(segment.end) - left), bar.height())
            painter.fillRect(band, QColor(self._palette.work))
            if segment.end_inferred:
                _hatch(painter, band, self._palette.surface)
        painter.restore()

        painter.setPen(QPen(QColor(self._palette.border), 1))
        painter.drawPath(track)

    def _paint_markers(self, painter: QPainter, bar: QRectF) -> None:
        """The finish line, and where the day has got to."""
        if self._leave_at is not None:
            pen = QPen(QColor(self._palette.good), 2)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            x = self._x_for(self._leave_at)
            painter.drawLine(int(x), int(bar.top() - 4), int(x), int(bar.bottom() + 4))

        if self._now is not None:
            painter.setPen(QPen(QColor(self._palette.text), 1))
            x = self._x_for(self._now)
            painter.drawLine(int(x), int(bar.top() - 4), int(x), int(bar.bottom() + 4))

    def _paint_punches(self, painter: QPainter) -> None:
        """A dot and a time at every real punch.

        Labels are dropped, not squeezed, when they would collide: an unreadable overlap of
        two times is worse than one time and a dot that says a punch is there.
        """
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        occupied = -1000.0

        for segment in self._segments:
            for moment, inferred in (
                (segment.start, False),
                (segment.end, segment.end_inferred),
            ):
                x = self._x_for(moment)
                colour = QColor(self._palette.text_faint if inferred else self._palette.text)
                painter.setBrush(colour)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QRectF(x - 2.5, self.BAR_TOP - 6, 5, 5))

                if inferred or x - 22 < occupied:
                    continue
                painter.setPen(colour)
                painter.drawText(
                    QRectF(x - 24, self.PUNCH_ROW - 11, 48, 12),
                    Qt.AlignmentFlag.AlignCenter,
                    _clock_short(moment),
                )
                occupied = x + 22


def _hatch(painter: QPainter, band: QRectF, colour: str) -> None:
    """Diagonal hatching marks a stretch whose end was inferred, not punched."""
    painter.save()
    painter.setClipRect(band)
    pen = QPen(QColor(colour))
    pen.setWidth(1)
    painter.setPen(pen)
    x = band.left() - band.height()
    while x < band.right():
        painter.drawLine(int(x), int(band.bottom()), int(x + band.height()), int(band.top()))
        x += 6
    painter.restore()


def _clock_short(moment: datetime) -> str:
    """9:05a — the time in as few characters as still reads unambiguously."""
    return moment.strftime("%I:%M%p").lstrip("0").replace("AM", "a").replace("PM", "p")


def _hour_label(moment: datetime) -> str:
    return moment.strftime("%I%p").lstrip("0").replace("AM", "am").replace("PM", "pm")


class BarChart(QWidget):
    """A labelled bar per value, with an optional reference line across them.

    Painted here rather than drawn with QtCharts. The packaging spec excludes QtCharts
    deliberately — dropping the unused Qt modules roughly halves the build — and pulling a
    whole charting framework back in for two bar charts would trade about fifteen megabytes
    for something this file already knows how to do. The segment bar, the timeline and the
    heatmap are all hand-painted for the same reason.

    Bars that meet the reference are drawn in the "good" colour and those that fall short in
    the work colour, so the comparison survives being read in greyscale or by someone who
    cannot separate the two hues — the height already carries it.
    """

    HEIGHT = 150
    LABEL_ROW = 16
    GAP = 6

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._bars: tuple[tuple[str, float, str], ...] = ()
        self._reference: float | None = None
        self._reference_label = ""
        self.setMinimumHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_bars(
        self,
        bars: Sequence[tuple[str, float, str]],
        *,
        reference: float | None = None,
        reference_label: str = "",
    ) -> None:
        """``bars`` is (label, value, tooltip-ish caption) in display order."""
        self._bars = tuple(bars)
        self._reference = reference
        self._reference_label = reference_label
        self.setVisible(bool(self._bars))
        self.setToolTip("\n".join(f"{label}: {caption}" for label, _value, caption in self._bars))
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        if not self._bars:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)

        plot_bottom = self.height() - self.LABEL_ROW
        ceiling = max([value for _l, value, _c in self._bars] + [self._reference or 0.0])
        if ceiling <= 0:
            return

        slot = self.width() / len(self._bars)
        width = max(6.0, slot - self.GAP)

        for index, (label, value, _caption) in enumerate(self._bars):
            height = (value / ceiling) * (plot_bottom - 4)
            left = index * slot + (slot - width) / 2
            colour = (
                self._palette.good
                if self._reference is not None and value >= self._reference
                else self._palette.work
            )
            bar = QRectF(left, plot_bottom - height, width, max(1.0, height))
            path = QPainterPath()
            path.addRoundedRect(bar, 3, 3)
            painter.fillPath(path, QColor(colour))

            painter.setPen(QColor(self._palette.text_faint))
            painter.drawText(
                QRectF(index * slot, plot_bottom + 2, slot, self.LABEL_ROW - 2),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )

        if self._reference is not None and self._reference > 0:
            y = plot_bottom - (self._reference / ceiling) * (plot_bottom - 4)
            pen = QPen(QColor(self._palette.text_muted), 1)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(0, int(y), self.width(), int(y))
            if self._reference_label:
                painter.setPen(QColor(self._palette.text_muted))
                painter.drawText(
                    QRectF(0, y - 14, self.width(), 12),
                    Qt.AlignmentFlag.AlignRight,
                    self._reference_label,
                )


class NextActionCard(QFrame):
    """The instruction, above everything that merely describes.

    Today reports a dozen true things and instructs on none of them. This is the one line
    that says what to do, so it sits above the numbers and is the only element on the screen
    allowed to be an imperative.
    """

    action_triggered = Signal(object)  # the Action

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._palette = palette
        self._action: Action | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.GAP, Space.ROW, Space.GAP, Space.ROW)
        layout.setSpacing(Space.ROW)

        self._stripe = QFrame()
        self._stripe.setFixedWidth(4)
        layout.addWidget(self._stripe)

        text = QVBoxLayout()
        text.setSpacing(Space.TIGHT // 2)
        self._headline = QLabel()
        self._headline.setWordWrap(True)
        self._detail = QLabel()
        self._detail.setObjectName("CardCaption")
        self._detail.setWordWrap(True)
        text.addWidget(self._headline)
        text.addWidget(self._detail)
        layout.addLayout(text, 1)

        self._button = QPushButton()
        self._button.setVisible(False)
        self._button.clicked.connect(lambda: self.action_triggered.emit(self._action))
        layout.addWidget(self._button)

    def set_action(self, action: NextAction | None) -> None:
        if action is None:
            self.setVisible(False)
            return

        colour = {
            Severity.SUCCESS: self._palette.good,
            Severity.INFO: self._palette.work,
            Severity.WARNING: self._palette.rest,
            Severity.CRITICAL: self._palette.bad,
        }[action.severity]

        self._stripe.setStyleSheet(f"background-color: {colour}; border-radius: 2px;")
        self._headline.setText(action.headline)
        self._headline.setStyleSheet(f"color: {colour}; font-size: 17px; font-weight: 600;")
        self._detail.setText(action.detail)

        self._action = action.action
        self._button.setVisible(self._action is not None)
        if self._action is not None:
            self._button.setText(self._action.label)
        self.setVisible(True)


class TargetBar(QWidget):
    """How much of the day's target is done, as one bar and one percentage.

    Four durations state the same fact between them and none of them answers "how far
    through am I" without arithmetic. Overtime is drawn past the target in its own colour
    rather than clamped at full, because a bar pinned at 100% cannot distinguish a day that
    finished on time from one that ran three hours over.
    """

    HEIGHT = 8

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._fraction = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.TIGHT)

        self._bar = QWidget()
        self._bar.setFixedHeight(self.HEIGHT)
        self._caption = QLabel()
        self._caption.setObjectName("CardCaption")
        layout.addWidget(self._bar)
        layout.addWidget(self._caption)

    def set_progress(self, fraction: float, caption: str = "") -> None:
        self._fraction = max(0.0, fraction)
        self._caption.setText(caption)
        self._caption.setVisible(bool(caption))
        self._bar.setStyleSheet(self._style())

    def _style(self) -> str:
        radius = f"border-radius: {self.HEIGHT // 2}px;"
        track = self._palette.border
        if self._fraction <= 0.0:
            return f"background: {track}; {radius}"
        if self._fraction >= 1.0:
            # Past the target the whole bar is met; overtime shows as the brighter fill
            # rather than as extra length there is no room for.
            return f"background: {self._palette.good}; {radius}"

        # The two stops have to straddle the boundary; landing both on one offset makes Qt
        # interpolate across the whole width and the bar reads as a wash.
        stop = min(max(self._fraction, 0.0001), 0.9999)
        return (
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            f" stop:0 {self._palette.work}, stop:{stop:.4f} {self._palette.work},"
            f" stop:{min(stop + 0.0001, 1.0):.4f} {track}, stop:1 {track}); {radius}"
        )


class MonthHeatmap(QWidget):
    """The month as a calendar, each day tinted by how much of the target it made.

    A table of thirty rows answers "what happened on the 14th". This answers "what does the
    month look like" — where the short days cluster, whether a bad week was one bad week or
    a slide. The two are complementary, so both are on the screen.

    Days needing attention carry a ring rather than a different fill. Fill is already
    spoken for by hours worked, and overloading it would make a short day and an outstanding
    one indistinguishable.
    """

    day_selected = Signal(object)  # date

    #: Weekday initials, Monday first, matching the grid's column order.
    HEADINGS = ("M", "T", "W", "T", "F", "S", "S")
    CELL = 30

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(4)
        self._grid.setColumnStretch(7, 1)

    def set_days(
        self,
        days: Sequence[DayRollup],
        *,
        target: Duration,
        attention: set[date] | None = None,
    ) -> None:
        """Render one month.

        Rollups rather than raw grid rows, because a rollup's ``worked`` is comparable with
        the work target. Tinting by ``Tot. Hrs.`` instead compares a gross span against a
        net target, which made an ordinary nine-hour day render as 112% of an eight-hour one
        and turned the whole calendar green.
        """
        self._clear()
        flagged = attention or set()

        for column, initial in enumerate(self.HEADINGS):
            heading = QLabel(initial)
            heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
            heading.setFixedWidth(self.CELL)
            heading.setStyleSheet(f"color: {self._palette.text_faint}; font-size: 10px;")
            self._grid.addWidget(heading, 0, column)

        if not days:
            return

        # Row 0 is the weekday headings, so the calendar starts at row 1. Weeks are indexed
        # from the first day's own Monday, which is what keeps columns aligned when a month
        # does not begin on one.
        first = min(day.day for day in days)
        origin = first - timedelta(days=first.weekday())

        for entry in sorted(days, key=lambda item: item.day):
            when = entry.day
            week = (when - origin).days // 7
            cell = _HeatCell(entry, target, self._palette, flagged=when in flagged)
            cell.clicked.connect(lambda day=when: self.day_selected.emit(day))
            self._grid.addWidget(cell, week + 1, when.weekday())

    def _clear(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()


class _HeatCell(QLabel):
    """One day of the heatmap."""

    clicked = Signal()

    def __init__(
        self,
        entry: DayRollup,
        target: Duration,
        palette: Palette,
        *,
        flagged: bool,
        parent: QWidget | None = None,
    ) -> None:
        when = entry.day
        super().__init__(str(when.day), parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(MonthHeatmap.CELL, MonthHeatmap.CELL)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        fill, ink = _heat_colours(entry, target, palette)
        border = f"1px solid {palette.bad}" if flagged else "1px solid transparent"
        self.setStyleSheet(
            f"background-color: {fill}; color: {ink}; border: {border};"
            f" border-radius: 6px; font-size: 11px;"
        )
        self.setToolTip(_heat_tooltip(entry, flagged))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def _heat_colours(entry: DayRollup, target: Duration, palette: Palette) -> tuple[str, str]:
    """Fill and text colour for one cell.

    Keyed on hours worked, not on status: a Saturday someone actually came in for is one of
    the more interesting cells in the month, and greying it out because the portal calls it
    a weekly off would hide the very thing worth seeing.
    """
    if entry.worked.minutes <= 0:
        return palette.overlay, palette.text_faint

    fraction = min(1.0, entry.worked.minutes / target.minutes) if target.minutes else 0.0
    base = palette.good if entry.worked >= target else palette.work
    # Alpha, not a lighter hue: the surface shows through, so the same colour reads as one
    # scale rather than a set of unrelated shades.
    alpha = int(60 + 195 * fraction)
    return _rgba(base, alpha), palette.text


def _heat_tooltip(entry: DayRollup, flagged: bool) -> str:
    label = entry.day.strftime("%A, %d %B").replace(" 0", " ")
    if entry.worked.minutes <= 0:
        state = entry.status.value.replace("_", " ")
        return f"{label} — {state}"

    body = f"{label} — {entry.worked.as_clock()} worked"
    if not entry.is_working_day:
        body += f" ({entry.status.value.replace('_', ' ')})"
    if entry.estimated:
        body += "\nEstimated from the grid; punch detail not synced"
    if entry.in_progress:
        body += "\nStill in progress"
    return f"{body}\nNeeds attention" if flagged else body


def _rgba(hex_colour: str, alpha: int) -> str:
    colour = QColor(hex_colour)
    return f"rgba({colour.red()}, {colour.green()}, {colour.blue()}, {alpha})"


class SectionTitle(QLabel):
    """A heading above a group of widgets."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        font = QFont()
        font.setPointSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        self.setFont(font)


class Skeleton(QWidget):
    """Placeholder bars shaped like the content that is coming.

    A cold start used to paint the real cards full of em-dashes, which reads as "there is
    no data" rather than "the data has not arrived". Blocks in the shape of the answer say
    the second thing without pretending to be the first.

    The shimmer is a slow gradient sweep rather than a spinner: it says work is happening
    without drawing the eye away from whatever the user is already reading.
    """

    #: Slow enough not to be a distraction, fast enough to read as alive.
    TICK_MS = 40
    PERIOD = 60

    def __init__(
        self,
        palette: Palette,
        *,
        rows: int = 3,
        height: int = 16,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self._phase = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SNUG)

        self._bars: list[QWidget] = []
        for index in range(rows):
            bar = QWidget()
            bar.setFixedHeight(height)
            # Ragged widths read as text; equal ones read as a table that failed to load.
            bar.setMaximumWidth(420 - (index % 3) * 90)
            layout.addWidget(bar)
            self._bars.append(bar)

        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)
        self.setVisible(False)

    def start(self) -> None:
        self.setVisible(True)
        self._timer.start()
        self._tick()

    def stop(self) -> None:
        self._timer.stop()
        self.setVisible(False)

    def _tick(self) -> None:
        self._phase = (self._phase + 1) % self.PERIOD
        for index, bar in enumerate(self._bars):
            offset = ((self._phase + index * 6) % self.PERIOD) / self.PERIOD
            bar.setStyleSheet(_shimmer(self._palette, offset))

    def hideEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        # A timer ticking behind a hidden widget is pure waste, and this one can sit on a
        # screen the user never opens.
        self._timer.stop()
        super().hideEvent(event)  # type: ignore[arg-type]


def _shimmer(palette: Palette, offset: float) -> str:
    """A soft highlight travelling left to right across the placeholder."""
    peak = min(max(offset, 0.001), 0.999)
    return (
        f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
        f" stop:0 {palette.border},"
        f" stop:{max(peak - 0.18, 0.0):.3f} {palette.border},"
        f" stop:{peak:.3f} {palette.overlay},"
        f" stop:{min(peak + 0.18, 1.0):.3f} {palette.border},"
        f" stop:1 {palette.border});"
        f" border-radius: 6px;"
    )


class StatusChip(QLabel):
    """A short state word on a tinted pill — present, pending, rejected.

    Coloured text alone was doing this job in three different screens, each with its own
    palette mapping. A chip reads as a state rather than as emphasis, and one definition
    means "approved" is the same green everywhere it appears.
    """

    def __init__(self, text: str, colour: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_state(text, colour)

    def set_state(self, text: str, colour: str) -> None:
        """Change what the chip says. Long-lived chips outnumber throwaway ones now."""
        self.setText(text)
        self.setStyleSheet(
            f"color: {colour}; background-color: {_tint(colour, 38)};"
            f" border-radius: 8px; padding: 2px 8px; font-size: 11px; font-weight: 600;"
        )


class EmptyState(QWidget):
    """What a screen shows when it has nothing — a reason, not a blank rectangle.

    An empty table under a heading reads as a failure. Saying which of "nothing to show",
    "not synced yet" or "filtered out" applies is usually the only thing the user needs.
    """

    def __init__(
        self,
        headline: str,
        detail: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, Space.GAP, 0, Space.GAP)
        layout.setSpacing(Space.TIGHT)

        self._headline = QLabel(headline)
        self._headline.setStyleSheet("font-weight: 600;")
        self._detail = QLabel(detail)
        self._detail.setObjectName("CardCaption")
        self._detail.setWordWrap(True)
        self._detail.setVisible(bool(detail))

        layout.addWidget(self._headline)
        layout.addWidget(self._detail)

    def set_message(self, headline: str, detail: str = "") -> None:
        self._headline.setText(headline)
        self._detail.setText(detail)
        self._detail.setVisible(bool(detail))


def data_table(
    columns: Sequence[str],
    *,
    stretch_last: bool = True,
    fit_rows: bool = False,
    selectable: bool = False,
) -> QTableWidget:
    """The one table style.

    There were four of these, one per screen, differing in resize mode, grid lines and
    scroll policy — so the same data looked like it came from three different applications.

    ``fit_rows`` shows every row instead of scrolling, for the short tables that sit inside
    an already-scrolling page: a scrollbar within a scrollbar is unusable with a wheel.
    """
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(list(columns))
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(
        QAbstractItemView.SelectionMode.SingleSelection
        if selectable
        else QAbstractItemView.SelectionMode.NoSelection
    )
    table.setShowGrid(False)

    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setStretchLastSection(stretch_last)
    # Headers stay put while the rows move, so a long month never leaves the reader
    # guessing which column is which.
    header.setFixedHeight(34)

    if fit_rows:
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setSizeAdjustPolicy(QAbstractItemView.SizeAdjustPolicy.AdjustToContents)
    return table


def card_row(*cards: QWidget) -> QWidget:
    """Lay cards out in an evenly spaced row."""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(Space.ROW)
    for card in cards:
        layout.addWidget(card)
    return container


def _tint(colour: str, alpha: int) -> str:
    """The chip's own colour at low opacity, so the pill always suits its text."""
    value = QColor(colour)
    return f"rgba({value.red()}, {value.green()}, {value.blue()}, {alpha})"


def link_button(text: str, on_click: Callable[[], None]) -> QPushButton:
    button = QPushButton(text)
    button.clicked.connect(on_click)
    return button
