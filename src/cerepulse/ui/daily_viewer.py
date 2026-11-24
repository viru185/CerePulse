"""The picture of the day at full size, with its title, credit and the day's quote."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPixmap, QScreen
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from cerepulse.services.daily import DailyView

MAX_WIDTH = 880
MAX_HEIGHT = 560


class DailyViewer(QDialog):
    """Opened from the sidebar tile. "Use as background" is how a picture becomes the
    wallpaper without a trip to Settings — the one action the picture invites."""

    use_as_background = Signal(str)

    def __init__(self, view: DailyView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        picture = view.picture
        self.setWindowTitle(picture.title if picture and picture.title else "Today")
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        if picture is not None:
            image = QLabel()
            pixmap = QPixmap(str(picture.path))
            if not pixmap.isNull():
                image.setPixmap(_fitted(pixmap, self.screen() or QGuiApplication.primaryScreen()))
            layout.addWidget(image, 0, Qt.AlignmentFlag.AlignHCenter)
            if picture.title:
                title = QLabel(picture.title)
                title.setObjectName("CardValue")
                layout.addWidget(title)
            if picture.copyright:
                credit = QLabel(
                    f'<a href="{picture.copyright_url}">{picture.copyright}</a>'
                    if picture.copyright_url
                    else picture.copyright
                )
                credit.setObjectName("CardCaption")
                credit.setOpenExternalLinks(True)
                credit.setWordWrap(True)
                layout.addWidget(credit)

        if view.quote is not None:
            quote = QLabel(
                f"“{view.quote.text}”"
                + (f" — {view.quote.attribution}" if view.quote.attribution else "")
            )
            quote.setWordWrap(True)
            layout.addWidget(quote)
            credit = QLabel(f'<a href="{view.quote.credit_url}">{view.quote.credit}</a>')
            credit.setObjectName("CardCaption")
            credit.setOpenExternalLinks(True)
            layout.addWidget(credit)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        if picture is not None:
            use = QPushButton("Use as background")
            use.setToolTip("Sets this picture as the window's background, dimmed by the theme")
            use.clicked.connect(lambda: self.use_as_background.emit(str(picture.path)))
            buttons.addWidget(use)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)


__all__ = ["DailyViewer"]


def _fitted(pixmap: QPixmap, screen: QScreen | None) -> QPixmap:
    """The picture at most :data:`MAX_WIDTH` by :data:`MAX_HEIGHT`, never more of the screen
    than leaves room for its caption and buttons, and drawn at the screen's pixel ratio.

    A fixed size did not fit a 1080p laptop at 175%, whose whole working area is 537 logical
    pixels tall; and scaled to logical pixels, the picture was soft on every scaled display.
    """
    width, height, ratio = MAX_WIDTH, MAX_HEIGHT, 1.0
    if screen is not None:
        room = screen.availableGeometry()
        width = min(width, round(room.width() * 0.8))
        height = min(height, round(room.height() * 0.6))
        ratio = screen.devicePixelRatio() or 1.0
    fitted = pixmap.scaled(
        QSize(round(width * ratio), round(height * ratio)),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    fitted.setDevicePixelRatio(ratio)
    return fitted
