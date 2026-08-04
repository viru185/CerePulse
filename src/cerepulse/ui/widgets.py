"""Shared widgets: cards, banners, insight chips, and the segmented day bar.

The segment bar is ninetofive's, repainted as a ``QWidget``. It is the one custom-painted
element because it carries information no stock widget does: the actual shape of the day —
where work happened, where breaks fell, which stretches were inferred from a missing punch.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta
from html import escape
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
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

if TYPE_CHECKING:  # Imported for the annotation only; widgets must not depend on services.
    from cerepulse.services.commute import CommuteView

#: What a card shows when it has no number yet.
EMPTY_VALUE = "—"


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

    #: Href of the built-in "open the logs" action. Not a real URL — external links are
    #: turned off and this is matched by hand, so nothing a message contains can navigate
    #: anywhere. Portal text reaches these banners, and it is not trusted markup.
    LOGS_LINK = "cerepulse:logs"

    #: Raised when the banner's own log link is clicked. A signal rather than a direct call
    #: because a widget in `ui/widgets` has no business knowing where logs live.
    logs_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Banner")
        self.setWordWrap(True)
        self.setVisible(False)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setOpenExternalLinks(False)
        self.linkActivated.connect(self._on_link)
        self._messages: dict[str, tuple[str, Severity]] = {}
        self._with_logs: set[str] = set()

    def show_message(
        self,
        text: str,
        severity: Severity = Severity.INFO,
        *,
        key: str = "general",
        offer_logs: bool = False,
    ) -> None:
        """Show a message, optionally with a link to the logs.

        ``offer_logs`` is for the failures whose explanation is only in the log file. Saying
        "check the logs" without a way to reach them is an instruction the user cannot
        follow — the path was in a tooltip on a button on a screen nobody had a reason to
        open.
        """
        self._messages[key] = (text, severity)
        self._with_logs.discard(key)
        if offer_logs:
            self._with_logs.add(key)
        self._render()

    def _on_link(self, href: str) -> None:
        if href == self.LOGS_LINK:
            self.logs_requested.emit()

    def clear_message(self, key: str | None = None) -> None:
        """Drop one source's message, or all of them when ``key`` is omitted."""
        if key is None:
            self._messages.clear()
            self._with_logs.clear()
        else:
            self._messages.pop(key, None)
            self._with_logs.discard(key)
        self._render()

    def _render(self) -> None:
        if not self._messages:
            self.setVisible(False)
            return

        shown = min(self._messages.items(), key=lambda entry: self._RANK.get(entry[1][1], 9))
        key, (text, severity) = shown
        others = len(self._messages) - 1

        # Escaped, then decorated. The messages carry portal text and exception strings, so
        # rendering them as markup would let the vendor's HTML into our own widget.
        body = escape(text)
        if others:
            body += f"  ·  +{others} more"
        if key in self._with_logs:
            body += f'  ·  <a href="{self.LOGS_LINK}">Open the logs</a>'
        self.setText(body)
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

    The axis is wall-clock hours, so the bands sit where they happened, the punches are
    labelled with the times they actually were, and the moment you can leave is a marker on
    the same scale rather than a number somewhere else on the screen.

    Work is a solid block and a break is the thin track showing between two of them, so the
    difference is carried by *shape* rather than by hue alone — and each is labelled with
    its own length, because a ten-minute coffee and a ninety-minute lunch used to look
    identical until you hovered.

    Two modes, chosen from the measured width rather than by the caller. The same widget is
    drawn at ~870 px on Today and inside a much narrower panel on Attendance; one set of
    constants tuned for the wide case produced a scribble at the narrow one.
    """

    #: Below this the hour axis and in-block labels stop fitting, so they are dropped
    #: rather than overlapped.
    COMPACT_BELOW = 460

    HEIGHT_FULL = 96
    HEIGHT_COMPACT = 54
    #: Minimum axis span. A day two hours old would otherwise be drawn at absurd magnification.
    MIN_HOURS = 5
    #: Narrower than this and a label written inside a block would be clipped.
    LABEL_FITS = 48

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._segments: tuple[WorkSegment, ...] = ()
        self._leave_at: datetime | None = None
        self._now: datetime | None = None
        self._start: datetime | None = None
        self._end: datetime | None = None
        self._status_label = ""
        self._status_colour: str | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_day(
        self,
        segments: tuple[WorkSegment, ...],
        *,
        leave_at: datetime | None = None,
        now: datetime | None = None,
        status_label: str = "",
        status_colour: str | None = None,
        domain: tuple[int, int] | None = None,
    ) -> None:
        """Render a day.

        ``status_label`` is for the days that have no punches and never will — leave, a
        holiday, outdoor duty. Those used to draw an empty track reading "No punches
        recorded", which is true and tells the user nothing about a week in Bengaluru.

        ``domain`` is a pair of clock hours imposed from outside, for when several of these
        are stacked and have to be read against each other. Left to itself each timeline
        scales to its own day, which is right in isolation and wrong in a column: a nine-to-
        six Monday and a noon-to-nine Tuesday would draw as identical bars, hiding the very
        difference the stack exists to show.
        """
        self._segments = segments
        self._leave_at = leave_at
        self._now = now
        self._status_label = status_label
        self._status_colour = status_colour

        if not segments:
            self._start = self._end = None
            self.setToolTip(status_label or "No punches recorded")
            self.update()
            return

        if domain is not None:
            midnight = segments[0].start.replace(hour=0, minute=0, second=0, microsecond=0)
            self._start = midnight + timedelta(hours=domain[0])
            self._end = midnight + timedelta(hours=domain[1])
        else:
            latest = max(
                [segments[-1].end] + [moment for moment in (leave_at, now) if moment is not None]
            )
            start = segments[0].start.replace(minute=0, second=0, microsecond=0)
            end = (latest + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            if (end - start) < timedelta(hours=self.MIN_HOURS):
                end = start + timedelta(hours=self.MIN_HOURS)
            self._start, self._end = start, end

        self.setToolTip("\n".join(self._describe()))
        self.update()

    def _describe(self) -> list[str]:
        """The whole day as text, for the tooltip and for anyone reading it aloud."""
        lines: list[str] = []
        previous: WorkSegment | None = None
        for segment in self._segments:
            if previous is not None:
                gap = _gap_between(previous, segment)
                if gap.minutes > 0:
                    lines.append(f"    break {gap}")
            lines.append(
                f"{_clock_short(segment.start)} – {_clock_short(segment.end)}"
                f"  worked {segment.duration}"
                + ("  · end inferred" if segment.end_inferred else "")
            )
            previous = segment
        return lines

    # --- geometry ---------------------------------------------------------------------

    @property
    def _compact(self) -> bool:
        """Derived from the current width rather than cached.

        The same instance is drawn full-width on Today and in a side panel on Attendance,
        and a stored flag has to be kept in step with every resize — including the ones Qt
        delivers before a widget is ever shown. Reading the width is always right.
        """
        return self.width() < self.COMPACT_BELOW

    def sizeHint(self) -> QSize:  # noqa: N802 — Qt override
        """Height follows the mode, which follows the width.

        Asked for rather than pushed: a layout queries this whenever it re-measures, so the
        widget never has to keep a cached height in step with a resize it might not have
        been told about yet.
        """
        return QSize(self.width(), self.HEIGHT_COMPACT if self._compact else self.HEIGHT_FULL)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 — Qt override
        """Only the height is a real minimum. The width is whatever it is given.

        This used to return :meth:`sizeHint` whole, which reports the *current* width — so
        the widget declared its present width as the least it could ever accept. Inside a
        resizable scroll area a content minimum can only ratchet upwards from there: the
        moment the vertical scrollbar claimed its ten pixels, the content no longer fitted
        its own stated minimum, a horizontal scrollbar appeared, and nothing could ever
        retire it. The timeline scales to any width; it has no minimum worth naming.
        """
        return QSize(0, self.sizeHint().height())

    def resizeEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        # Crossing the threshold changes the height the hints report, so the layout has to
        # be told to ask again.
        self.updateGeometry()
        super().resizeEvent(event)  # type: ignore[arg-type]

    @property
    def _bar(self) -> QRectF:
        top = 8.0 if self._compact else 34.0
        height = 22.0 if self._compact else 28.0
        return QRectF(0, top, float(self.width()), height)

    # --- painting ---------------------------------------------------------------------

    def paintEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = self._bar

        if self._start is None or self._end is None:
            self._paint_status(painter, bar)
            return

        self._paint_track(painter, bar)
        self._paint_breaks(painter, bar)
        self._paint_work(painter, bar)
        self._paint_markers(painter, bar)
        self._paint_punches(painter, bar)
        if not self._compact:
            self._paint_hours(painter, bar)

    def _paint_status(self, painter: QPainter, bar: QRectF) -> None:
        """One labelled band for a day that has no punches to draw."""
        colour = QColor(self._status_colour or self._palette.border)
        path = QPainterPath()
        path.addRoundedRect(bar, 6, 6)
        painter.fillPath(path, QColor(colour.red(), colour.green(), colour.blue(), 60))
        painter.setPen(QPen(colour, 1))
        painter.drawPath(path)

        painter.setPen(
            QColor(self._palette.text if self._status_label else self._palette.text_faint)
        )
        painter.drawText(
            bar, Qt.AlignmentFlag.AlignCenter, self._status_label or "No punches recorded"
        )

    def _x_for(self, moment: datetime) -> float:
        assert self._start is not None and self._end is not None
        total = (self._end - self._start).total_seconds()
        fraction = (moment - self._start).total_seconds() / total
        return min(max(fraction, 0.0), 1.0) * self.width()

    def _paint_track(self, painter: QPainter, bar: QRectF) -> None:
        """A thin rule for the whole span. What a break looks like where no block covers it."""
        line = QRectF(0, bar.center().y() - 1.5, bar.width(), 3)
        path = QPainterPath()
        path.addRoundedRect(line, 1.5, 1.5)
        painter.fillPath(path, QColor(self._palette.border))

    def _paint_breaks(self, painter: QPainter, bar: QRectF) -> None:
        """Every gap between two work blocks, with its length written in it when it fits."""
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)

        for previous, following in zip(self._segments, self._segments[1:], strict=False):
            gap = _gap_between(previous, following)
            if gap.minutes <= 0:
                continue
            left, right = self._x_for(previous.end), self._x_for(following.start)
            span = QRectF(left, bar.center().y() - 1.5, right - left, 3)
            painter.fillPath(_rounded(span, 1.5), QColor(self._palette.rest))

            if right - left >= self.LABEL_FITS and not self._compact:
                # Above the In times, not level with them. At `bar.top() - 15` this shared a
                # band with the punch labels at `- 17`, so a gap just wide enough to earn a
                # duration wrote it straight through the In time at its right-hand edge.
                painter.setPen(QColor(self._palette.rest))
                painter.drawText(
                    QRectF(left, bar.top() - 31, right - left, 13),
                    Qt.AlignmentFlag.AlignCenter,
                    str(gap),
                )

    def _paint_work(self, painter: QPainter, bar: QRectF) -> None:
        """Solid blocks, each carrying its own duration when there is room to write it."""
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)

        for segment in self._segments:
            left = self._x_for(segment.start)
            width = max(2.0, self._x_for(segment.end) - left)
            block = QRectF(left, bar.top(), width, bar.height())
            painter.fillPath(_rounded(block, 4), QColor(self._palette.work))
            if segment.end_inferred:
                _hatch(painter, block, self._palette.surface)

            if width >= self.LABEL_FITS:
                painter.setPen(QColor(self._palette.surface))
                painter.drawText(block, Qt.AlignmentFlag.AlignCenter, str(segment.duration))

    def _paint_markers(self, painter: QPainter, bar: QRectF) -> None:
        """The finish line and where the day has got to — both named, not bare lines."""
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)

        markers = []
        if self._leave_at is not None:
            markers.append((self._leave_at, self._palette.good, "leave", True))
        if self._now is not None:
            markers.append((self._now, self._palette.text, "now", False))

        # "leave" and "now" are 80 px boxes drawn at whatever x they fall on, so the day
        # where you can leave *around now* — the one this screen exists for — wrote the two
        # words on top of each other. Laid out left to right like the punch times.
        occupied = -1e9
        for moment, colour, label, dashed in sorted(markers, key=lambda m: m[0]):
            pen = QPen(QColor(colour), 2 if dashed else 1)
            if dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            x = self._x_for(moment)
            painter.drawLine(int(x), int(bar.top() - 5), int(x), int(bar.bottom() + 5))
            if self._compact:
                continue

            width = painter.fontMetrics().horizontalAdvance(label)
            left = max(x - width / 2, occupied + LABEL_GAP)
            painter.setPen(QColor(colour))
            painter.drawText(QRectF(left, 0, width + 2, 12), Qt.AlignmentFlag.AlignLeft, label)
            occupied = left + width

    def _paint_punches(self, painter: QPainter, bar: QRectF) -> None:
        """A dot at every punch, with In times above the bar and Out times below it.

        Splitting by direction was half the answer. Labels used to be skipped whenever two
        fell within 22 px, so a ten-punch day lost exactly the times it most needed to show;
        In and Out alternate by nature, so giving each its own row stops *neighbours*
        competing. It does nothing for punches two apart in the sequence, which share a row —
        two segments starting a few minutes apart put two times in the same place, and at
        roughly three pixels per minute anything closer than a quarter of an hour is a smear.

        So each row is laid out rather than drawn where it falls: a label that would land on
        the previous one slides right until it clears, and is dropped only when sliding would
        take it so far from its own dot that it would be pointing at the wrong punch. The dot
        is always drawn — the exact position is never the thing that gets lost.
        """
        if self._compact:
            return
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)

        above: list[tuple[float, str]] = []
        below: list[tuple[float, str]] = []
        for segment in self._segments:
            self._dot(painter, bar, segment.start, above=True, inferred=False)
            self._dot(painter, bar, segment.end, above=False, inferred=segment.end_inferred)
            above.append((self._x_for(segment.start), _clock_short(segment.start)))
            if not segment.end_inferred:
                # No time was punched, so writing one would be inventing it.
                below.append((self._x_for(segment.end), _clock_short(segment.end)))

        painter.setPen(QColor(self._palette.text))
        self._row(painter, above, top=bar.top() - 17)
        self._row(painter, below, top=bar.bottom() + 4)

    def _dot(
        self, painter: QPainter, bar: QRectF, moment: datetime, *, above: bool, inferred: bool
    ) -> None:
        colour = QColor(self._palette.text_faint if inferred else self._palette.text)
        painter.setBrush(colour)
        painter.setPen(Qt.PenStyle.NoPen)
        edge = bar.top() - 3 if above else bar.bottom() - 2
        painter.drawEllipse(QRectF(self._x_for(moment) - 2.5, edge, 5, 5))

    def _row(self, painter: QPainter, labels: list[tuple[float, str]], *, top: float) -> None:
        """Draw one row of time labels, sliding each clear of the one before it."""
        metrics = painter.fontMetrics()
        occupied = -1e9
        for x, text in sorted(labels):
            width = metrics.horizontalAdvance(text)
            left = max(x - width / 2, occupied + LABEL_GAP)
            if left - (x - width / 2) > LABEL_DRIFT:
                # Sliding this far would park the time above a different punch, which is
                # worse than not writing it: the dot still says exactly when it happened.
                continue
            painter.drawText(QRectF(left, top, width + 2, 13), Qt.AlignmentFlag.AlignLeft, text)
            occupied = left + width

    def _paint_hours(self, painter: QPainter, bar: QRectF) -> None:
        """The hour axis — what makes it a clock rather than a bar."""
        assert self._start is not None and self._end is not None
        hours = int((self._end - self._start).total_seconds() // 3600)
        step = max(1, -(-hours // 8))

        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)

        for index in range(0, hours + 1, step):
            moment = self._start + timedelta(hours=index)
            x = self._x_for(moment)
            painter.setPen(QColor(self._palette.text_faint))
            painter.drawText(
                QRectF(x - 24, bar.bottom() + 18, 48, 14),
                Qt.AlignmentFlag.AlignCenter,
                _hour_label(moment),
            )


#: Bounds for a shared timeline domain. Wide enough that an ordinary office day never
#: touches the edges, narrow enough that one 3 AM deployment does not squash the other six
#: days into an inch.
DOMAIN_EARLIEST = 6
DOMAIN_LATEST = 23
#: Least span worth drawing. Below it the bands are wider than the day they describe.
DOMAIN_MIN_HOURS = 6

#: Clear space between two time labels sharing a row.
LABEL_GAP = 6.0
#: How far a label may slide from its own punch before it is dropped instead. Past this it
#: is sitting above a neighbour's dot, which reads as a wrong answer rather than a missing one.
LABEL_DRIFT = 18.0


def shared_domain(spans: Sequence[tuple[datetime, datetime]]) -> tuple[int, int]:
    """One clock-hour range covering every day given, for stacking timelines.

    Rounded outward to whole hours so the axis labels land on the hours themselves, and
    clamped, because the point of a shared scale is comparison and a single outlier that
    triples the span defeats it — the outlier still draws, just against the same ruler as
    everything else.
    """
    if not spans:
        return DOMAIN_EARLIEST, DOMAIN_EARLIEST + DOMAIN_MIN_HOURS

    first = min(start.hour for start, _ in spans)
    last = max(end.hour + (1 if end.minute or end.second else 0) for _, end in spans)
    first = max(DOMAIN_EARLIEST, min(first, DOMAIN_LATEST - DOMAIN_MIN_HOURS))
    last = min(DOMAIN_LATEST, max(last, first + DOMAIN_MIN_HOURS))
    return first, last


class HourAxis(QWidget):
    """The hour ruler for a column of :class:`DayTimeline` rows, drawn once beneath them.

    One axis per row would be six repetitions of the same numbers and, worse, would let each
    row carry its own scale without saying so. Drawn once under all of them, it is visibly
    one ruler — which is the claim the stacked rows are making.
    """

    HEIGHT = 18

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._domain = (DOMAIN_EARLIEST, DOMAIN_EARLIEST + DOMAIN_MIN_HOURS)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_domain(self, domain: tuple[int, int]) -> None:
        self._domain = domain
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        first, last = self._domain
        span = max(1, last - first)
        # One label every hour is unreadable at seven days' width; every other hour, or
        # every third for a long span, keeps them apart at any window size.
        step = 1 if span <= 8 else (2 if span <= 14 else 3)

        painter = QPainter(self)
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QColor(self._palette.text_faint))

        for hour in range(first, last + 1, step):
            x = (hour - first) / span * self.width()
            label = f"{(hour - 1) % 12 + 1}{'am' if hour < 12 else 'pm'}"
            box = QRectF(x - 20, 2, 40, self.HEIGHT - 4)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
        painter.end()


class DayJourney(QWidget):
    """The day's punches as a list of events, breaks included.

    Replaces a four-column table of In / Out / Duration / Note. That table had one row per
    *work segment*, so the gaps between them — the thing anyone scanning a punch log is
    actually looking for — were not rows at all; you had to subtract one row's In from the
    previous row's Out to find your own lunch. It also stranded three narrow time columns on
    the left and gave the whole remaining width to a Note column that was empty on almost
    every row.

    Here every event is a row in the order it happened, work and break alike, and a repaired
    punch is marked on its own row rather than in a column that exists only for it.
    """

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

    #: A gap at or under this is an ordinary break. Past it, the day has a hole in it and
    #: the row says so rather than calling four hours "Break".
    LONG_BREAK = 90

    def set_segments(self, segments: Sequence[WorkSegment]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

        self.setVisible(bool(segments))
        rows: list[_JourneyRow] = []
        previous: WorkSegment | None = None
        for segment in segments:
            if previous is not None:
                gap = _gap_between(previous, segment)
                if gap.minutes > 0:
                    long_break = gap.minutes > self.LONG_BREAK
                    rows.append(
                        _JourneyRow(
                            self._palette,
                            when=f"{_clock_short(previous.end)} – {_clock_short(segment.start)}",
                            title=f"{'Away' if long_break else 'Break'} · {gap}",
                            detail="",
                            colour=self._palette.bad if long_break else self._palette.rest,
                            primary=False,
                        )
                    )
            rows.append(
                _JourneyRow(
                    self._palette,
                    when=f"{_clock_short(segment.start)} – {_clock_short(segment.end)}",
                    title=f"Worked · {segment.duration}",
                    detail=(
                        "the out punch is missing, so this end was inferred"
                        if segment.end_inferred
                        else ""
                    ),
                    colour=self._palette.work,
                    muted=segment.end_inferred,
                )
            )
            previous = segment

        # The spine has to know where it ends, which is only knowable once every row exists.
        for index, row in enumerate(rows):
            row.set_position(first=index == 0, last=index == len(rows) - 1)
            self._layout.addWidget(row)


class _JourneyRow(QWidget):
    """One event on the day's spine: a marker, what it was, and when.

    Rows used to carry a short stripe each, separated by their own padding, so the day read
    as a list of unrelated lines rather than one continuous journey. The spine here is drawn
    edge to edge and the rows are stacked with no spacing, so the segments join up; the
    marker is filled for work and hollow for a break, which is the same distinction the
    colour makes, said twice for anyone who cannot rely on the colour.
    """

    GUTTER = 18
    DOT = 7

    def __init__(
        self,
        palette: Palette,
        *,
        when: str,
        title: str,
        detail: str,
        colour: str,
        muted: bool = False,
        primary: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self._colour = colour
        self._primary = primary
        self._first = False
        self._last = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(self.GUTTER, Space.TIGHT // 2, 0, Space.TIGHT // 2)
        layout.setSpacing(Space.ROW)

        stamp = QLabel(when)
        stamp.setFixedWidth(118)
        stamp.setStyleSheet(
            f"color: {palette.text if primary else palette.text_muted};"
            f" font-weight: {600 if primary else 400};"
            " font-variant-numeric: tabular-nums;"
        )
        layout.addWidget(stamp)

        heading = QLabel(title)
        heading.setStyleSheet(
            f"color: {colour}; font-weight: {600 if primary else 400};"
            f" font-size: {13 if primary else 12}px;"
        )
        layout.addWidget(heading)

        if detail:
            note = QLabel(detail)
            note.setObjectName("CardCaption")
            note.setWordWrap(True)
            layout.addWidget(note, 1)
        else:
            layout.addStretch(1)

        if muted:
            stamp.setStyleSheet(f"color: {palette.text_muted}; font-variant-numeric: tabular-nums;")

    def set_position(self, *, first: bool, last: bool) -> None:
        """Tell the row where it sits, so the spine stops at the ends instead of overhanging."""
        self._first = first
        self._last = last
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        centre = self.GUTTER / 2 - 2
        middle = self.height() / 2
        painter.setPen(QPen(QColor(self._palette.border), 2))
        painter.drawLine(
            QPointF(centre, 0 if not self._first else middle),
            QPointF(centre, self.height() if not self._last else middle),
        )

        radius = self.DOT / 2 if self._primary else self.DOT / 2 - 1
        painter.setPen(QPen(QColor(self._colour), 2))
        painter.setBrush(QColor(self._colour) if self._primary else QColor(self._palette.surface))
        painter.drawEllipse(QPointF(centre, middle), radius, radius)
        painter.end()


def _rounded(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _gap_between(previous: WorkSegment, following: WorkSegment) -> Duration:
    minutes = int((following.start - previous.end).total_seconds() // 60)
    return Duration(max(0, minutes))


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
        self._caption.setWordWrap(True)
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
    #: Floor and ceiling for a cell. Between them the calendar grows with the window rather
    #: than sitting at a fixed 222 px beside several hundred pixels of nothing.
    MIN_CELL = 28
    MAX_CELL = 54

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(4)
        for column in range(7):
            self._grid.setColumnStretch(column, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def set_days(
        self,
        days: Sequence[DayRollup],
        *,
        target: Duration,
        attention: set[date] | None = None,
        year: int | None = None,
        month: int | None = None,
        today: date | None = None,
    ) -> None:
        """Render one month — the *whole* month, not only the days that have happened.

        Rollups rather than raw grid rows, because a rollup's ``worked`` is comparable with
        the work target. Tinting by ``Tot. Hrs.`` instead compares a gross span against a
        net target, which made an ordinary nine-hour day render as 112% of an eight-hour one
        and turned the whole calendar green.

        Days the portal has no row for are drawn as empty outlines rather than left out.
        A calendar that stops at today is not a calendar, and on the second of the month it
        made two cells look like the entire month's worth of data.
        """
        self._clear()
        flagged = attention or set()

        for column, initial in enumerate(self.HEADINGS):
            heading = QLabel(initial)
            heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
            heading.setStyleSheet(f"color: {self._palette.text_faint}; font-size: 10px;")
            self._grid.addWidget(heading, 0, column)

        known = {entry.day: entry for entry in days}
        span = self._month_days(known, year, month)
        if not span:
            return

        # Row 0 is the weekday headings, so the calendar starts at row 1. Weeks are indexed
        # from the first day's own Monday, which is what keeps columns aligned when a month
        # does not begin on one.
        origin = span[0] - timedelta(days=span[0].weekday())
        cutoff = today or date.today()

        for when in span:
            week = (when - origin).days // 7
            entry = known.get(when)
            if entry is None:
                cell: QWidget = _FutureCell(when, self._palette, ahead=when > cutoff)
            else:
                heat = _HeatCell(entry, target, self._palette, flagged=when in flagged)
                heat.clicked.connect(lambda day=when: self.day_selected.emit(day))
                cell = heat
            self._grid.addWidget(cell, week + 1, when.weekday())

    @staticmethod
    def _month_days(
        known: dict[date, DayRollup], year: int | None, month: int | None
    ) -> list[date]:
        """Every date in the month, taken from the caller or inferred from the rollups."""
        if year is None or month is None:
            if not known:
                return []
            sample = min(known)
            year, month = sample.year, sample.month
        _, last = monthrange(year, month)
        return [date(year, month, number) for number in range(1, last + 1)]

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
        self.setMinimumSize(MonthHeatmap.MIN_CELL, MonthHeatmap.MIN_CELL)
        # Both dimensions. Capping only the height let seven stretch-1 Expanding columns
        # pull each cell to ~120px wide against 28 tall — a two-digit number adrift in a
        # letterbox, and a bigger void than the layout change that was meant to close one.
        self.setMaximumSize(MonthHeatmap.MAX_CELL, MonthHeatmap.MAX_CELL)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        fill, ink = _heat_colours(entry, target, palette)
        border = f"1px solid {palette.bad}" if flagged else "1px solid transparent"
        if entry.on_duty:
            # Outdoor duty has no hours to tint by, so without its own mark it renders as
            # the same grey as a day nobody worked — which is how a week away disappeared.
            border = f"1px dashed {palette.adjust}"
        self.setStyleSheet(
            f"background-color: {fill}; color: {ink}; border: {border};"
            f" border-radius: 6px; font-size: 11px;"
        )
        self.setToolTip(_heat_tooltip(entry, flagged))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _FutureCell(QLabel):
    """A day the portal holds nothing for — usually one that has not happened yet.

    Drawn rather than omitted so the calendar is always a whole month. Not clickable: there
    is nothing behind it, and a cell that responds to a click by doing nothing is worse than
    one that plainly cannot be clicked.
    """

    def __init__(
        self, when: date, palette: Palette, *, ahead: bool, parent: QWidget | None = None
    ) -> None:
        super().__init__(str(when.day), parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(MonthHeatmap.MIN_CELL, MonthHeatmap.MIN_CELL)
        # Both dimensions. Capping only the height let seven stretch-1 Expanding columns
        # pull each cell to ~120px wide against 28 tall — a two-digit number adrift in a
        # letterbox, and a bigger void than the layout change that was meant to close one.
        self.setMaximumSize(MonthHeatmap.MAX_CELL, MonthHeatmap.MAX_CELL)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            f"background: transparent; color: {palette.text_faint};"
            f" border: 1px solid {palette.border}; border-radius: 6px; font-size: 11px;"
        )
        self.setToolTip(
            f"{when:%A, %d %B} — not yet".replace(" 0", " ")
            if ahead
            else f"{when:%A, %d %B} — no record".replace(" 0", " ")
        )


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
    note = f"\n{entry.note}" if entry.note else ""
    if entry.worked.minutes <= 0:
        state = entry.status.value.replace("_", " ")
        if entry.on_duty:
            # The remark is the whole story on these days: it is the difference between
            # "nothing recorded" and "training in Bengaluru".
            return f"{label} — outdoor duty{note or ' (no swipes to measure)'}"
        return f"{label} — {state}{note}"

    body = f"{label} — {entry.worked} worked"
    if entry.on_duty:
        body += ", partly outdoor duty"
    body += note
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


class CommuteCard(QFrame):
    """When you get home, with its own Refresh.

    The button is **never disabled**, whatever state the card is in. A control that greys
    out to stop you spending an API call is a control that looks broken, and looking broken
    is what makes people click it repeatedly — the exact behaviour the guards behind it
    exist to absorb. It always answers; the service decides whether answering costs a call.
    """

    refresh_requested = Signal()
    setup_requested = Signal()

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._palette = palette

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        title = QLabel("HOME BY")
        title.setObjectName("CardTitle")
        head.addWidget(title)
        head.addStretch(1)

        self.refresh = QPushButton("Refresh")
        self.refresh.clicked.connect(self.refresh_requested)
        head.addWidget(self.refresh)

        self.setup = QPushButton("Set up")
        self.setup.setObjectName("Primary")
        self.setup.clicked.connect(self.setup_requested)
        head.addWidget(self.setup)
        layout.addLayout(head)

        self._value = QLabel(EMPTY_VALUE)
        self._value.setObjectName("CardValue")
        layout.addWidget(self._value)

        self._caption = QLabel()
        self._caption.setObjectName("CardCaption")
        self._caption.setWordWrap(True)
        layout.addWidget(self._caption)

    def show_view(self, view: CommuteView, *, now: datetime | None = None) -> None:
        from cerepulse.intelligence.commute import describe

        self.setup.setVisible(view.needs_setup)
        # Refresh is pointless before there is anything configured to refresh, but it is
        # hidden rather than disabled — a greyed-out control invites clicking at it.
        self.refresh.setVisible(not view.needs_setup)

        if view.arrival is None:
            self._value.setText(EMPTY_VALUE)
            self._caption.setText(view.message)
            return

        self._value.setText(_clock_label(view.arrival.clock))
        caption = describe(view.arrival, now=now)
        self._caption.setText(f"{caption} — {view.message}" if view.message else caption)


def _clock_label(when: time) -> str:
    """``7:34 PM``. Windows has no ``%-I``, so the leading zero comes off by hand."""
    return when.strftime("%I:%M %p").lstrip("0")


def _tint(colour: str, alpha: int) -> str:
    """The chip's own colour at low opacity, so the pill always suits its text."""
    value = QColor(colour)
    return f"rgba({value.red()}, {value.green()}, {value.blue()}, {alpha})"


def link_button(text: str, on_click: Callable[[], None]) -> QPushButton:
    button = QPushButton(text)
    button.clicked.connect(on_click)
    return button


#: Width of a step button. Wide enough that the glyph is not clipped by the stylesheet's
#: own padding, which is what made these render as blank squares at 30px.
STEP_BUTTON_WIDTH = 34


def step_button(glyph: str, tooltip: str, on_click: Callable[[], None]) -> QPushButton:
    """A square ◀ / ▶ stepper.

    Shared rather than repeated at four call sites because the thing that broke them is
    invisible locally: the width and the stylesheet's padding have to be chosen together,
    and setting one without knowing the other is how every one of them ended up with no
    room to draw in.
    """
    button = QPushButton(glyph)
    button.setObjectName("StepButton")
    button.setFixedWidth(STEP_BUTTON_WIDTH)
    button.setToolTip(tooltip)
    button.clicked.connect(on_click)
    return button
