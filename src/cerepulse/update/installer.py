"""Handing over to the installer, and coming back.

The sequence is fiddlier than it looks, and every step of it exists for a reason.

**The app must quit before the installer runs.** Inno Setup's ``CloseApplications`` can
force a running copy closed, but that kills the process mid-write; quitting first means the
database is closed cleanly and the single-instance lock is released.

**The installer will not relaunch us.** ``installer.iss`` marks its post-install ``[Run]``
entry ``skipifsilent``, which is correct — a silent install triggered by some other tool
should not pop a window — but it means a silent update ends with nothing running. So the
relaunch is arranged here instead: a detached helper waits for this process to exit, runs
the installer, and starts the new build.

**The previous installer is kept.** Rolling back is then just running it, which is the only
rollback mechanism available for a per-user Inno install — there is no uninstall-to-previous.

Nothing here executes anything the app did not download and verify itself.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from loguru import logger

from cerepulse import __about__ as about
from cerepulse.update import seen
from cerepulse.update.downloader import installer_path, staged_app_dir
from cerepulse.update.mode import BuildMode, build_mode

#: Silent, no reboot, no message boxes. /VERYSILENT shows nothing at all; the progress the
#: user sees is CerePulse's own, before it quits.
SILENT_FLAGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")


class InstallError(Exception):
    """The update could not be started."""


def is_installed_build() -> bool:
    """Whether this is an installed copy, as opposed to a source run or a portable one.

    A source run has no installer to hand over to, and updating a portable copy in place
    would be wrong — it lives wherever the user put it.
    """
    from cerepulse.core import paths

    return getattr(sys, "frozen", False) and not paths.is_portable()


PREVIOUS_VERSION_FILE = "previous.version"
#: Characters cmd cannot carry safely through the swap script's ``echo`` and ``set`` lines.
_UNSAFE_PATH_CHARS = set("&^!%|<>")


def previous_dir() -> Path:
    """Where a portable update keeps the build it replaced: ``<app>.old`` beside the app."""
    from cerepulse.core import paths

    return Path(f"{paths.install_dir()}.old")


def swap_dir() -> Path:
    from cerepulse.core import paths

    return Path(f"{paths.install_dir()}.swap")


def failed_dir() -> Path:
    from cerepulse.core import paths

    return Path(f"{paths.install_dir()}.failed")


def previous_version() -> str | None:
    """The version kept in ``<app>.old``, when the folder is a runnable build."""
    folder = previous_dir()
    if not (folder / f"{about.NAME}.exe").exists():
        return None
    try:
        return (folder / PREVIOUS_VERSION_FILE).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def helper_dir() -> Path:
    """Where the portable swap script lives: outside the folder it is about to rename."""
    return Path(tempfile.gettempdir()) / f"{about.NAME}-update"


def apply_update(version: str, *, restart: bool = True) -> None:
    """Quit, install, and come back on the new version.

    Returns as soon as the helper is detached; the caller is expected to close the app
    immediately afterwards. The helper waits for this process to disappear before touching
    any files. An installed copy hands over to the Setup exe; a portable copy swaps its own
    folder for the unpacked archive.
    """
    mode = build_mode()
    if mode is BuildMode.PORTABLE:
        _apply_portable(version, restart=restart)
        return
    installer = installer_path(version)
    if not installer.exists():
        raise InstallError(f"The installer for {version} is not downloaded.")
    if mode is not BuildMode.INSTALLED:
        raise InstallError(
            "Automatic install only works for an installed or portable build. "
            "Download the new version and run it yourself."
        )

    script = _handoff_script(installer, os.getpid(), restart=restart)
    try:
        subprocess.Popen(  # noqa: S603 — argv list, no shell, our own generated script
            ["cmd.exe", "/c", str(script)],
            creationflags=_NO_WINDOW,
            close_fds=True,
        )
    except OSError as exc:
        raise InstallError(f"Could not start the installer: {exc}") from exc

    seen.record_update(version, "installing", f"handed over to {installer.name}")
    logger.info("Handed over to the installer for {}; quitting", version)


def rollback_to(version: str) -> None:
    """Go back to an earlier version.

    Installed: reinstall its Setup exe, the only rollback a per-user Inno install offers
    (same AppId, so it overwrites in place). Portable: put the folder kept beside the app
    back — or, when a staged archive of that version is what we have, swap to that instead.
    """
    if build_mode() is BuildMode.PORTABLE:
        rollback_portable(version)
        return
    if not installer_path(version).exists():
        raise InstallError(
            f"No installer for {version} is kept locally, so it cannot be rolled back to."
        )
    apply_update(version, restart=True)
    seen.record_update(version, "rolled-back")


def rollback_portable(version: str) -> None:
    """Swap the kept previous folder back in, or a staged archive of that version."""
    if previous_version() == version:
        _launch_swap(previous_dir(), version)
    elif (staged_app_dir(version) / f"{about.NAME}.exe").exists():
        _apply_portable(version, restart=True)
    else:
        raise InstallError(f"No copy of {version} is kept beside this folder to go back to.")
    seen.record_update(version, "rolled-back")


def rollback_candidates(current: str | None = None) -> list[str]:
    """Versions this copy can go back to, newest first.

    Installed: every staged installer that is not the running one. Portable: the version in
    ``<app>.old``, kept by the last swap. Both read filenames through
    :func:`version_in_asset_name`, which understands every layout ever shipped — otherwise
    the build you upgraded *from* would be invisible to the build you upgraded *to*.
    """
    from cerepulse.core import paths
    from cerepulse.update.downloader import downloads_dir, version_in_asset_name

    running = current or about.VERSION
    if paths.is_portable():
        kept = previous_version()
        return [kept] if kept and kept != running else []

    directory = downloads_dir()
    if not directory.exists():
        return []

    found = [
        str(version)
        for version in (
            version_in_asset_name(file.name) for file in directory.iterdir() if file.is_file()
        )
        if version is not None and str(version) != running
    ]
    return sorted(set(found), reverse=True)


def clear_failed_portable_folder() -> None:
    """Remove ``<app>.failed`` if a swap left one. By construction it never received Data."""
    folder = failed_dir()
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
        logger.info("Removed the failed update folder {}", folder.name)


def _apply_portable(version: str, *, restart: bool) -> None:
    from cerepulse.core import paths

    source = staged_app_dir(version)
    if not (source / f"{about.NAME}.exe").exists():
        raise InstallError(f"The update for {version} is not unpacked yet.")
    if swap_dir().exists():
        raise InstallError(
            f"{swap_dir().name} is left over from an earlier attempt and may hold your data. "
            f"Rename it back to {paths.install_dir().name} by hand, then try again."
        )
    if _UNSAFE_PATH_CHARS & set(str(paths.install_dir())):
        raise InstallError(
            f"Move the {about.NAME} folder to a path without any of & ^ ! % | < > and try again."
        )
    _launch_swap(source, version, restart=restart)
    seen.record_update(version, "installing", f"swapping in {source.parent.name}")


def _launch_swap(source: Path, version: str, *, restart: bool = True) -> None:
    """Write the swap script and start it from *outside* the app folder.

    ``cwd`` is load-bearing: a frozen app's working directory is its own folder, and a
    ``cmd.exe`` sitting inside the folder being renamed makes the first ``move`` fail.
    """
    script = _write_swap_script(source, version, os.getpid(), restart=restart)
    try:
        subprocess.Popen(  # noqa: S603 — argv list, no shell, our own generated script
            ["cmd.exe", "/c", str(script)],
            cwd=str(script.parent),
            creationflags=_NO_WINDOW,
            close_fds=True,
        )
    except OSError as exc:
        raise InstallError(f"Could not start the update helper: {exc}") from exc
    logger.info("Handed over to the folder swap for {}; quitting", version)


def _write_swap_script(source: Path, version: str, pid: int, *, restart: bool) -> Path:
    from cerepulse.core import paths

    directory = helper_dir()
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / f"apply-update-{pid}.cmd"
    log = directory / f"apply-update-{pid}.log"
    script.write_text(
        swap_script_text(
            app=paths.install_dir(),
            source=source,
            version=version,
            previous_version=about.VERSION,
            pid=pid,
            exe_name=Path(sys.executable).name,
            log=log,
            restart=restart,
        ),
        encoding="utf-8",
    )
    return script


def swap_script_text(
    *,
    app: Path,
    source: Path,
    version: str,
    previous_version: str,
    pid: int,
    exe_name: str,
    log: Path,
    restart: bool,
) -> str:
    """The batch file that replaces the app folder once the app has exited. Pure.

    Order: wait for the pid → rename the app folder aside → rename the unpacked build into
    place → move ``Data`` across → keep the old folder as ``<app>.old`` for rollback →
    relaunch. Every step is a same-volume directory rename, so a large ``Data`` costs
    nothing; every failure path renames the old folder back and relaunches it.

    Four rules about cmd, three inherited from the installed script and one new: ``ping``
    rather than ``timeout`` (stdin is not a console); match the image name, not the pid;
    a space before every ``>>`` (a trailing digit is read as a stream handle); and **no
    parenthesised blocks at all** — ``%APP%`` may contain ``(x86)``, and a ``)`` inside an
    ``if (...)`` block ends the block. Every branch is a ``goto``.

    When the source sits inside the app folder (an update), its path is rewritten to where
    it will be after the app folder has been renamed; a rollback's source is a sibling and
    is used as is.
    """
    swap = app.with_name(f"{app.name}.swap")
    try:
        source_after = swap / source.relative_to(app)
    except ValueError:
        source_after = source
    relaunch = 'start "" /D "%APP%" "%EXE%"' if restart else "rem no relaunch requested"
    return rf"""@echo off
