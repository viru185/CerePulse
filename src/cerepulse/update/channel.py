"""Release channels.

Two, and no more. **Stable** is what everyone gets: full releases published from ``main``.
**Beta** additionally sees prereleases published from ``dev``, so a build can be proven on
one machine before it reaches anyone else.

The asymmetry is deliberate and worth stating, because it is what makes switching safe:
a beta user sees *both* channels, so once a stable release overtakes the beta they were on
they are offered it and land back on the stable track without doing anything. Switching
from beta to stable does not roll anything back on its own — the app cannot un-install a
newer build — but it stops offering prereleases, and the next stable release picks them up.
"""

from __future__ import annotations

from enum import Enum


class Channel(Enum):
    """Which releases this installation is willing to be offered."""

    STABLE = "stable"
    BETA = "beta"

    @classmethod
    def parse(cls, value: str) -> Channel:
        """Resolve a configured string, defaulting to stable rather than raising.

        A typo in the config must not opt someone into prereleases.
        """
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return cls.STABLE

    @property
    def label(self) -> str:
        return {Channel.STABLE: "Stable", Channel.BETA: "Beta"}[self]

    @property
    def accepts_prereleases(self) -> bool:
        return self is Channel.BETA

    @property
    def description(self) -> str:
        return {
            Channel.STABLE: "Tested releases only.",
            Channel.BETA: "Early builds as well. Expect the occasional rough edge.",
        }[self]


__all__ = ["Channel"]
