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
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetricsF,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from cerepulse.intelligence.insights import Action, Insight, Severity
from cerepulse.intelligence.month import DayRollup
from cerepulse.intelligence.next_action import NextAction
from cerepulse.intelligence.segments import WorkedGap, WorkSegment
from cerepulse.models.values import Duration
from cerepulse.ui.theme import Palette, Space

if TYPE_CHECKING:  # Imported for the annotation only; widgets must not depend on services.
    from cerepulse.services.commute import CommuteView

#: What a card shows when it has no number yet.
EMPTY_VALUE = "—"


def empty_layout(layout: QLayout) -> None:
    """Remove every widget from a layout, and stop it painting *now*.

    ``takeAt`` only detaches the layout item — the widget stays a child of the container and
    keeps drawing at its last geometry until ``deleteLater`` is serviced, which happens when
    the event loop next drains and not before. The records timeline painted four stacked
    copies of every row that way, and the attendance heatmap a stale block of cells over its
    own headings after every re-render. Reparenting first makes the removal immediate.
    """
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget() if item is not None else None
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


def add_reveal_toggle(field: QLineEdit, *, colour: str = "#8A8F98") -> QAction:
    """Put an eye at the end of a masked field that shows what was typed while it is held on.

    Off by default and never remembered: the point is checking a password or a key you have
    just typed, on a screen that may be sitting in an office. The icons are painted rather
    than shipped — the app has no glyph set, and two sixteen-pixel drawings are cheaper than
    adding one. ``colour`` defaults to a grey that reads on every palette, because the sign-in
    dialog is built before a palette reaches it.
    """
    action = QAction(_eye_icon(colour, crossed=False), "Show", field)
    action.setCheckable(True)
    action.setToolTip("Show what you typed")
    field.addAction(action, QLineEdit.ActionPosition.TrailingPosition)

    def toggle(shown: bool) -> None:
        field.setEchoMode(QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password)
        action.setIcon(_eye_icon(colour, crossed=shown))
        action.setText("Hide" if shown else "Show")
        action.setToolTip("Hide it again" if shown else "Show what you typed")

    action.toggled.connect(toggle)
    return action


def _eye_icon(colour: str, *, crossed: bool) -> QIcon:
    """A sixteen-pixel eye, struck through when the field is being shown.

    Drawn once per common pixel ratio, so Qt picks a sharp one at 125%, 150% and 175% rather
    than stretching a 16-pixel bitmap.
    """
    icon = QIcon()
    for ratio in (1.0, 1.25, 1.5, 1.75, 2.0, 3.0):
        icon.addPixmap(_eye_pixmap(colour, crossed=crossed, ratio=ratio))
    return icon


def _eye_pixmap(colour: str, *, crossed: bool, ratio: float) -> QPixmap:
    side = round(16 * ratio)
    pixmap = QPixmap(side, side)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(colour), 1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    outline = QPainterPath()
    outline.moveTo(1.5, 8)
    outline.cubicTo(4, 3.5, 12, 3.5, 14.5, 8)
    outline.cubicTo(12, 12.5, 4, 12.5, 1.5, 8)
    painter.drawPath(outline)
    painter.setBrush(QColor(colour))
    painter.drawEllipse(QRectF(5.75, 5.75, 4.5, 4.5))
    if crossed:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(3, 13), QPointF(13, 3))
    painter.end()
    return pixmap


