"""Work out the next version from the commits, and write it where it lives.

    uv run python tools/bump.py                    # say what the next version would be
    uv run python tools/bump.py --write            # write __about__.py and CHANGELOG.md
    uv run python tools/bump.py --version 0.17.0 --write

Versions are plain numbers, bumped by what changed since the last tag — a ``fix`` bumps the
patch, a ``feat`` the minor, a breaking change (``feat!:`` or a ``BREAKING CHANGE:`` footer)
the major — as ``[bump]`` in ``cliff.toml`` tells git-cliff. Every release is published as a
beta; promoting one to stable flips its flag on GitHub and is not a new version. See
``docs/releasing.md``.

A pre-release suffix (``-beta.3``) is refused: that scheme is retired, and git-cliff still
proposes one when the last tag carried a suffix, which is why the first plain version is
given by hand.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ABOUT = ROOT / "src" / "cerepulse" / "__about__.py"
PLAIN = re.compile(r"^\d+\.\d+\.\d+$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", help="use this version instead of the computed one")
    parser.add_argument("--write", action="store_true", help="write __about__.py and the changelog")
    args = parser.parse_args()

    current = re.search(r'__version__ = "([^"]+)"', ABOUT.read_text(encoding="utf-8"))
    if current is None:
        print(f"No __version__ in {ABOUT}", file=sys.stderr)
        return 1
    proposed = (args.version or _bumped()).lstrip("v")
    if not PLAIN.match(proposed):
        print(
            f"git-cliff proposes {proposed!r}, which carries a pre-release suffix. Betas are "
            "plain numbers now; pass --version X.Y.Z (this happens once, right after a "
            "suffixed tag).",
            file=sys.stderr,
        )
        return 2

    print(f"{current.group(1)} -> {proposed}")
    if not args.write:
        return 0

    ABOUT.write_text(
        ABOUT.read_text(encoding="utf-8").replace(
            f'__version__ = "{current.group(1)}"', f'__version__ = "{proposed}"'
        ),
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "cliff", "--config", "cliff.toml", "--tag", f"v{proposed}", "-o", "CHANGELOG.md"],
        cwd=ROOT,
        check=True,
    )
    print(
        "Written. Next:\n"
        f'  git commit -am "chore: bump to {proposed}"\n'
        "  git push origin dev\n"
        f'  git tag -a v{proposed} -m "CerePulse {proposed}" && git push origin v{proposed}'
    )
    return 0


def _bumped() -> str:
    completed = subprocess.run(
        ["git", "cliff", "--bumped-version"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip().splitlines()[-1]


if __name__ == "__main__":
    raise SystemExit(main())
