"""Entry point for ``python -m cerepulse`` and for the frozen build.

PyInstaller needs a real module to point at, and a frozen GUI build must not require the
console-script shim that ``pip install`` normally generates.
"""

from __future__ import annotations

import os
import sys


def _attach_parent_console() -> bool:
    """Borrow the console of whatever launched us, if there is one.

    A windowed build has no console of its own, so ``CerePulse.exe paths`` typed into a
    terminal printed into ``os.devnull`` and looked like it had done nothing — which is
    exactly the command someone runs when they are trying to find the log directory.
    ``ATTACH_PARENT_PROCESS`` gives back the terminal's own handles when one exists, and
    fails harmlessly when the app was double-clicked instead.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        if not ctypes.windll.kernel32.AttachConsole(-1):
            return False
        # Only the streams that are actually missing. A redirected run
        # (`CerePulse.exe paths > out.txt`) inherits a real handle for stdout, and taking
        # it over with CONOUT$ would send the output to the terminal instead of the file
        # the user asked for.
        for name in ("stdout", "stderr"):
            if getattr(sys, name, None) is None:
                setattr(sys, name, open("CONOUT$", "w", encoding="utf-8", buffering=1))
    except Exception:  # noqa: BLE001 — no console is the normal case, not an error
        return False
    return True


def _ensure_std_streams() -> None:
    """Give the process usable stdout/stderr before anything tries to write.

    A windowed PyInstaller build launched from Explorer or the Start Menu has no console,
    and Python sets ``sys.stdout``/``sys.stderr`` to ``None`` rather than to a null stream.
    Anything that writes to them then raises — loguru refuses a ``None`` sink outright, and
    a bare ``print`` fails the same way — so the app died before it could open a window.

    It does not reproduce when the same executable is started from a terminal, because the
    process inherits that terminal's handles. Launching from a shell is therefore not a
    valid test of a windowed build.
    """
    if any(getattr(sys, name, None) is None for name in ("stdout", "stderr")):
        _attach_parent_console()
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


_ensure_std_streams()

from cerepulse.cli import main  # noqa: E402 — must follow the stream guard

if __name__ == "__main__":
    sys.exit(main())
