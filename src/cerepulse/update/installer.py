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
import subprocess
import sys
from pathlib import Path

from loguru import logger

from cerepulse import __about__ as about
from cerepulse.update import seen
from cerepulse.update.downloader import installer_path

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


def apply_update(version: str, *, restart: bool = True) -> None:
    """Quit, install, and come back on the new version.

    Returns as soon as the helper is detached; the caller is expected to close the app
    immediately afterwards. The helper waits for this process to disappear before touching
    any files.
    """
    installer = installer_path(version)
    if not installer.exists():
        raise InstallError(f"The installer for {version} is not downloaded.")
    if not is_installed_build():
        raise InstallError(
            "Automatic install only works for an installed build. "
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
    """Reinstall an earlier version whose installer is still staged.

    The only rollback a per-user Inno install offers: same AppId, so it overwrites in place.
    """
    if not installer_path(version).exists():
        raise InstallError(
            f"No installer for {version} is kept locally, so it cannot be rolled back to."
        )
    apply_update(version, restart=True)
    seen.record_update(version, "rolled-back")


def rollback_candidates(current: str | None = None) -> list[str]:
    """Versions with a staged installer that are not the one running."""
    from cerepulse.update.downloader import downloads_dir

    running = current or about.VERSION
    directory = downloads_dir()
    if not directory.exists():
        return []

    found: list[str] = []
    prefix, suffix = f"{about.NAME}-", "-Setup.exe"
    for file in directory.iterdir():
        name = file.name
        if name.startswith(prefix) and name.endswith(suffix):
            version = name[len(prefix) : -len(suffix)]
            if version and version != running:
                found.append(version)
    return sorted(found, reverse=True)


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
    "is_installed_build",
    "rollback_candidates",
    "rollback_to",
]
