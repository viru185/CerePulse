"""Entry point for ``python -m cerepulse`` and for the frozen build.

PyInstaller needs a real module to point at, and a frozen GUI build must not require the
console-script shim that ``pip install`` normally generates.
"""

from __future__ import annotations

import os
import sys


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
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


_ensure_std_streams()

from cerepulse.cli import main  # noqa: E402 — must follow the stream guard

if __name__ == "__main__":
    sys.exit(main())
