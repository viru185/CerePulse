# PyInstaller spec for CerePulse.
#
# One-folder rather than one-file: a one-file build unpacks PySide6 to a temp directory on
# every launch, which costs seconds of startup for an app whose whole premise is opening
# instantly. The folder is what the installer and the portable zip both wrap.
#
# Built via `python tools/build_all.py`, not by invoking PyInstaller directly, so the
# version and paths stay in step with __about__.py.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from cerepulse import __about__ as about  # noqa: E402

ICON = ROOT / "src" / "cerepulse" / "ui" / "assets" / "icon.ico"

# keyring resolves its Windows backend at runtime, so the module never appears in the
# import graph and has to be named explicitly.
HIDDEN = [
    "keyring.backends.Windows",
    "win32timezone",
    *collect_submodules("cerepulse"),
]

# Qt modules the app never touches. Dropping them roughly halves the build.
EXCLUDED_QT = [
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
]

analysis = Analysis(
    [str(ROOT / "src" / "cerepulse" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[
        (str(ROOT / "src" / "cerepulse" / "ui" / "assets"), "cerepulse/ui/assets"),
        # The What's New dialog reads this. It opens 400 ms after launch, which is no time
        # to be waiting on a round trip to GitHub for notes we already have.
        (str(ROOT / "CHANGELOG.md"), "."),
    ],
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=[*EXCLUDED_QT, "tkinter", "unittest", "pydoc_data"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=about.NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Windowed: this is a GUI app and a console would flash on every launch.
    console=False,
    icon=str(ICON) if ICON.exists() else None,
    version_file=str(ROOT / "packaging" / "version_info.txt")
    if (ROOT / "packaging" / "version_info.txt").exists()
    else None,
)

COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name=about.NAME,
)
