"""Version comparison.

Only what a release check needs: parse ``1.2.3`` with an optional ``v`` prefix and an
optional pre-release suffix, and order two of them. A pre-release sorts *before* the release
it leads to, so ``1.1.0-rc1`` never looks newer than ``1.1.0``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

_PATTERN = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?(?:[-+](?P<label>.+))?$"
)


@total_ordering
@dataclass(frozen=True, slots=True)
class Version:
    """A comparable release version."""

    major: int
    minor: int
    patch: int = 0
    label: str = ""

    @classmethod
    def parse(cls, text: str) -> Version | None:
        """Parse a tag or version string. Returns None when it is not a version at all."""
        match = _PATTERN.match(text.strip())
        if match is None:
            return None
        return cls(
            major=int(match["major"]),
            minor=int(match["minor"]),
            patch=int(match["patch"] or 0),
            label=match["label"] or "",
        )

    @property
    def is_prerelease(self) -> bool:
        return bool(self.label)

    def _key(self) -> tuple[int, int, int, int]:
        # A pre-release ranks below the same numeric version without a label.
        return (self.major, self.minor, self.patch, 0 if self.label else 1)

    def __lt__(self, other: Version) -> bool:
        if self._key() != other._key():
            return self._key() < other._key()
        # Same numbers and both pre-releases: fall back to lexical order for stability.
        return self.label < other.label

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return f"{base}-{self.label}" if self.label else base


def is_newer(candidate: str, current: str) -> bool:
    """Whether ``candidate`` is a strictly newer release than ``current``.

    Unparseable input is treated as "not newer": a malformed tag must never trigger an
    update prompt.
    """
    new = Version.parse(candidate)
    old = Version.parse(current)
    if new is None or old is None:
        return False
    return new > old
