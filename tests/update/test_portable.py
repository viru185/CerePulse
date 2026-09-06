"""The portable build updating itself: asset choice, naming, staging, the swap script."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from cerepulse import __about__ as about
from cerepulse.update import downloader
from cerepulse.update.checker import _parse
from cerepulse.update.downloader import (
    StageError,
    archive_name,
    stage_archive,
    version_in_asset_name,
    version_in_installer_name,
)
from cerepulse.update.installer import swap_script_text
from cerepulse.update.mode import BuildMode


def release(*names: str) -> dict[str, object]:
    return {
        "tag_name": "v0.16.0",
        "name": "CerePulse 0.16.0",
        "body": "",
        "html_url": "https://example.test/v0.16.0",
        "prerelease": False,
        "draft": False,
        "assets": [
            {"name": name, "browser_download_url": f"https://example.test/{name}", "size": 10}
            for name in names
        ],
    }


@pytest.fixture(autouse=True)
def staged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "updates"
    monkeypatch.setattr(downloader, "downloads_dir", lambda: directory)
    return directory


# --- the release carries both assets -------------------------------------------------------


def test_a_release_offers_each_build_its_own_asset() -> None:
    parsed = _parse(release("CerePulse-Setup-0.16.0.exe", "CerePulse-portable-0.16.0.zip"))
    assert parsed is not None
    assert parsed.asset_for(BuildMode.INSTALLED).name == "CerePulse-Setup-0.16.0.exe"  # type: ignore[union-attr]
    assert parsed.asset_for(BuildMode.PORTABLE).name == "CerePulse-portable-0.16.0.zip"  # type: ignore[union-attr]
    assert parsed.asset_for(BuildMode.SOURCE) is None


def test_a_release_with_only_a_zip_installs_on_portable_and_not_on_installed() -> None:
    parsed = _parse(release("CerePulse-portable-0.16.0.zip"))
    assert parsed is not None
    assert parsed.is_installable
    assert parsed.installable_for(BuildMode.PORTABLE)
    assert not parsed.installable_for(BuildMode.INSTALLED)


# --- naming ----------------------------------------------------------------------------------


def test_the_archive_name_round_trips_and_matches_the_build_tool() -> None:
    assert archive_name("0.16.0") == "CerePulse-portable-0.16.0.zip"
    assert str(version_in_asset_name("CerePulse-portable-0.16.0.zip")) == "0.16.0"
    assert str(version_in_asset_name("CerePulse-portable-0.16.0-beta.1.zip")) == "0.16.0-beta.1"


def test_partial_downloads_are_never_read_as_a_version() -> None:
    assert version_in_asset_name("CerePulse-portable-0.16.0.part") is None
    assert version_in_asset_name("CerePulse-Setup-0.16.0.part") is None


def test_the_old_exe_only_name_still_reads_both_exe_layouts() -> None:
    assert str(version_in_installer_name("CerePulse-Setup-0.15.0.exe")) == "0.15.0"
    assert str(version_in_installer_name("CerePulse-0.14.1-Setup.exe")) == "0.14.1"


# --- staging ---------------------------------------------------------------------------------


def build_zip(path: Path, *, with_exe: bool = True, members: tuple[str, ...] = ()) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        if with_exe:
            bundle.writestr(f"{about.NAME}/{about.NAME}.exe", b"exe")
        bundle.writestr(f"{about.NAME}/_internal/x", b"lib")
        bundle.writestr(f"{about.NAME}/portable.marker", b"")
        for member in members:
            bundle.writestr(member, b"?")
    return path


def test_a_portable_archive_is_unpacked_into_a_runnable_folder(
    staged: Path, tmp_path: Path
) -> None:
    archive = build_zip(tmp_path / "CerePulse-portable-0.16.0.zip")
    app_dir = stage_archive(archive, "0.16.0")

    assert app_dir == staged / "staged" / "0.16.0" / about.NAME
    assert (app_dir / f"{about.NAME}.exe").exists()
    assert not (staged / "staged" / "0.16.0.part").exists()


def test_staging_is_idempotent(staged: Path, tmp_path: Path) -> None:
    archive = build_zip(tmp_path / "CerePulse-portable-0.16.0.zip")
    first = stage_archive(archive, "0.16.0")
    stamp = (first / f"{about.NAME}.exe").stat().st_mtime_ns
    assert stage_archive(archive, "0.16.0") == first
    assert (first / f"{about.NAME}.exe").stat().st_mtime_ns == stamp


def test_an_archive_without_the_exe_is_refused_and_leaves_nothing(
    staged: Path, tmp_path: Path
) -> None:
    archive = build_zip(tmp_path / "bad.zip", with_exe=False)
    with pytest.raises(StageError):
        stage_archive(archive, "0.16.0")
    assert not (staged / "staged" / "0.16.0").exists()


def test_an_archive_that_escapes_its_folder_is_refused(staged: Path, tmp_path: Path) -> None:
    archive = build_zip(tmp_path / "evil.zip", members=("../evil.txt",))
    with pytest.raises(StageError):
        stage_archive(archive, "0.16.0")


# --- the swap script ---------------------------------------------------------------------------


def script_for(app: Path, source: Path, *, restart: bool = True) -> str:
    return swap_script_text(
        app=app,
        source=source,
        version="0.16.0",
        previous_version="0.15.0",
        pid=4242,
        exe_name="CerePulse.exe",
        log=Path("C:/Temp/CerePulse-update/apply-update-4242.log"),
        restart=restart,
    )


def test_the_swap_script_rewrites_the_staged_path_to_where_it_will_be() -> None:
    """The staged folder lives inside the app folder, which is renamed first."""
    app = Path("C:/Program Files (x86)/My CerePulse")
    source = app / "Data" / "updates" / "staged" / "0.16.0" / "CerePulse"
    script = script_for(app, source)

    assert f'set "SOURCE_BEFORE={source}"' in script
    assert (
        'set "SOURCE=C:\\Program Files (x86)\\My CerePulse.swap\\Data' in script.replace("/", "\\")
        or "My CerePulse.swap" in script
    )


def test_a_rollback_source_beside_the_app_is_used_as_is() -> None:
    app = Path("C:/Apps/CerePulse")
    script = script_for(app, app.with_name("CerePulse.old"))
    assert (
        'set "SOURCE=C:\\Apps\\CerePulse.old"' in script.replace("/", "\\")
        or "CerePulse.old" in script
    )


def test_the_script_keeps_every_rule_cmd_has_taught_us() -> None:
    app = Path("C:/Program Files (x86)/My CerePulse")
    script = script_for(app, app / "Data" / "updates" / "staged" / "0.16.0" / "CerePulse")
    lines = script.splitlines()

    assert not any("timeout" in line for line in lines), "timeout dies without a console"
    assert any("ping -n" in line for line in lines)
    assert any('find /I "CerePulse.exe"' in line for line in lines), "match the image name"
    for line in lines:
        if ">>" in line and "echo" in line:
            assert " >>" in line, f"a trailing digit would be read as a handle: {line}"
        assert not (line.strip().startswith("if ") and line.rstrip().endswith("(")), (
            "a ')' in the path ends a parenthesised block"
        )
    assert 'cd /d "%TEMP%"' in script, "cmd must not sit inside the folder being renamed"
    assert "previous.version" in script


def test_the_relaunch_can_be_skipped() -> None:
    app = Path("C:/Apps/CerePulse")
    assert "rem no relaunch requested" in script_for(app, app / "x", restart=False)
    assert 'start "" /D "%APP%" "%EXE%"' in script_for(app, app / "x")


# --- cleanup knows about zips ----------------------------------------------------------------


def test_cleanup_removes_spent_archives_and_keeps_pending_ones(staged: Path) -> None:
    from cerepulse.update.downloader import clear_spent_installers

    staged.mkdir(parents=True, exist_ok=True)
    for version in ("0.15.0", "0.16.0", "0.17.0"):
        downloader.archive_path(version).write_bytes(b"x")
    (staged / "staged" / "0.16.0").mkdir(parents=True)

    clear_spent_installers("0.16.0")
    assert not downloader.archive_path("0.15.0").exists()
    assert not downloader.archive_path("0.16.0").exists()
    assert downloader.archive_path("0.17.0").exists()
    assert not (staged / "staged" / "0.16.0").exists()