def cover_pixmap(source: QPixmap, frame: QSize, ratio: float) -> QPixmap:
    """``source`` cropped to cover ``frame`` logical pixels, drawn at ``ratio`` device pixels.

    Scaling to the logical size and letting Qt stretch it is what made a picture soft at
    175%: 180 pixels of image spread over 315 of screen.
    """
    target = QSize(round(frame.width() * ratio), round(frame.height() * ratio))
    scaled = source.scaled(
        target,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = (scaled.width() - target.width()) // 2
    y = (scaled.height() - target.height()) // 2
    cropped = scaled.copy(x, y, target.width(), target.height())
    cropped.setDevicePixelRatio(ratio)
    return cropped


class DailyTile(QWidget):
    """The sidebar's picture and quote of the day.

    Lives in the one region of the window that was empty — between the navigation and the
    portal button — so it is visible from every screen without displacing anything the
    screens are for. The picture is cropped to cover a 16:9 frame as wide as the sidebar gives
    it, rendered at the screen's pixel ratio so it stays sharp at 175%; the quote wraps
    beneath it in the tagline's style. Nothing else: the picture's title and copyright are its
    tooltip and the viewer's caption, and the providers' credits live in Settings and
    About, so the sidebar carries the picture and the words and not the paperwork.
    Clicking the picture opens the viewer.
    """

    clicked = Signal()
    FRAME = QSize(180, 101)

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._pixmap: QPixmap | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(4)

        self._image = QLabel()
        # A floor rather than a fixed size: a fixed 180 px would hold the sidebar's content
        # wider than its viewport once the scrollbar takes its 10 px.
        self._image.setMinimumSize(1, self.FRAME.height())
        self._image.setMaximumSize(self.FRAME)
        self._rendered: QSize | None = None
        self._image.setObjectName("DailyPicture")
        self._image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image.setCursor(Qt.CursorShape.PointingHandCursor)
        self._image.setToolTip("Picture of the day — click for the full picture")
        self._image.setVisible(False)
        layout.addWidget(self._image)

        self._quote = QLabel()
        self._quote.setObjectName("SidebarTagline")
        self._quote.setWordWrap(True)
        self._quote.setVisible(False)
        layout.addWidget(self._quote)

    def show_day(
        self, quote_text: str, quote_author: str, picture: Path | None, caption: str = ""
    ) -> None:
        """``caption`` is the picture's title and copyright, said on hover."""
        self._pixmap = QPixmap(str(picture)) if picture is not None else None
        if self._pixmap is not None and self._pixmap.isNull():
            self._pixmap = None
        self._render_picture()
        self._image.setVisible(self._pixmap is not None)
        self._image.setToolTip(
            f"{caption}\nClick for the full picture" if caption else "Click for the full picture"
        )

        if quote_text:
            author = f" — {quote_author}" if quote_author else ""
            self._quote.setText(f"“{quote_text}”{author}")
        self._quote.setVisible(bool(quote_text))

        self.setVisible(self._pixmap is not None or bool(quote_text))

    def _frame(self) -> QSize:
        """The picture's size: the tile's width, up to :attr:`FRAME`, at 16:9."""
        room = self.contentsRect().width() if self.width() > 0 else self.FRAME.width()
        width = max(80, min(self.FRAME.width(), room))
        return QSize(width, round(width * self.FRAME.height() / self.FRAME.width()))

    def _render_picture(self) -> None:
        frame = self._frame()
        self._rendered = frame
        self._image.setMinimumSize(1, frame.height())
        self._image.setMaximumSize(frame)
        if self._pixmap is None:
            return
        self._image.setPixmap(cover_pixmap(self._pixmap, frame, self.devicePixelRatioF() or 1.0))

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 — Qt override
        super().resizeEvent(event)
        if self._rendered != self._frame():
            self._render_picture()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt override
        if event.button() == Qt.MouseButton.LeftButton and self._image.isVisible():
            self.clicked.emit()
        super().mousePressEvent(event)


class FlowLayout(QLayout):
    """Lays its items out left to right and wraps them onto the next line when it runs out.

    For rows of buttons whose total width is set by their labels — and so by the font and the
    display scale — rather than by the window: Settings' jump list needed 1,050 px in one
    line, which pushed the whole page sideways off a laptop at 175%.
    """

    def __init__(self, parent: QWidget | None = None, *, spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 — Qt override
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 — Qt override
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 — Qt override
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 — Qt override
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 — Qt override
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 — Qt override
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 — Qt override
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802 — Qt override
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 — Qt override
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            if x > area.x() and x + hint.width() > area.right() + 1:
                x, y, line = area.x(), y + line + self._gap, 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._gap
            line = max(line, hint.height())
        return y + line - rect.y() + margins.bottom()


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

        # Parented now, not when the layout adopts it: shown while parentless, a label is a
        # top-level window for that moment, and five of them flashed open at every launch.
        self._caption = QLabel(caption, self)
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

    def add_detail(self, widget: QWidget) -> None:
        """Append a widget below the caption — a per-item list under a headline figure."""
        layout = self.layout()
        if layout is not None:
            layout.addWidget(widget)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt override
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class Toast(QFrame):
    """A short note that something was done, which fades away by itself.

    The app had no way to say "done": a Settings save changed one line of text beside the
    Save button (the same text every time, so a second save looked like nothing happened),
    and a history sync started from Insights reported to a banner on the Settings screen.
    Banners are for conditions that last; this is for events.

    Opaque on purpose: over a wallpaper the plain widget background is transparent, and a
    note drawn straight on a photograph is unreadable.
    """

    closed = Signal()

    #: Milliseconds on screen before fading, by severity: a warning gets longer to be read.
    HOLD = {
        Severity.SUCCESS: 3000,
        Severity.INFO: 3000,
        Severity.WARNING: 5500,
        Severity.CRITICAL: 7000,
    }
    FADE_IN = 150
    FADE_OUT = 450

    def __init__(
        self, text: str, severity: Severity, palette: Palette, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.severity = severity
        self.text = text
        self._palette = palette
        self._label = QLabel(text, self)
        # Portal text reaches these, and it is not trusted markup.
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._label.setWordWrap(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.GAP, Space.ROW, Space.GAP, Space.ROW)
        layout.addWidget(self._label)
        self._margins = layout
        self.setMaximumWidth(TOAST_WIDTH)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        # Connected once; `_gone` checks whether this was a fade *out*.
        self._fade.finished.connect(self._gone)
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self.dismiss)
        self._leaving = False

    def fit_to_text(self) -> QSize:
        """Size to the message: as wide as its text, up to :data:`TOAST_WIDTH`, then wrapped.

        Done by hand because a layout's size hint for a word-wrapped label is a guess made
        before the width is known — the first version trusted it and drew three empty
        slivers in the corner.
        """
        margins = self._margins.contentsMargins()
        side = margins.left() + margins.right()
        frame = 6  # the 4px accent edge and the 1px border opposite it
        natural = self._label.fontMetrics().horizontalAdvance(self.text) + 4
        width = min(TOAST_WIDTH, natural + side + frame)
        inner = width - side - frame
        height = self._label.heightForWidth(inner)
        if height <= 0:
            height = self._label.fontMetrics().height()
        size = QSize(width, height + margins.top() + margins.bottom() + 2)
        self.setFixedSize(size)
        return size

    def show_for_a_while(self) -> None:
        self._animate(1.0, self.FADE_IN)
        self._hold.start(self.HOLD.get(self.severity, 3000))

    def again(self) -> None:
        """The same message once more: keep this one up rather than stacking a copy."""
        if self._leaving:
            self._leaving = False
            self._fade.stop()
            self._animate(1.0, self.FADE_IN)
        self._hold.start(self.HOLD.get(self.severity, 3000))

    def dismiss(self) -> None:
        if self._leaving:
            return
        self._leaving = True
        self._hold.stop()
        self._animate(0.0, self.FADE_OUT)

    def _animate(self, to: float, duration: int) -> None:
        self._fade.stop()
        self._fade.setDuration(duration)
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(to)
        self._fade.start()

    def _gone(self) -> None:
        if self._leaving and self._effect.opacity() <= 0.0:
            self.closed.emit()

    def enterEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        # Hovering holds it, so a note being read does not fade out from under the pointer.
        self._hold.stop()
        super().enterEvent(event)  # type: ignore[arg-type]

    def leaveEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        if not self._leaving:
            self._hold.start(1500)
        super().leaveEvent(event)  # type: ignore[arg-type]

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt override
        self.dismiss()
        super().mouseReleaseEvent(event)

    @property
    def _palette(self) -> Palette:
        return self._theme

    @_palette.setter
    def _palette(self, palette: Palette) -> None:
        # A property, because the theme walk in `_apply_theme` only assigns `_palette`: a
        # toast already on screen restyles the moment it is handed the new one.
        self._theme = palette
        self._restyle()

    def _restyle(self) -> None:
        palette = self._theme
        edge = {
            Severity.SUCCESS: palette.good,
            Severity.INFO: palette.work,
            Severity.WARNING: palette.rest,
            Severity.CRITICAL: palette.bad,
        }.get(self.severity, palette.work)
        self.setStyleSheet(
            f"#Toast {{ background-color: {palette.elevated}; border: 1px solid {palette.border};"
            f" border-left: 4px solid {edge}; border-radius: 10px; }}"
            f"#Toast QLabel {{ color: {palette.text}; background: transparent; }}"
        )


class ToastStack(QWidget):
    """Where toasts appear: the bottom-right of the window, newest at the bottom, three at most.

    Positioned by hand over the window's content rather than placed in a layout, because it
    floats above whatever screen is showing. It follows the parent's size through an event
    filter, so it never needs telling about a resize.
    """

    MAX = 3
    MARGIN = 20

    def __init__(self, palette: Palette, parent: QWidget) -> None:
        super().__init__(parent)
        self._palette = palette
        self._toasts: list[Toast] = []
        parent.installEventFilter(self)
        self.hide()

    @property
    def messages(self) -> list[str]:
        """What is on screen now, oldest first. For tests and for anyone reading it aloud."""
        return [toast.text for toast in self._toasts if not toast._leaving]

    def show_message(self, text: str, severity: Severity = Severity.SUCCESS) -> None:
        for toast in self._toasts:
            if toast.text == text and toast.severity is severity:
                toast.again()
                return
        while len(self._toasts) >= self.MAX:
            self._drop(self._toasts[0])
        toast = Toast(text, severity, self._palette, self)
        toast.closed.connect(lambda t=toast: self._drop(t))
        self._toasts.append(toast)
        toast.show()
        self.show()
        self.raise_()
        self._place()
        toast.show_for_a_while()

    def _drop(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        try:
            toast.setParent(None)
            toast.deleteLater()
            if not self._toasts:
                self.hide()
            else:
                self._place()
        except RuntimeError:
            # A fade that finished while the window was being torn down: the layout went
            # first, and there is nothing left to tidy.
            return

    def _place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        sizes = [toast.fit_to_text() for toast in self._toasts]
        if not sizes:
            return
        gap = Space.SNUG
        width = max(size.width() for size in sizes)
        height = sum(size.height() for size in sizes) + gap * (len(sizes) - 1)
        self.setGeometry(
            parent.width() - width - self.MARGIN,
            parent.height() - height - self.MARGIN,
            width,
            height,
        )
        # Newest at the bottom, each right-aligned: the corner stays put as notes come and go.
        top = 0
        for toast, size in zip(self._toasts, sizes, strict=True):
            toast.setGeometry(width - size.width(), top, size.width(), size.height())
            top += size.height() + gap

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 — Qt override
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._place()
        return False


#: Widest a toast grows before its text wraps.
TOAST_WIDTH = 380


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
        empty_layout(self._layout)

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
    #: A lane has no hour labels to lose, and its punch times give way to each other one by
    #: one, so it keeps them down to a much narrower column — on Week at the window's
    #: minimum size they are most of what the lane is for.
    COMPACT_LANE_BELOW = 280

    HEIGHT_FULL = 96
    HEIGHT_COMPACT = 54
    #: A lane has no hour labels of its own, so it is the full height less that row.
    HEIGHT_LANE = 82
    #: A lane with no punches: its band, and room either side.
    HEIGHT_QUIET = 44
    #: Minimum axis span. A day two hours old would otherwise be drawn at absurd magnification.
    MIN_HOURS = 5
    #: Narrower than this and a label written inside a block would be clipped.
    LABEL_FITS = 48
    #: Clock time kept clear before the first punch and after the last moment drawn.
    LEAD = timedelta(minutes=30)

    def __init__(
        self, palette: Palette, parent: QWidget | None = None, *, lane: bool = False
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        #: One day in a stack of them on one clock, as on Week. The stack has one ruler
        #: under it, so a lane draws faint hour lines instead of its own labels, and today's
        #: finish is the stretch still to go rather than a marker over the bar.
        self._lane = lane
        self._domain: tuple[int, int] | None = None
        #: The last block is still being worked: its end is "now", not a missing punch.
        self._ongoing = False
        self._segments: tuple[WorkSegment, ...] = ()
        self._leave_at: datetime | None = None
        #: The finish time before an over-long break pushed it out. Drawn fainter, so the
        #: eye still lands on the one that counts.
        self._flat_at: datetime | None = None
        self._now: datetime | None = None
        self._start: datetime | None = None
        self._end: datetime | None = None
        self._status_label = ""
        self._status_colour: str | None = None
        #: Gaps the user reclassified as work, with the extent each one covers. Drawn as a
        #: band so a stretch that reads as continuous does not look like an unbroken measured
        #: run — a dotted line at the start could only say "something happened here".
        self._worked_spans: tuple[WorkedGap, ...] = ()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_day(
        self,
        segments: tuple[WorkSegment, ...],
        *,
        leave_at: datetime | None = None,
        flat_at: datetime | None = None,
        now: datetime | None = None,
        status_label: str = "",
        status_colour: str | None = None,
        domain: tuple[int, int] | None = None,
        worked_spans: Sequence[WorkedGap] = (),
        ongoing: bool = False,
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

        ``ongoing`` says the last block is still being worked, so its end is labelled "now"
        rather than left blank as a missing punch.
        """
        self._segments = segments
        self._domain = domain
        self._ongoing = ongoing
        self._worked_spans = tuple(worked_spans)
        # A lane's height follows whether it has punches to draw.
        self.updateGeometry()
        self._leave_at = leave_at
        self._flat_at = flat_at
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
            # Room on both sides. The axis used to start on the hour of the first punch, so
            # a 9:03 arrival was drawn three minutes — four pixels — from the left edge while
            # the right had up to an hour of air. Half an hour of lead, then the hour; the
            # same at the end, which also has to reach the muted break-counted finish.
            moments = (leave_at, flat_at, now)
            latest = max([segments[-1].end] + [m for m in moments if m is not None])
            start = _floor_hour(segments[0].start - self.LEAD)
            end = _ceil_hour(latest + self.LEAD)
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
            marks = [
                name
                for name, flagged in (
                    ("start inferred", segment.start_inferred),
                    ("end inferred", segment.end_inferred),
                )
                if flagged
            ]
            lines.append(
                f"{_clock_short(segment.start)} – {_clock_short(segment.end)}"
                f"  worked {segment.duration}" + ("  · " + ", ".join(marks) if marks else "")
            )
            # The notes belong here, not only in the journey rows. The band says a stretch was
            # reclaimed; only the user's own words say what it was.
            for span in self._worked_spans:
                if segment.start <= span.start and span.end <= segment.end:
                    lines.append(
                        f"    {_clock_short(span.start)} – {_clock_short(span.end)}"
                        f"  marked as work: {span.note or 'no note'}"
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
        return self.width() < (self.COMPACT_LANE_BELOW if self._lane else self.COMPACT_BELOW)

    def sizeHint(self) -> QSize:  # noqa: N802 — Qt override
        """Height follows the mode, which follows the width.

        Asked for rather than pushed: a layout queries this whenever it re-measures, so the
        widget never has to keep a cached height in step with a resize it might not have
        been told about yet.
        """
        if self._compact:
            return QSize(self.width(), self.HEIGHT_COMPACT)
        if self._lane:
            return QSize(self.width(), self.HEIGHT_LANE if self._segments else self.HEIGHT_QUIET)
        return QSize(self.width(), self.HEIGHT_FULL)

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
    def _inset(self) -> float:
        """Side margin: room for half a time label, so the first and last fit inside.

        A lane keeps it even when compact, because it has to line up with the one ruler
        drawn under the stack — which is a claim about the scale, not about room.
        """
        return 4.0 if self._compact and not self._lane else timeline_inset(self.font())

    @property
    def _bar(self) -> QRectF:
        # A lane with nothing to draw has no times above or below its band, so it gives up
        # their rows: a week of leave should not stand as tall as the days that were worked.
        quiet = self._compact or (self._lane and not self._segments)
        top = 8.0 if quiet else 34.0
        height = 22.0 if self._compact else 28.0
        inset = self._inset
        return QRectF(inset, top, max(0.0, self.width() - 2 * inset), height)

    # --- painting ---------------------------------------------------------------------

    def paintEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = self._bar
        if self._lane:
            self._paint_grid(painter, bar)

        if self._start is None or self._end is None:
            self._paint_status(painter, bar)
            return

        self._paint_track(painter, bar)
        self._paint_breaks(painter, bar)
        if self._lane:
            self._paint_ahead(painter, bar)
        self._paint_work(painter, bar)
        if not self._lane:
            self._paint_markers(painter, bar)
        self._paint_adjustments(painter, bar)
        self._paint_punches(painter, bar)
        if not self._compact and not self._lane:
            self._paint_hours(painter, bar)

    def _paint_grid(self, painter: QPainter, bar: QRectF) -> None:
        """Faint hour lines at the ruler's own ticks, so a lane reads against the one axis.

        Drawn from the domain rather than from the day, so a day with no punches — leave, a
        holiday — still carries the lines and the stack reads as one chart.
        """
        if self._domain is None:
            return
        first, last = self._domain
        span = max(1, last - first)
        painter.save()
        painter.setPen(QPen(QColor(self._palette.border), 1))
        for hour in range(first, last + 1, axis_step(span)):
            x = round(bar.left() + (hour - first) / span * bar.width()) + 0.5
            painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        painter.restore()

    def _paint_ahead(self, painter: QPainter, bar: QRectF) -> None:
        """Today's finish as the stretch still to go: a dashed block up to the leave time.

        The same shape as the work beside it, outlined rather than filled, because it is
        the work that has not happened yet — a marker line said *when* but not *how much*.
        """
        if self._leave_at is None or not self._segments:
            return
        start = self._segments[-1].end
        if self._leave_at <= start:
            return
        left, right = self._x_for(start), self._x_for(self._leave_at)
        if right - left < 2:
            return
        block = QRectF(left, bar.top(), right - left, bar.height()).adjusted(1, 1, -1, -1)
        pen = QPen(QColor(self._palette.work), 1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.save()
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(block, 4, 4)
        painter.restore()

    def _paint_status(self, painter: QPainter, bar: QRectF) -> None:
        """One labelled band for a day that has no punches to draw."""
        colour = QColor(self._status_colour or self._palette.border)
        path = QPainterPath()
        path.addRoundedRect(bar, 6, 6)
        # A lane is one of seven, so its band is quieter: the stack is about the days that
        # have a shape, and a week of leave should not out-shout them.
        painter.fillPath(
            path, QColor(colour.red(), colour.green(), colour.blue(), 34 if self._lane else 60)
        )
        if not self._lane:
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
        bar = self._bar
        return bar.left() + min(max(fraction, 0.0), 1.0) * bar.width()

    def _paint_track(self, painter: QPainter, bar: QRectF) -> None:
        """A thin rule for the whole span. What a break looks like where no block covers it."""
        line = QRectF(bar.left(), bar.center().y() - 1.5, bar.width(), 3)
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
            # Either end can be reconstructed now that an approved swipe request can move the
            # arrival, and a repaired block has to look repaired whichever end it was.
            if segment.end_inferred or segment.start_inferred:
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
        if self._flat_at is not None and self._flat_at != self._leave_at:
            # Where the day would have ended without the extra break — the figure the
            # adjusted line silently absorbed. Muted, so "leave" stays the answer.
            markers.append((self._flat_at, self._palette.text_muted, "+break", True))
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
            left = self._inside(max(x - width / 2, occupied + LABEL_GAP), width)
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
        for index, segment in enumerate(self._segments):
            still_going = self._ongoing and index == len(self._segments) - 1
            self._dot(painter, bar, segment.start, above=True, inferred=False)
            self._dot(painter, bar, segment.end, above=False, inferred=segment.end_inferred)
            above.append((self._x_for(segment.start), _clock_short(segment.start)))
            if still_going:
                below.append((self._x_for(segment.end), f"now {_clock_short(segment.end)}"))
            elif not segment.end_inferred:
                # No time was punched, so writing one would be inventing it.
                below.append((self._x_for(segment.end), _clock_short(segment.end)))
        if self._lane and self._leave_at is not None and self._segments:
            if self._leave_at > self._segments[-1].end:
                below.append((self._x_for(self._leave_at), f"leave {_clock_short(self._leave_at)}"))

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
            left = self._inside(max(x - width / 2, occupied + LABEL_GAP), width)
            if abs(left - (x - width / 2)) > LABEL_DRIFT:
                # Sliding this far would park the time above a different punch, which is
                # worse than not writing it: the dot still says exactly when it happened.
                continue
            painter.drawText(QRectF(left, top, width + 2, 13), Qt.AlignmentFlag.AlignLeft, text)
            occupied = left + width

    def _inside(self, left: float, width: float) -> float:
        """A label's left edge, moved just enough to keep the whole label on the widget."""
        return min(max(0.0, left), self.width() - width - 2)

    def _paint_adjustments(self, painter: QPainter, bar: QRectF) -> None:
        """Draw each stretch the user reclaimed from a break, over the work block it joined.

        Merging the segments is what makes the figures right; drawing nothing there is what
        would make them look measured. This used to be a dotted line at the gap's start,
        because the merge threw the gap's extent away before the widget ever saw it — so the
        one thing the mark could not show was how much of the block had been reclaimed, which
        is the whole question. The violet is the palette's "an adjustment" accent, carrying
        the meaning it carries everywhere else.
        """
        if not self._worked_spans or self._start is None or self._end is None:
            return

        for span in self._worked_spans:
            if span.end <= self._start or span.start >= self._end:
                continue
            left = self._x_for(span.start)
            width = max(2.0, self._x_for(span.end) - left)
            band = QRectF(left, bar.top(), width, bar.height())
            painter.fillPath(_rounded(band, 2), QColor(self._palette.adjust))
            # Hatched as well as coloured: this is work nobody measured, and it should read
            # like the other reconstructions on the bar rather than like a solid block.
            _hatch(painter, band, self._palette.surface)

    def _paint_hours(self, painter: QPainter, bar: QRectF) -> None:
        """The hour axis — what makes it a clock rather than a bar."""
        assert self._start is not None and self._end is not None
        hours = int((self._end - self._start).total_seconds() // 3600)
        step = max(1, -(-hours // 8))

        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)

        painter.setPen(QColor(self._palette.text_faint))
        for index in range(0, hours + 1, step):
            moment = self._start + timedelta(hours=index)
            x = self._x_for(moment)
            # Centred on the tick, but never outside the widget. The rect used to be a flat
            # `x - 24` wide 48, so the first label began 24px off the left edge and drew as
            # "m" instead of "9am", and the last ran 24px past the right and lost its "m"
            # too. Both ends of every timeline on Today and Week were clipped.
            left = min(max(0.0, x - HOUR_LABEL_WIDTH / 2), self.width() - HOUR_LABEL_WIDTH)
            painter.drawText(
                QRectF(left, bar.bottom() + 18, HOUR_LABEL_WIDTH, 14),
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

#: Box an hour label is drawn in. Wide enough for "12pm" at 8pt with room to spare.
HOUR_LABEL_WIDTH = 48.0

#: Clear space between two time labels sharing a row.
LABEL_GAP = 6.0
#: How far a label may slide from its own punch before it is dropped instead. Past this it
#: is sitting above a neighbour's dot, which reads as a wrong answer rather than a missing one.
LABEL_DRIFT = 18.0


def axis_step(span: int) -> int:
    """Hours between labelled ticks on a shared ruler.

    One label every hour is unreadable at seven days' width; every other hour, or every
    third for a long span, keeps them apart at any window size. Shared, because the hour
    lines in each lane have to fall exactly on the ruler's ticks.
    """
    return 1 if span <= 8 else (2 if span <= 14 else 3)


def timeline_inset(font: QFont) -> float:
    """The side margin every clock-axis widget keeps: half a time label, plus air.

    Shared because a column of timelines and the ruler under them must agree to the pixel.
    """
    sized = QFont(font)
    sized.setPointSize(8)
    return QFontMetricsF(sized).horizontalAdvance("12:59p") / 2 + 4


def _floor_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def _ceil_hour(moment: datetime) -> datetime:
    floor = _floor_hour(moment)
    return floor if floor == moment else floor + timedelta(hours=1)


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
        step = axis_step(span)

        painter = QPainter(self)
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QColor(self._palette.text_faint))

        # The same inset as the bars above, or the ruler would not line up with what it
        # measures; and every label kept on the widget — the first and last used to hang
        # half off either end, reading "m" and "7p".
        inset = timeline_inset(self.font())
        usable = max(0.0, self.width() - 2 * inset)
        for hour in range(first, last + 1, step):
            x = inset + (hour - first) / span * usable
            label = f"{(hour - 1) % 12 + 1}{'am' if hour < 12 else 'pm'}"
            left = min(max(0.0, x - 20), self.width() - 40.0)
            box = QRectF(left, 2, 40, self.HEIGHT - 4)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
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
        self._values: tuple[str, ...] = ()
        self.setMinimumHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_bars(
        self,
        bars: Sequence[tuple[str, float, str]],
        *,
        reference: float | None = None,
        reference_label: str = "",
        value_labels: Sequence[str] | None = None,
    ) -> None:
        """``bars`` is (label, value, tooltip-ish caption) in display order.

        ``value_labels`` puts the figure on the bar itself. The values used to live only in
        the tooltip, which on a chart that is scanned rather than hovered is the same as
        not being there — the stated complaint was a chart of unlabeled heights.
        """
        self._bars = tuple(bars)
        self._reference = reference
        self._reference_label = reference_label
        self._values = tuple(value_labels) if value_labels else ()
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
        if self._values:
            # Headroom for the value labels, so the tallest bar's figure is not pushed off
            # the top of the widget and silently dropped.
            ceiling *= 1.18

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

            if index < len(self._values):
                # Above the bar when there is headroom, inside its top when there is not —
                # a label pushed off the top edge of the widget is no label at all.
                if bar.top() >= 14:
                    painter.setPen(QColor(self._palette.text_muted))
                    painter.drawText(
                        QRectF(index * slot, bar.top() - 14, slot, 12),
                        Qt.AlignmentFlag.AlignCenter,
                        self._values[index],
                    )
                elif height >= 16:
                    painter.setPen(QColor(self._palette.surface))
                    painter.drawText(
                        QRectF(index * slot, bar.top() + 2, slot, 12),
                        Qt.AlignmentFlag.AlignCenter,
                        self._values[index],
                    )

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
        empty_layout(self._grid)


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
        self._detail = QLabel(detail, self)  # parented first; see Card
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
    # Equal shares, so a card with a long caption does not take its neighbours' width and
    # leave one of them a column of three-word lines.
    for card in cards:
        layout.addWidget(card, 1)
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
    #: The departure basis moved — the window recomputes with the same guards.
    departure_changed = Signal()

    #: What "leaving at" means. Data on the combo, so render code never matches labels.
    DEPARTURES = (
        ("When target met", "target"),
        ("Now", "now"),
        ("At…", "custom"),
    )

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        from cerepulse.ui import kit

        super().__init__(parent)
        self.setProperty("variant", "card")
        self._palette = palette

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.GAP, 14, Space.GAP, 14)
        layout.setSpacing(2)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(Space.SNUG)
        head.addWidget(kit.Text("Home by", self, role="caption", tone="muted"))
        head.addStretch(1)

        # "How long if I leave right now?" is a different question from "when am I home if
        # I work my target?", and both are fair. The choice only changes which departure
        # the service is asked about — every guard behind it still applies.
        self.departure = kit.ComboBox()
        for label, key in self.DEPARTURES:
            self.departure.addItem(label, key)
        self.departure.currentIndexChanged.connect(lambda _index: self._departure_moved())
        head.addWidget(self.departure)

        self.at_time = kit.TimeEdit()
        self.at_time.setVisible(False)
        self.at_time.editingFinished.connect(self.departure_changed)
        head.addWidget(self.at_time)

        self.refresh = kit.Button("Refresh", self, small=True, on_click=self.refresh_requested.emit)
        head.addWidget(self.refresh)

        self.setup = kit.Button(
            "Set up", self, variant="primary", small=True, on_click=self.setup_requested.emit
        )
        head.addWidget(self.setup)
        layout.addLayout(head)

        self._value = kit.Text(EMPTY_VALUE, self, role="title")
        layout.addWidget(self._value)

        self._caption = kit.Text(role="caption", tone="muted", wrap=True)
        layout.addWidget(self._caption)

    def _departure_moved(self) -> None:
        self.at_time.setVisible(self.departure.currentData() == "custom")
        self.departure_changed.emit()

    def departure_basis(self) -> str:
        """``target`` | ``now`` | ``custom`` — what "leaving at" currently means."""
        return str(self.departure.currentData() or "target")

    def custom_departure(self) -> time:
        picked = self.at_time.time()
        return time(picked.hour(), picked.minute())

    def show_view(self, view: CommuteView, *, now: datetime | None = None) -> None:
        from cerepulse.intelligence.commute import describe

        self.setup.setVisible(view.needs_setup)
        # Refresh is pointless before there is anything configured to refresh, but it is
        # hidden rather than disabled — a greyed-out control invites clicking at it.
        self.refresh.setVisible(not view.needs_setup)
        self.departure.setVisible(not view.needs_setup)
        self.at_time.setVisible(not view.needs_setup and self.departure_basis() == "custom")

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
