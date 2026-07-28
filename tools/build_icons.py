"""Render assets/icon.svg into the PNG and ICO files the app and installer ship.

Run after editing the SVG::

    uv run python tools/build_icons.py

Outputs land in ``src/cerepulse/ui/assets/`` so they are packaged with the wheel and picked
up by PyInstaller. They are committed rather than generated at build time, because the
installer needs a real ``.ico`` on disk before Python ever runs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Rendering needs no display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "icon.svg"
OUTPUT = ROOT / "src" / "cerepulse" / "ui" / "assets"

#: Windows shows 16px in the tray and title bar, 32 in the taskbar, 256 in Explorer.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
PNG_SIZES = (16, 32, 64, 128, 256, 512)


def render(renderer: QSvgRenderer, size: int) -> QImage:
    """Rasterize the SVG at one size, on transparent pixels."""
    image = QImage(QSize(size, size), QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()
    return image


def main() -> int:
    if not SOURCE.exists():
        print(f"Missing {SOURCE}", file=sys.stderr)
        return 1

    QGuiApplication([])
    renderer = QSvgRenderer(str(SOURCE))
    if not renderer.isValid():
        print(f"{SOURCE} is not valid SVG", file=sys.stderr)
        return 1

    OUTPUT.mkdir(parents=True, exist_ok=True)

    for size in PNG_SIZES:
        target = OUTPUT / f"icon-{size}.png"
        render(renderer, size).save(str(target), "PNG")
        print(f"wrote {target.relative_to(ROOT)}")

    # Pillow assembles the multi-resolution ICO; Qt writes only a single size per file.
    from PIL import Image

    largest = OUTPUT / "icon-512.png"
    with Image.open(largest) as source:
        ico = OUTPUT / "icon.ico"
        source.save(ico, format="ICO", sizes=[(size, size) for size in ICO_SIZES])
        print(f"wrote {ico.relative_to(ROOT)} ({len(ICO_SIZES)} sizes)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
