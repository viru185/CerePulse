"""The numeric version the Windows resource and the installer both demand.

Found by cutting the first beta: the build crashed on ``int('0-beta')``. The version
resource is a fixed struct of four 16-bit fields and simply cannot hold a label, and Inno's
``VersionInfoVersion`` has the same constraint — so a pre-release tag failed to package at
all, which would have made the beta channel unusable the moment it was needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from build_all import numeric_version  # noqa: E402


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.4.0", (0, 4, 0, 0)),
        ("1.2.3", (1, 2, 3, 0)),
        ("0.4.0-beta.1", (0, 4, 0, 0)),
        ("0.4.0-rc1", (0, 4, 0, 0)),
        ("1.0.0+build7", (1, 0, 0, 0)),
        ("0.1", (0, 1, 0, 0)),
    ],
)
def test_a_label_never_reaches_the_numeric_fields(
    version: str, expected: tuple[int, int, int, int]
) -> None:
    assert numeric_version(version) == expected


def test_it_always_returns_four_numbers() -> None:
    """The struct has four fields; fewer is a malformed resource, not a shorter one."""
    for version in ("1", "1.2", "1.2.3", "1.2.3.4"):
        assert len(numeric_version(version)) == 4


def test_nonsense_degrades_to_zeros_rather_than_crashing() -> None:
    """A build must not die on the version string; a wrong resource is recoverable."""
    assert numeric_version("nightly") == (0, 0, 0, 0)
