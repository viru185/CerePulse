"""How this copy of the app was delivered, which decides how it can update itself.

A leaf module: the checker needs the enum to pick an asset and the installer needs the
runtime answer to pick a script, and neither may import the other.
"""

from __future__ import annotations

import sys
from enum import Enum


class BuildMode(Enum):
    SOURCE = "source"
    INSTALLED = "installed"
    PORTABLE = "portable"


def build_mode() -> BuildMode:
    """SOURCE unless frozen; then PORTABLE if the marker sits beside the exe, else INSTALLED."""
    from cerepulse.core import paths

    if not getattr(sys, "frozen", False):
        return BuildMode.SOURCE
    return BuildMode.PORTABLE if paths.is_portable() else BuildMode.INSTALLED


def can_self_update(mode: BuildMode | None = None) -> bool:
    """A source run has nothing to hand over to; the other two do."""
    return (mode or build_mode()) is not BuildMode.SOURCE


__all__ = ["BuildMode", "build_mode", "can_self_update"]