rem Generated by {about.NAME} {previous_version}. Safe to delete.
rem Replaces "{app}" with "{source}" once pid {pid} has exited. Data is carried across.
setlocal
set "APP={app}"
set "SWAP={swap}"
set "OLD={app}.old"
set "FAILED={app}.failed"
set "SOURCE_BEFORE={source}"
set "SOURCE={source_after}"
set "EXE={app}\{exe_name}"
set "LOG={log}"
set "APPLOG={app}\Data\updates\apply-update.log"
set "PREVIOUS={previous_version}"
cd /d "%TEMP%"

echo [%DATE% %TIME%] swap %PREVIOUS% to {version}: waiting for {exe_name} pid {pid} >>"%LOG%"
set /a turns=0

:wait
set /a turns+=1
if %turns% GTR {_MAX_WAIT_TURNS} goto still_running
tasklist /NH /FI "PID eq {pid}" 2>nul | find /I "{exe_name}" >nul
if errorlevel 1 goto preflight
ping -n 3 127.0.0.1 >nul
goto wait

:still_running
echo [%DATE% %TIME%] gave up waiting; the app is still running, nothing changed >>"%LOG%"
goto end

:preflight
if exist "%SWAP%" goto swap_exists
if exist "%FAILED%" rd /s /q "%FAILED%" >>"%LOG%" 2>&1
if not exist "%SOURCE_BEFORE%\{exe_name}" goto no_source
set /a tries=0

