"""Build every Windows artifact: the app folder, the portable zip, and the installer.

    uv run python tools/build_all.py            # everything available
    uv run python tools/build_all.py --portable # skip the installer
    uv run python tools/build_all.py --clean    # rebuild from scratch

The version comes from ``__about__.py`` and is injected into the PyInstaller version
resource and the InnoSetup script, so a release is a one-line bump in one file.

The installer step is skipped with a clear message when InnoSetup is not installed, rather
than failing the build — most local builds only need the folder or the zip.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cerepulse import __about__ as about  # noqa: E402

DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP_DIR = DIST / about.NAME
SPEC = ROOT / "packaging" / "cerepulse.spec"
ISS = ROOT / "packaging" / "installer.iss"

#: Marker that switches the app to storing its data beside the executable.
PORTABLE_MARKER = "portable.marker"

ISCC_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
)


def run(command: list[str], *, cwd: Path | None = None) -> None:
    print(f"$ {' '.join(command)}")
    subprocess.run(command, cwd=cwd or ROOT, check=True)


def clean() -> None:
    for target in (DIST, BUILD):
        if target.exists():
            print(f"removing {target.relative_to(ROOT)}")
            shutil.rmtree(target)


def numeric_version(version: str) -> tuple[int, int, int, int]:
    """The four integers Windows wants, from a version that may carry a label.

    ``0.4.0-beta.1`` has to become ``(0, 4, 0, 0)``: the resource is a fixed struct of four
    16-bit numbers and cannot hold "beta". The full string still goes into FileVersion and
    ProductVersion, which are free text, so nothing is lost — the label is visible in the
    file properties even though the numeric fields cannot express it.
    """
    base = version.split("-", 1)[0].split("+", 1)[0]
    parts: list[int] = []
    for piece in base.split(".")[:4]:
        try:
            parts.append(int(piece))
        except ValueError:
            break
    while len(parts) < 4:
        parts.append(0)
    return parts[0], parts[1], parts[2], parts[3]


def write_version_resource() -> Path:
    """Generate the Windows version resource embedded in the executable."""
    quad = ", ".join(str(part) for part in numeric_version(about.VERSION))

    target = ROOT / "packaging" / "version_info.txt"
    target.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({quad}), prodvers=({quad}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', {about.AUTHOR!r}),
        StringStruct('FileDescription', {about.SUMMARY.split(".")[0]!r}),
        StringStruct('FileVersion', {about.VERSION!r}),
        StringStruct('InternalName', {about.NAME!r}),
        StringStruct('OriginalFilename', {f"{about.NAME}.exe"!r}),
        StringStruct('ProductName', {about.NAME!r}),
        StringStruct('ProductVersion', {about.VERSION!r}),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return target


def build_app() -> None:
    write_version_resource()
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)])
    if not (APP_DIR / f"{about.NAME}.exe").exists():
        raise SystemExit(f"PyInstaller did not produce {APP_DIR / f'{about.NAME}.exe'}")
    print(f"built {APP_DIR.relative_to(ROOT)} ({_folder_size_mb(APP_DIR):.0f} MB)")


def build_portable() -> Path:
    """Zip the app folder with a portable marker so it keeps its data alongside itself."""
    marker = APP_DIR / PORTABLE_MARKER
    marker.write_text(
        "This file makes CerePulse store its data in the Data folder beside the "
        "executable instead of in %LOCALAPPDATA%.\n",
        encoding="utf-8",
    )

    target = DIST / f"{about.NAME}-{about.VERSION}-portable.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(APP_DIR.rglob("*")):
            if path.is_file():
                archive.write(path, Path(about.NAME) / path.relative_to(APP_DIR))

    # The installed build must not think it is portable.
    marker.unlink()
    print(f"built {target.relative_to(ROOT)} ({target.stat().st_size / 1e6:.0f} MB)")
    return target


def find_iscc() -> Path | None:
    found = shutil.which("ISCC")
    if found:
        return Path(found)
    return next((path for path in ISCC_CANDIDATES if path.exists()), None)


def build_installer() -> Path | None:
    iscc = find_iscc()
    if iscc is None:
        print(
            "skipping installer: InnoSetup 6 not found.\n"
            "  Install it from https://jrsoftware.org/isdl.php, or run with --portable."
        )
        return None

    numeric = ".".join(str(part) for part in numeric_version(about.VERSION))
    run(
        [
            str(iscc),
            f"/DAppVersion={about.VERSION}",
            # VersionInfoVersion is a fixed numeric struct; a pre-release label fails it.
            f"/DNumericVersion={numeric}",
            f"/DSourceDir={APP_DIR}",
            f"/DOutputDir={DIST}",
            str(ISS),
        ]
    )
    target = DIST / f"{about.NAME}-{about.VERSION}-Setup.exe"
    if target.exists():
        print(f"built {target.relative_to(ROOT)} ({target.stat().st_size / 1e6:.0f} MB)")
        return target
    return None


def _folder_size_mb(folder: Path) -> float:
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file()) / 1e6


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Build {about.NAME} for Windows.")
    parser.add_argument("--clean", action="store_true", help="Remove dist/ and build/ first.")
    parser.add_argument("--portable", action="store_true", help="Skip the installer.")
    parser.add_argument("--app-only", action="store_true", help="Only build the app folder.")
    args = parser.parse_args()

    if os.name != "nt":
        print("This build produces Windows artifacts and must run on Windows.", file=sys.stderr)
        return 2

    if args.clean:
        clean()

    print(f"Building {about.NAME} {about.VERSION}")
    build_app()
    if args.app_only:
        return 0

    build_portable()
    if not args.portable:
        build_installer()

    print("\nArtifacts:")
    for path in sorted(DIST.iterdir()):
        if path.is_file():
            print(f"  {path.name}  ({path.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
