"""Screenshot every screen at every display scale and theme, and check each for layout defects.

The app is built against a synthetic year (``tests/qc/seed.py``) with the clock frozen and
the network blocked, so the pictures are the same on every run and nothing reaches SpineHR::

    uv run python tools/qc_screens.py                     # everything
    uv run python tools/qc_screens.py --scales 1.5 --themes dark light --screens today records

Each display scale runs in its own process, because Qt reads ``QT_SCALE_FACTOR`` once, when
the application starts. Output, under ``build/qc/`` by default:

- ``<scale>/<size>/<theme>/NN-<screen>.png`` — the window as the user sees it;
- ``<scale>/<size>/<theme>/NN-<screen>-page.png`` — a scrolling page rendered at full length;
- ``sheet-<scale>-<size>-<theme>.png`` — every screen on one contact sheet;
- ``report.json`` and ``report.txt`` — every overflow, clipped label and sideways scroll found.

Sizes are logical pixels. ``screen`` is a 1920x1080 panel at that scale, less the taskbar and
the title bar: the most room the app gets on a common laptop. ``min`` is the window's own
minimum size; if it is larger than ``screen``, that alone is a defect and is reported.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "build" / "qc"
SCALES = ("1.0", "1.25", "1.5", "1.75")
THEMES = ("dark", "light", "sakura", "neon", "ocean", "solar")
SIZES = ("screen", "min")
#: Logical pixels Windows 11 takes from a maximised window: the taskbar and the title bar.
TASKBAR = 48
TITLE_BAR = 32


def _font_dir() -> str:
    if sys.platform == "win32":
        return str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
    return "/usr/share/fonts"


def screen_size(scale: float) -> tuple[int, int]:
    return round(1920 / scale), round(1080 / scale) - TASKBAR - TITLE_BAR


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--scales", nargs="+", default=list(SCALES))
    parser.add_argument("--themes", nargs="+", default=list(THEMES))
    parser.add_argument("--sizes", nargs="+", default=list(SIZES))
    parser.add_argument("--screens", nargs="+", default=None)
    parser.add_argument("--no-pages", action="store_true", help="skip full-length page renders")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.child:
        return _child(args)

    args.out.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, object]] = []
    for scale in args.scales:
        print(f"-- scale {scale}", flush=True)
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--child",
            "--out",
            str(args.out),
            "--scales",
            scale,
            "--themes",
            *args.themes,
            "--sizes",
            *args.sizes,
        ]
        if args.screens:
            command += ["--screens", *args.screens]
        if args.no_pages:
            command.append("--no-pages")
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=scale)
        # The offscreen platform reads fonts through FreeType from one folder and knows
        # nothing of the system's; without this every glyph is a box and every width a guess.
        environment.setdefault("QT_QPA_FONTDIR", _font_dir())
        completed = subprocess.run(command, env=environment, cwd=ROOT, check=False)
        if completed.returncode != 0:
            print(f"   scale {scale} failed with exit code {completed.returncode}", flush=True)
            return completed.returncode
        part = args.out / f"report-{scale}.json"
        reports += json.loads(part.read_text(encoding="utf-8"))
        part.unlink()

    (args.out / "report.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")
    lines: list[str] = []
    for entry in reports:
        issues = entry["issues"]
        assert isinstance(issues, list)
        if issues:
            lines.append(f"[{entry['scale']} {entry['size']} {entry['screen']}]")
            lines += [f"  {issue}" for issue in issues]
    (args.out / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _sheets(args.out)
    total = sum(len(entry["issues"]) for entry in reports)  # type: ignore[arg-type]
    print(f"{total} layout issue(s); see {args.out / 'report.txt'}", flush=True)
    return 0


def _child(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT))
    scale = float(args.scales[0])
    root = Path(tempfile.mkdtemp(prefix="cerepulse-qc-"))
    os.environ["CEREPULSE_DATA_DIR"] = str(root)

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QScrollArea
    from tests.qc import app as qc_app
    from tests.qc import clock, layout, seed

    # The same start-up as ``cerepulse.ui.app.run_app``, minus the parts that touch the shell.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    application = QApplication.instance() or QApplication([])
    qc_app.style_like_the_app(application, args.themes[0])

    wanted = args.screens or list(qc_app.SCREENS)
    reports: list[dict[str, object]] = []
    with clock.frozen(seed.NOW):
        window = qc_app.build_window(root, theme=args.themes[0])
        window.show()
        default = (window.width(), window.height())
        minimum = (window.minimumWidth(), window.minimumHeight())
        for size_name in args.sizes:
            if size_name == "screen":
                target = screen_size(scale)
            elif size_name == "min":
                target = minimum
            elif "x" in size_name:
                # A particular screen, in logical pixels: "1440x820" is a 2880x1800 panel at
                # 200%, less the taskbar and the title bar.
                width, height = size_name.split("x", 1)
                target = (int(width), int(height))
            else:
                target = default
            window.resize(*target)
            qc_app.settle(window, at_least=0.1)
            actual = (window.width(), window.height())
            for theme_index, theme in enumerate(args.themes):
                window._apply_theme(theme)
                qc_app.settle(window, at_least=0.1)
                folder = args.out / args.scales[0] / size_name / theme
                folder.mkdir(parents=True, exist_ok=True)
                for index, screen in enumerate(qc_app.SCREENS):
                    if screen not in wanted:
                        continue
                    window._navigation.select(index)
                    qc_app.settle(window, at_least=0.05)
                    views = [("", None), *qc_app.VIEWS.get(screen, ())]
                    for suffix, view_index in views:
                        if view_index is not None:
                            getattr(window, screen).show_view(view_index)
                            qc_app.settle(window, at_least=0.05)
                        stem = f"{index + 1:02d}-{screen}{suffix}"
                        window.grab().save(str(folder / f"{stem}.png"))
                        page = window._stack.currentWidget()
                        areas = [a for a in page.findChildren(QScrollArea) if a.isVisible()]
                        if areas and not args.no_pages:
                            content = max(areas, key=lambda a: a.widget().height()).widget()
                            if content is not None and content.height() > areas[0].height():
                                content.grab().save(str(folder / f"{stem}-page.png"))
                        if theme_index == 0:
                            issues = [str(issue) for issue in layout.find_issues(window)]
                            if actual != target:
                                issues.insert(
                                    0,
                                    f"window: asked for {target[0]}x{target[1]}, the window's "
                                    f"minimum forced {actual[0]}x{actual[1]}",
                                )
                            reports.append(
                                {
                                    "scale": args.scales[0],
                                    "size": size_name,
                                    "window": list(actual),
                                    "screen": f"{screen}{suffix}",
                                    "issues": issues,
                                }
                            )
                    if screen in qc_app.VIEWS:
                        getattr(window, screen).show_view(0)
                print(f"   {size_name} {theme} done", flush=True)
        qc_app.close(window)
    (args.out / f"report-{args.scales[0]}.json").write_text(
        json.dumps(reports, indent=2), encoding="utf-8"
    )
    return 0


def _sheets(out: Path) -> None:
    """One contact sheet per scale, size and theme, thumbnails in sidebar order."""
    from PIL import Image, ImageDraw

    for folder in sorted(p for p in out.glob("*/*/*") if p.is_dir()):
        shots = sorted(p for p in folder.glob("*.png") if not p.stem.endswith("-page"))
        if not shots:
            continue
        thumbs = []
        for shot in shots:
            with Image.open(shot) as image:
                image = image.convert("RGB")
                image.thumbnail((640, 640))
                thumbs.append((shot.stem, image.copy()))
        width = max(t.width for _, t in thumbs)
        height = max(t.height for _, t in thumbs)
        columns = 4
        rows = (len(thumbs) + columns - 1) // columns
        sheet = Image.new(
            "RGB", (columns * (width + 16) + 16, rows * (height + 40) + 16), "#202020"
        )
        draw = ImageDraw.Draw(sheet)
        for i, (name, thumb) in enumerate(thumbs):
            x = 16 + (i % columns) * (width + 16)
            y = 16 + (i // columns) * (height + 40)
            draw.text((x, y), name, fill="#e0e0e0")
            sheet.paste(thumb, (x, y + 20))
        scale, size, theme = folder.parts[-3:]
        sheet.save(out / f"sheet-{scale}-{size}-{theme}.png")


if __name__ == "__main__":
    raise SystemExit(main())
