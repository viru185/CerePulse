"""Shared widgets: cards, banners, insight chips, and the segmented day bar.

The segment bar is ninetofive's, repainted as a ``QWidget``. It is the one custom-painted
element because it carries information no stock widget does: the actual shape of the day —
where work happened, where breaks fell, which stretches were inferred from a missing punch.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta

from PySide6.QtCore import QRectF, Qt, Signal
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

from cerepulse.intelligence.insights import Insight, Severity
from cerepulse.intelligence.month import DayRollup
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


class StatusChip(QLabel):
    """A short state word on a tinted pill — present, pending, rejected.

    Coloured text alone was doing this job in three different screens, each with its own
    palette mapping. A chip reads as a state rather than as emphasis, and one definition
    means "approved" is the same green everywhere it appears.
    """

    def __init__(self, text: str, colour: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