:move_app
set /a tries+=1
move "%APP%" "%SWAP%" >>"%LOG%" 2>&1
if not errorlevel 1 goto place_new
if %tries% GEQ 15 goto app_busy
ping -n 3 127.0.0.1 >nul
goto move_app

:place_new
echo [%DATE% %TIME%] moved the app folder aside >>"%LOG%"
set /a tries=0

:move_source
set /a tries+=1
move "%SOURCE%" "%APP%" >>"%LOG%" 2>&1
if not errorlevel 1 goto place_data
if %tries% GEQ 15 goto source_busy
ping -n 3 127.0.0.1 >nul
goto move_source

:place_data
if not exist "%EXE%" goto bad_source
if exist "%APP%\Data" goto bad_source
move "%SWAP%\Data" "%APP%\Data" >>"%LOG%" 2>&1
if errorlevel 1 goto data_stuck
echo [%DATE% %TIME%] Data moved into the new folder >>"%LOG%"

:keep_previous
if exist "%OLD%" rd /s /q "%OLD%" >>"%LOG%" 2>&1
move "%SWAP%" "%OLD%" >>"%LOG%" 2>&1
if errorlevel 1 goto old_stuck
>"%OLD%\{PREVIOUS_VERSION_FILE}" echo %PREVIOUS%
echo [%DATE% %TIME%] previous build kept at %OLD% >>"%LOG%"
goto relaunch

:old_stuck
echo [%DATE% %TIME%] previous build left at %SWAP%; delete it by hand, no rollback >>"%LOG%"
goto relaunch

:bad_source
echo [%DATE% %TIME%] the new folder is not a usable {about.NAME} build; restoring >>"%LOG%"
move "%APP%" "%FAILED%" >>"%LOG%" 2>&1
goto restore

:data_stuck
echo [%DATE% %TIME%] could not move Data into the new folder; restoring >>"%LOG%"
move "%APP%" "%FAILED%" >>"%LOG%" 2>&1
goto restore

:source_busy
echo [%DATE% %TIME%] could not place the new build after %tries% tries; restoring >>"%LOG%"
goto restore

:restore
move "%SWAP%" "%APP%" >>"%LOG%" 2>&1
if errorlevel 1 goto restore_failed
echo [%DATE% %TIME%] restored the previous folder; nothing was changed >>"%LOG%"
goto relaunch

