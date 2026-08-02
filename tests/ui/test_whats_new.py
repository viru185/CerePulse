"""Reading one release's notes out of the bundled changelog.

This had no test at all, which is how five releases shipped showing "No release notes were
published for this version." The lookup was never broken — `CHANGELOG.md` was, because CI
regenerated it *after* PyInstaller had already bundled it. A test here would not have caught
that either; what it catches is the half that is code, and the fallback added alongside it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cerepulse.ui import whats_new
from cerepulse.ui.whats_new import changelog_section, render_notes

CHANGELOG = """\
# Changelog

## [0.10.0] - 2026-08-10

### Bug Fixes

- The session truce now actually fires

## [0.9.0] - 2026-08-02

### Features

- Records replaces Leave and Requests

## [0.4.0] - 2026-08-01

- Auto-update
"""


@pytest.fixture(autouse=True)
def bundled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the lookup at a changelog this test owns, not the repository's."""
    path = tmp_path / "CHANGELOG.md"
    path.write_text(CHANGELOG, encoding="utf-8")
    monkeypatch.setattr(whats_new, "_bundled_changelog", lambda: path.read_text(encoding="utf-8"))


def test_a_version_finds_its_own_section() -> None:
    notes = changelog_section("0.10.0")
    assert "session truce" in notes
    assert "Records replaces" not in notes, "it must stop at the next heading"


def test_a_leading_v_is_tolerated() -> None:
    """The tag is `v0.10.0`; the heading is `[0.10.0]`."""
    assert changelog_section("v0.10.0") == changelog_section("0.10.0")


def test_the_last_section_in_the_file_still_terminates() -> None:
    assert "Auto-update" in changelog_section("0.4.0")


def test_a_version_that_is_not_there_yields_nothing() -> None:
    assert changelog_section("0.3.0") == ""


def test_a_beta_falls_back_to_its_base_version() -> None:
    """A beta is cut before the release heading it belongs to exists, so its own section is
    usually absent. Showing the release's notes beats showing none."""
    notes = changelog_section("0.9.0-beta.1")
    assert "Records replaces" in notes


def test_the_fallback_says_which_version_it_is_showing() -> None:
    """Passing 0.9.0's notes off as 0.9.0-beta.1's would be worse than showing nothing —
    the user would read about changes their build does not have."""
    notes = changelog_section("0.9.0-beta.1")
    assert "0.9.0" in notes.splitlines()[0]
    assert "0.9.0-beta.1" in notes.splitlines()[0]


def test_a_beta_with_its_own_section_does_not_fall_back() -> None:
    assert changelog_section("0.10.0").startswith("###")


def test_a_beta_whose_base_is_also_missing_yields_nothing() -> None:
    assert changelog_section("0.3.0-beta.1") == ""


def test_no_bundled_changelog_is_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(whats_new, "_bundled_changelog", lambda: "")
    assert changelog_section("0.10.0") == ""


def test_empty_notes_render_as_a_sentence_rather_than_a_blank_dialog() -> None:
    assert "No release notes" in render_notes("")
