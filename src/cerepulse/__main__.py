"""Entry point for ``python -m cerepulse`` and for the frozen build.

PyInstaller needs a real module to point at, and a frozen GUI build must not require the
console-script shim that ``pip install`` normally generates.
"""

from __future__ import annotations

import sys

from cerepulse.cli import main

if __name__ == "__main__":
    sys.exit(main())
