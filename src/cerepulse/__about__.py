"""Project metadata — the single source of truth for version, About screen, and packaging.

``__version__`` is read at build time by hatchling (see ``[tool.hatch.version]`` in
pyproject.toml) and at runtime by the About dialog, the updater, and the InnoSetup build,
so there is exactly one place to bump a release.
"""

from __future__ import annotations

__version__ = "0.8.0-beta.1"

NAME = "CerePulse"
TAGLINE = "Knows when you can leave, before you ask."
SUMMARY = (
    "A smart attendance and leave companion for Windows. Shows your full day breakdown — "
    "worked, break, expected out, overtime, early exit — plus what your history says about "
    "your habits, which days need a swipe request, and the cheapest leave to book for the "
    "longest break, without opening the HR portal."
)
VERSION = __version__
AUTHOR = "Viren Hirpara"
REPO_URL = "https://github.com/viru185/CerePulse"
GITHUB_URL = "https://github.com/viru185"
LINKEDIN_URL = "https://www.linkedin.com/in/hirparaviren/"

# Prior art this app builds on, credited in the About screen.
CREDITS = (
    ("NineToFive", "https://github.com/viru185/ninetofive", "the original day-breakdown engine"),
    ("ReportFlow", "https://github.com/viru185/ReportFlow", "packaging and release tooling"),
)

__all__ = [
    "AUTHOR",
    "CREDITS",
    "GITHUB_URL",
    "LINKEDIN_URL",
    "NAME",
    "REPO_URL",
    "SUMMARY",
    "TAGLINE",
    "VERSION",
    "__version__",
]
