"""Facts about this installation, for the About screen and for reporting a problem.

Everything here is safe to paste into an issue. That is the whole design constraint: the
report names paths, versions and sizes, and never the employee code, the portal URL, the
username or anything from the cache. Someone helping with a bug should not thereby learn
where the user works.

Sizes are computed rather than tracked, because they are asked for rarely and a stale number
is worse than a slow one.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cerepulse import __about__ as about
from cerepulse.core import paths
from cerepulse.repository.schema import SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class Diagnostics:
    """A snapshot of the installation, with nothing identifying in it."""

    version: str
    channel: str
    mode: str
    python: str
    qt: str
    windows: str
    schema: int
    database_bytes: int
    cache_bytes: int
    logs_bytes: int
    updates_bytes: int
    data_root: str
    last_checked: datetime | None
    #: The tail of the update helper's own log. Everything that helper does happens after
    #: the app has quit, so this is the only record of why an install did or did not work.
    last_handoff: str = ""

    def as_text(self) -> str:
        """The paste-into-an-issue form."""
        checked = self.last_checked.strftime("%Y-%m-%d %H:%M") if self.last_checked else "never"
        lines = [
            f"{about.NAME} {self.version} ({self.channel}, {self.mode})",
            f"Windows      {self.windows}",
            f"Python       {self.python}",
            f"Qt           {self.qt}",
            f"Cache schema v{self.schema}",
            f"Database     {human_size(self.database_bytes)}",
            f"Cache        {human_size(self.cache_bytes)}",
            f"Logs         {human_size(self.logs_bytes)}",
            f"Updates      {human_size(self.updates_bytes)}",
            f"Last checked {checked}",
        ]
        if self.last_handoff:
            lines.extend(["", "Last update attempt:", self.last_handoff])
        return "\n".join(lines)


def collect(*, channel: str = "stable") -> Diagnostics:
    """Gather everything the About screen shows."""
    from cerepulse.update.downloader import downloads_dir
    from cerepulse.update.seen import last_checked

    database = paths.database_file()
    return Diagnostics(
        version=about.VERSION,
        channel=channel,
        mode="Portable" if paths.is_portable() else "Installed",
        python=platform.python_version(),
        qt=_qt_version(),
        windows=f"{platform.system()} {platform.release()} ({platform.machine()})",
        schema=SCHEMA_VERSION,
        database_bytes=_file_size(database),
        cache_bytes=directory_size(database.parent),
        logs_bytes=directory_size(paths.logs_dir()),
        updates_bytes=directory_size(downloads_dir()),
        data_root=str(paths.data_root()),
        last_checked=last_checked(),
        last_handoff=handoff_log(downloads_dir()),
    )


def handoff_log(updates: Path, *, lines: int = 8) -> str:
    """The tail of the update helper's log, or nothing when it has never run.

    Safe to paste: the helper only ever records a process id, a file name and an exit code.
    """
    log = updates / "apply-update.log"
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])


def directory_size(directory: Path) -> int:
    """Total bytes under a directory. Missing counts as zero, not as an error."""
    if not directory.exists():
        return 0
    total = 0
    for item in directory.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def human_size(size: int) -> str:
    """``1.4 MB``. Decimal units, because that is what file managers show."""
    if size < 1000:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1000
        if value < 1000:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TB"


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _qt_version() -> str:
    try:
        from PySide6 import __version__

        return str(__version__)
    except ImportError:  # pragma: no cover — PySide6 is a hard dependency
        return sys.version.split()[0]


__all__ = ["Diagnostics", "collect", "directory_size", "handoff_log", "human_size"]
