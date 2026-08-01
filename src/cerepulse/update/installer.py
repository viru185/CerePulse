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
            creationflags=_DETACHED,
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


#: CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS — the helper must outlive us.
_DETACHED = 0x00000200 | 0x00000008


def _handoff_script(installer: Path, pid: int, *, restart: bool) -> Path:
    """Write the batch file that waits for us to exit, installs, and relaunches.

    A script rather than a chain of processes because it has to survive its parent dying,
    which is the whole point: the thing it is waiting for is this process.
    """
    from cerepulse.update.downloader import downloads_dir

    target = downloads_dir() / "apply-update.cmd"
    executable = Path(sys.executable).resolve()
    relaunch = f'start "" "{executable}"' if restart else "rem no relaunch requested"

    # /T 2 rather than a tight loop: waiting on a pid we no longer own is not something
    # cmd can do properly, and two seconds is far below the installer's own startup cost.
    target.write_text(
        f"""@echo off
rem Generated by {about.NAME} {about.VERSION}. Safe to delete.
:wait
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
if not errorlevel 1 (
    timeout /T 2 /NOBREAK >nul
    goto wait
)
"{installer}" {" ".join(SILENT_FLAGS)}
{relaunch}
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
