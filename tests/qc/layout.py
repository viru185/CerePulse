"""What a person checks a screenshot for, checked by code instead.

Three defects account for nearly everything that makes a screen look broken at another scale:
something wider than the window (cut off at the edge), a label narrower than its own text
(cut off mid-word), and a page that only fits by scrolling sideways. None of them is visible
in a widget tree's text, which is why the existing UI tests could pass while the screen was
cropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractScrollArea,
    QComboBox,
    QFocusFrame,
    QLabel,
    QLayout,
    QMainWindow,
    QRubberBand,
    QScrollArea,
    QScrollBar,
    QSizeGrip,
    QStackedLayout,
    QToolButton,
    QWidget,
)

#: Rounding at fractional scales legitimately puts an edge a pixel out.
SLACK = 1


@dataclass(frozen=True, slots=True)
class Issue:
    kind: str
    widget: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.widget} — {self.detail}"


def describe(widget: QWidget) -> str:
    name = widget.objectName() or type(widget).__name__
    text = ""
    if isinstance(widget, (QLabel, QAbstractButton)):
        text = widget.text()
    elif isinstance(widget, QComboBox):
        text = widget.currentText()
    if text:
        plain = " ".join(_plain(text).split())
        return f"{name} '{plain[:40]}'"
    return name


def find_issues(root: QWidget) -> list[Issue]:
    """Every layout defect among the widgets currently shown inside ``root``."""
    issues: list[Issue] = []
    bounds = root.rect()
    for widget in root.findChildren(QWidget):
        if not widget.isVisibleTo(root) or widget.isWindow():
            continue
        if _inside_scrolled_content(widget, root):
            # Content inside a scroll area may legitimately run past the viewport
            # vertically; sideways overflow is reported once, on the area itself.
            pass
        else:
            placed = QRect(widget.mapTo(root, widget.rect().topLeft()), widget.size())
            if not bounds.adjusted(-SLACK, -SLACK, SLACK, SLACK).contains(placed):
                issues.append(
                    Issue("overflow", describe(widget), f"{_rect(placed)} outside {_rect(bounds)}")
                )
        if isinstance(widget, QScrollArea):
            issues += _sideways(widget)
        issues += _clipped_text(widget)
        issues += _overlaps(widget, root)
        issue = _stray(widget)
        if issue is not None:
            issues.append(issue)
    return issues


def _managed(layout: QLayout) -> list[QWidget]:
    """Every widget a layout places, through any nested layouts."""
    found: list[QWidget] = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item is None:
            continue
        if item.widget() is not None:
            found.append(item.widget())
        elif item.layout() is not None:
            found += _managed(item.layout())
    return found


def _overlaps(container: QWidget, root: QWidget) -> list[Issue]:
    """Siblings a layout has squeezed on top of each other — what too little room looks like.

    A layout that cannot fit its children's minimum sizes does not fail; it overlaps them.
    That is how the quote of the day came to be drawn across its own picture on a short
    screen, and no amount of checking each widget alone can see it.
    """
    layout = container.layout()
    if layout is None or isinstance(layout, QStackedLayout):
        return []
    shown = [w for w in _managed(layout) if w.isVisibleTo(root) and not w.isWindow()]
    found: list[Issue] = []
    for index, first in enumerate(shown):
        for second in shown[index + 1 :]:
            overlap = first.geometry().intersected(second.geometry())
            if overlap.width() > SLACK + 1 and overlap.height() > SLACK + 1:
                found.append(
                    Issue(
                        "overlap",
                        f"{describe(first)} and {describe(second)}",
                        f"{overlap.width()}x{overlap.height()}px drawn on top of each other",
                    )
                )
    return found


def _stray(widget: QWidget) -> Issue | None:
    """A visible child its parent's layout does not place: drawn wherever it last was.

    The shape of a widget taken out of a layout but not out of its parent — still painting,
    at a stale position, over whatever the layout has put there since.
    """
    parent = widget.parentWidget()
    if parent is None or widget.objectName().startswith("qt_"):
        return None
    layout = parent.layout()
    if layout is None or isinstance(parent, (QAbstractScrollArea, QMainWindow)):
        return None
    if isinstance(widget, (QScrollBar, QSizeGrip, QFocusFrame, QRubberBand)):
        return None
    if widget in _managed(layout):
        return None
    return Issue(
        "stray", describe(widget), f"visible at {_rect(widget.geometry())} outside any layout"
    )


def _inside_scrolled_content(widget: QWidget, root: QWidget) -> bool:
    parent = widget.parentWidget()
    while parent is not None and parent is not root:
        if isinstance(parent.parentWidget(), QAbstractScrollArea):
            return True
        parent = parent.parentWidget()
    return False


def _sideways(area: QScrollArea) -> list[Issue]:
    content = area.widget()
    if content is None or not content.isVisible():
        return []
    overflow = content.width() - area.viewport().width()
    if overflow <= SLACK:
        return []
    hidden = area.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    what = "cut off (sideways scrolling is disabled)" if hidden else "needs sideways scrolling"
    return [Issue("sideways", describe(area), f"content {overflow}px wider than the view: {what}")]


def _clipped_text(widget: QWidget) -> list[Issue]:
    if isinstance(widget, QLabel):
        text = widget.text()
        if not text or widget.wordWrap():
            return []
        plain = _plain(text) if _is_rich(widget) else text
        lines = plain.splitlines() or [plain]
        metrics = widget.fontMetrics()
        needed = max(metrics.horizontalAdvance(line) for line in lines)
        room = widget.contentsRect().width() - 2 * widget.margin() - 2 * widget.indent()
        found: list[Issue] = []
        if needed > room + SLACK and widget.width() > 0:
            found.append(Issue("clipped", describe(widget), f"needs {needed}px, has {room}px"))
        tall = metrics.height() * len(lines)
        if widget.contentsRect().height() + SLACK < metrics.height() and tall > 0:
            found.append(
                Issue(
                    "clipped",
                    describe(widget),
                    f"{widget.contentsRect().height()}px tall for {metrics.height()}px text",
                )
            )
        return found
    icon_only = (
        isinstance(widget, QToolButton)
        and widget.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        and not widget.icon().isNull()
    )
    if isinstance(widget, QAbstractButton) and widget.text() and not icon_only:
        needed = widget.fontMetrics().horizontalAdvance(widget.text().replace("&", ""))
        # Buttons draw their text inside a padded frame; under ~12px of padding the text
        # touches or crosses the border.
        if needed + 12 > widget.width() and widget.width() > 0:
            return [
                Issue(
                    "clipped", describe(widget), f"text {needed}px in a {widget.width()}px button"
                )
            ]
    return []


def _is_rich(label: QLabel) -> bool:
    fmt = label.textFormat()
    if fmt == Qt.TextFormat.RichText:
        return True
    return fmt == Qt.TextFormat.AutoText and _looks_rich(label.text())


def _plain(text: str) -> str:
    if not _looks_rich(text):
        return text
    document = QTextDocument()
    document.setHtml(text)
    return document.toPlainText()


def _looks_rich(text: str) -> bool:
    return bool(re.search(r"<[a-zA-Z/!][^>]*>", text))


def _rect(rect: QRect) -> str:
    return f"({rect.x()},{rect.y()} {rect.width()}x{rect.height()})"
