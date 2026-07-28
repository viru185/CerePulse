"""Packaged icon lookup.

Icons are committed rather than rendered at runtime: the installer needs a real ``.ico`` on
disk before Python ever runs, and PyInstaller has to be able to bundle a file. Regenerate
them with ``python tools/build_icons.py`` after editing ``assets/icon.svg``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QIcon, QPixmap

ASSETS = Path(__file__).resolve().parent / "assets"

#: Sizes shipped as PNG. Qt picks the closest when the icon is drawn.
ICON_SIZES = (16, 32, 64, 128, 256, 512)


def icon_path(name: str = "icon.ico") -> Path:
    """Absolute path to a packaged asset, for the installer and the updater."""
    return ASSETS / name


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """The application icon, carrying every rendered size.

    Built from the individual PNGs rather than the ICO so Qt can pick the exact pixel size
    for the tray, the taskbar and the title bar without rescaling.
    """
    icon = QIcon()
    for size in ICON_SIZES:
        candidate = ASSETS / f"icon-{size}.png"
        if candidate.exists():
            icon.addPixmap(QPixmap(str(candidate)))
    if icon.isNull() and icon_path().exists():
        icon = QIcon(str(icon_path()))
    return icon