:restore_failed
echo [%DATE% %TIME%] RESTORE FAILED. Data is in %SWAP%; rename it to %APP% by hand >>"%LOG%"
goto end

:app_busy
echo [%DATE% %TIME%] the app folder would not rename after %tries% tries; nothing changed >>"%LOG%"
goto relaunch

:no_source
echo [%DATE% %TIME%] nothing to install at %SOURCE_BEFORE%; nothing was changed >>"%LOG%"
goto relaunch

:swap_exists
echo [%DATE% %TIME%] %SWAP% is left from an earlier attempt and may hold Data; refusing >>"%LOG%"
goto relaunch

:relaunch
if exist "%APP%\Data\updates" type "%LOG%" >>"%APPLOG%"
{relaunch}

:end
echo [%DATE% %TIME%] done >>"%LOG%"
endlocal
"""


#: CREATE_NO_WINDOW — a console the user never sees, rather than no console at all.
#:
#: This was DETACHED_PROCESS, and that is what broke the whole update flow. DETACHED_PROCESS
#: gives the child no console whatsoever, and every tool the wait loop is built from —
#: ``tasklist``, ``find``, ``timeout`` — is a console utility. The script started, wrote its
#: first line, and hung in the loop forever: the installer was never reached, so the app quit
#: and simply never came back. Windows does not kill a child when its parent exits, so the
#: helper outlives us either way; a hidden console costs nothing and makes the tools work.
_NO_WINDOW = 0x08000000

#: Roughly five minutes at two seconds a turn. A clean quit takes a second or two, so
#: reaching this means something is wrong — and installing anyway is the better failure,
#: since Inno's CloseApplications can deal with a copy that will not close on its own.
_MAX_WAIT_TURNS = 150


def _handoff_script(installer: Path, pid: int, *, restart: bool) -> Path:
    """Write the batch file that waits for us to exit, installs, and relaunches.

    A script rather than a chain of processes because it has to survive its parent dying,
    which is the whole point: the thing it is waiting for is this process.

    It keeps its own log. The app can only ever report "handed over"; everything after that
    happens once it is gone, so without this a failed update leaves no evidence anywhere.
    """
    from cerepulse.update.downloader import downloads_dir

    directory = downloads_dir()
    target = directory / "apply-update.cmd"
    log = directory / "apply-update.log"
    executable = Path(sys.executable).resolve()
    relaunch = f'start "" "{executable}"' if restart else "rem no relaunch requested"

    # ping, not timeout: timeout reads the console to allow cancellation and dies with
    # "Input redirection is not supported" the moment stdin is anything but a keyboard.
    # A ping to loopback sleeps just as well and needs nothing.
    #
    # Matching the image name rather than the pid, because when the process is gone
    # tasklist still prints an INFO line, and a pid can appear inside one by coincidence.
    #
    # Every echo keeps a space before its ``>>``. Without it a line ending in a number —
    # ``exited with %ERRORLEVEL%>>`` — has its last digit read as a stream handle, so the
    # exit code silently vanishes from the log written to diagnose exit codes.
    target.write_text(
        f"""@echo off
rem Generated by {about.NAME} {about.VERSION}. Safe to delete.
set "LOG={log}"
echo [%DATE% %TIME%] waiting for {executable.name} (pid {pid}) >>"%LOG%"
set /a turns=0

:wait
set /a turns+=1
if %turns% GTR {_MAX_WAIT_TURNS} (
    echo [%DATE% %TIME%] gave up waiting; installing anyway >>"%LOG%"
    goto install
)
tasklist /NH /FI "PID eq {pid}" 2>nul | find /I "{executable.name}" >nul
if errorlevel 1 goto install
ping -n 3 127.0.0.1 >nul
goto wait

:install
echo [%DATE% %TIME%] running {installer.name} >>"%LOG%"
"{installer}" {" ".join(SILENT_FLAGS)} "/LOG={directory / "install.log"}"
echo [%DATE% %TIME%] installer exited with %ERRORLEVEL% >>"%LOG%"
{relaunch}
echo [%DATE% %TIME%] done >>"%LOG%"
""",
        encoding="utf-8",
    )
    return target


__all__ = [
    "SILENT_FLAGS",
    "InstallError",
    "apply_update",
    "clear_failed_portable_folder",
    "is_installed_build",
    "previous_dir",
    "previous_version",
    "rollback_candidates",
    "rollback_portable",
    "rollback_to",
    "swap_script_text",
]
