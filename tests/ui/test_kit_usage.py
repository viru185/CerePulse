"""The UI is built from the kit: no raw Qt controls, no stylesheets set widget by widget.

Each button, field and coloured label used to be made and painted where it was needed, so
the same control looked three ways on three screens and a theme change had to hunt down
every widget that had coloured itself. ``cerepulse.ui.kit`` makes each kind once.

This counts, per file outside the kit, the raw controls built and the ``setStyleSheet``
calls made. Screens not yet moved onto the kit are allowed what they have today, and the
allowance can only shrink: a file over it fails, and a file under it fails too, asking for
its number to be lowered, so a migration is recorded the moment it lands.
"""

from __future__ import annotations

import ast
from pathlib import Path

UI = Path(__file__).resolve().parents[2] / "src" / "cerepulse" / "ui"

#: Controls the kit makes. Built anywhere else, they bypass its look and behaviour.
RAW = frozenset(
    {
        "QPushButton",
        "QToolButton",
        "QComboBox",
        "QSpinBox",
        "QDoubleSpinBox",
        "QTimeEdit",
        "QDateEdit",
        "QLineEdit",
        "QCheckBox",
        "QMenu",
        "QTableWidget",
    }
)

#: (raw controls, setStyleSheet calls) a file may still have. Only ever lowered.
ALLOWED: dict[str, tuple[int, int]] = {
    # The application sheet itself, set once at launch and again on a theme change.
    "app.py": (0, 1),
    "daily_viewer.py": (2, 0),
    "login_dialog.py": (5, 1),
    "main_window.py": (3, 1),
    "views/about.py": (9, 2),
    "views/settings.py": (37, 3),
    "views/sync_panel.py": (3, 2),
    "whats_new.py": (6, 2),
    "widgets.py": (5, 14),
}


def _counts(path: Path) -> tuple[int, int]:
    raw = styles = 0
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in RAW:
            raw += 1
        if isinstance(node.func, ast.Attribute) and node.func.attr == "setStyleSheet":
            styles += 1
    return raw, styles


def _ui_files() -> dict[str, tuple[int, int]]:
    found: dict[str, tuple[int, int]] = {}
    for path in sorted(UI.rglob("*.py")):
        relative = path.relative_to(UI)
        if relative.parts[0] == "kit":
            continue
        found[relative.as_posix()] = _counts(path)
    return found


def test_no_file_builds_more_raw_controls_or_styles_than_it_is_allowed() -> None:
    over = {
        name: (counts, ALLOWED.get(name, (0, 0)))
        for name, counts in _ui_files().items()
        if counts[0] > ALLOWED.get(name, (0, 0))[0] or counts[1] > ALLOWED.get(name, (0, 0))[1]
    }
    assert over == {}, (
        "Build these from cerepulse.ui.kit (Button, ComboBox, Text, Pill…) instead of raw Qt "
        f"controls or setStyleSheet — (raw, styles) found vs allowed: {over}"
    )


def test_the_allowance_shrinks_as_screens_move_onto_the_kit() -> None:
    files = _ui_files()
    stale = {
        name: (files.get(name, (0, 0)), allowed)
        for name, allowed in ALLOWED.items()
        if files.get(name, (0, 0)) != allowed and files.get(name, (0, 0)) <= allowed
    }
    assert stale == {}, f"Lower ALLOWED to what these files now have: {stale}"
