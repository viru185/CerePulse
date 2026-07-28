"""Render every icon exploration into one comparison sheet.

Each candidate is shown large, then at 32px and 16px upscaled with nearest-neighbour so
tray legibility can actually be judged rather than guessed::

    uv run python tools/preview_icons.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw  # noqa: E402
from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "assets" / "icon.svg"
EXPLORATIONS = ROOT / "assets" / "explorations"

BIG = 168
SMALL_SCALE = 4  # 32px -> 128, 16px -> 64
BACKGROUND = (24, 24, 27, 255)
LABEL = (161, 161, 170, 255)


#: Brand colours, swapped out to prove a mark holds up in a single flat colour.
BRAND_COLOURS = ("#22D3EE", "#34D399", "#F59E0B")


def monochrome(svg: Path, scratch: Path) -> Path:
    """Write a flat white-on-black copy of an SVG.

    The honest test of a timeless mark: strip the gradient and every accent, and see whether
    the shape still carries it. Anything that only works in colour is decoration.
    """
    text = svg.read_text(encoding="utf-8")
    for colour in BRAND_COLOURS:
        text = text.replace(colour, "#FFFFFF").replace(colour.lower(), "#FFFFFF")
    text = text.replace("#0E0E10", "#000000")

    target = scratch / f"{svg.stem}-mono.svg"
    target.write_text(text, encoding="utf-8")
    return target


def rasterize(svg: Path, size: int, scratch: Path) -> Image.Image:
    """Render via a temporary PNG.

    Handing Qt a QBuffer and reading the bytes back segfaults on teardown here, and a file
    round-trip costs nothing for a handful of previews.
    """
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise SystemExit(f"{svg} is not valid SVG")

    image = QImage(QSize(size, size), QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()

    target = scratch / f"{svg.stem}-{size}.png"
    image.save(str(target), "PNG")
    with Image.open(target) as opened:
        return opened.convert("RGBA")


def main() -> int:
    import tempfile

    QGuiApplication([])
    scratch = Path(tempfile.mkdtemp(prefix="cerepulse-icons-"))

    candidates: list[tuple[str, Path]] = [("A  current", CURRENT)]
    candidates += [
        (f"{path.stem[0].upper()}  {path.stem.split('-', 1)[1]}", path)
        for path in sorted(EXPLORATIONS.glob("*.svg"))
    ]

    row_h = BIG + 34
    row_w = BIG * 2 + 24 + 24 + 32 * SMALL_SCALE + 16 + 16 * SMALL_SCALE + 48
    sheet = Image.new("RGBA", (row_w, row_h * len(candidates) + 16), BACKGROUND)
    draw = ImageDraw.Draw(sheet)

    for index, (label, path) in enumerate(candidates):
        top = index * row_h + 8
        sheet.alpha_composite(rasterize(path, BIG, scratch), (16, top))

        # Flat white, no gradient: does the shape alone still carry the mark?
        sheet.alpha_composite(
            rasterize(monochrome(path, scratch), BIG, scratch), (16 + BIG + 16, top)
        )

        x = 16 + BIG * 2 + 24 + 24
        for size in (32, 16):
            scaled = rasterize(path, size, scratch).resize(
                (size * SMALL_SCALE, size * SMALL_SCALE), Image.Resampling.NEAREST
            )
            sheet.alpha_composite(scaled, (x, top + (BIG - size * SMALL_SCALE) // 2))
            draw.text((x, top + BIG - 6), f"{size}px", fill=LABEL)
            x += size * SMALL_SCALE + 16

        draw.text((16, top + BIG + 8), label, fill=(244, 244, 245, 255))
        draw.text((16 + BIG + 16, top + BIG + 8), "flat, one colour", fill=LABEL)

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "icon-preview.png"
    sheet.save(target)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
