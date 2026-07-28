"""Version comparison, update checking, release-note rendering, and seen tracking."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from cerepulse.ui.whats_new import render_notes
from cerepulse.update import seen
from cerepulse.update.checker import LATEST_RELEASE_URL, check_for_update
from cerepulse.update.version import Version, is_newer


def release_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "tag_name": "v0.2.0",
        "name": "CerePulse 0.2.0",
        "body": "## Added\n- Tray countdown\n",
        "html_url": "https://github.com/viru185/CerePulse/releases/tag/v0.2.0",
        "published_at": "2026-08-01T10:00:00Z",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "CerePulse-0.2.0-Setup.exe",
                "browser_download_url": "https://example.invalid/Setup.exe",
            }
        ],
    }
    payload.update(overrides)
    return payload


# --- version ordering -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.2.3", (1, 2, 3, "")),
        ("v1.2.3", (1, 2, 3, "")),
        ("0.1", (0, 1, 0, "")),
        ("1.0.0-rc1", (1, 0, 0, "rc1")),
    ],
)
def test_versions_parse(text: str, expected: tuple[int, int, int, str]) -> None:
    version = Version.parse(text)
    assert version is not None
    assert (version.major, version.minor, version.patch, version.label) == expected


@pytest.mark.parametrize("text", ["", "latest", "nightly", "v"])
def test_unparseable_versions_return_none(text: str) -> None:
    assert Version.parse(text) is None


@pytest.mark.parametrize(
    ("candidate", "current"),
    [("0.2.0", "0.1.0"), ("1.0.0", "0.9.9"), ("0.1.1", "0.1.0"), ("v0.2.0", "0.1.0")],
)
def test_newer_versions_are_detected(candidate: str, current: str) -> None:
    assert is_newer(candidate, current)


@pytest.mark.parametrize(
    ("candidate", "current"),
    [("0.1.0", "0.1.0"), ("0.1.0", "0.2.0"), ("0.9.9", "1.0.0")],
)
def test_older_or_equal_versions_are_not_newer(candidate: str, current: str) -> None:
    assert not is_newer(candidate, current)


def test_a_prerelease_ranks_below_its_release() -> None:
    """1.0.0-rc1 must never look newer than 1.0.0."""
    assert not is_newer("1.0.0-rc1", "1.0.0")
    assert is_newer("1.0.0", "1.0.0-rc1")


def test_a_malformed_tag_never_triggers_an_update() -> None:
    assert not is_newer("nightly", "0.1.0")
    assert not is_newer("0.2.0", "garbage")


# --- update check ---------------------------------------------------------------------


@respx.mock
def test_a_newer_release_is_reported() -> None:
    respx.get(LATEST_RELEASE_URL).mock(return_value=httpx.Response(200, json=release_payload()))
    release = check_for_update("0.1.0")

    assert release is not None
    assert release.version == "0.2.0"
    assert release.display_name == "CerePulse 0.2.0"
    assert release.installer_url == "https://example.invalid/Setup.exe"
    assert release.published_at is not None


@respx.mock
def test_the_same_version_is_not_an_update() -> None:
    respx.get(LATEST_RELEASE_URL).mock(return_value=httpx.Response(200, json=release_payload()))
    assert check_for_update("0.2.0") is None


@respx.mock
def test_drafts_and_prereleases_are_ignored() -> None:
    respx.get(LATEST_RELEASE_URL).mock(
        return_value=httpx.Response(200, json=release_payload(prerelease=True))
    )
    assert check_for_update("0.1.0") is None


@respx.mock
def test_a_network_failure_is_silent() -> None:
    """An update check failing must never interrupt someone reading their attendance."""
    respx.get(LATEST_RELEASE_URL).mock(side_effect=httpx.ConnectError("offline"))
    assert check_for_update("0.1.0") is None


@respx.mock
def test_a_rate_limit_is_silent() -> None:
    respx.get(LATEST_RELEASE_URL).mock(return_value=httpx.Response(403, json={}))
    assert check_for_update("0.1.0") is None


@respx.mock
def test_malformed_json_is_silent() -> None:
    respx.get(LATEST_RELEASE_URL).mock(return_value=httpx.Response(200, text="not json"))
    assert check_for_update("0.1.0") is None


@respx.mock
def test_a_release_without_an_installer_still_reports() -> None:
    respx.get(LATEST_RELEASE_URL).mock(
        return_value=httpx.Response(200, json=release_payload(assets=[]))
    )
    release = check_for_update("0.1.0")
    assert release is not None
    assert release.installer_url == ""


# --- release notes --------------------------------------------------------------------


def test_headings_and_bullets_render() -> None:
    html = render_notes("## Added\n- Tray countdown\n- Offline history\n")
    assert "<h4>Added</h4>" in html
    assert html.count("<li>") == 2
    assert "<ul>" in html and "</ul>" in html


def test_inline_markup_renders() -> None:
    html = render_notes("- **Bold** and `code` and [a link](https://example.com)")
    assert "<b>Bold</b>" in html
    assert "<code>code</code>" in html
    assert '<a href="https://example.com">a link</a>' in html


def test_notes_cannot_inject_markup() -> None:
    """Release bodies are third-party text; they render as text, not as trusted HTML."""
    html = render_notes("- <script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_empty_notes_say_so() -> None:
    assert "No release notes" in render_notes("")
    assert "No release notes" in render_notes("   \n  ")


def test_paragraphs_close_an_open_list() -> None:
    html = render_notes("- one\n\nAfterwards\n")
    assert html.index("</ul>") < html.index("<p>Afterwards</p>")


# --- seen tracking --------------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CEREPULSE_DATA_DIR", str(tmp_path))
    from cerepulse.core import paths

    paths.data_root.cache_clear()
    yield tmp_path
    paths.data_root.cache_clear()


def test_a_first_run_does_not_show_release_notes(data_dir: Path) -> None:
    """Someone installing fresh does not need to know what changed since a version they
    never had."""
    assert not seen.should_show_whats_new("0.1.0")
    assert seen.last_seen_version() == "0.1.0"


def test_notes_show_once_after_an_upgrade(data_dir: Path) -> None:
    seen.mark_seen("0.1.0")
    assert seen.should_show_whats_new("0.2.0")

    seen.mark_seen("0.2.0")
    assert not seen.should_show_whats_new("0.2.0")


def test_a_corrupt_state_file_is_tolerated(data_dir: Path) -> None:
    seen.state_file().write_text("{ not json", encoding="utf-8")
    assert seen.last_seen_version() is None
    seen.mark_seen("0.3.0")
    assert seen.last_seen_version() == "0.3.0"


def test_unrelated_state_keys_survive(data_dir: Path) -> None:
    import json

    seen.state_file().write_text(json.dumps({"other": 1}), encoding="utf-8")
    seen.mark_seen("0.2.0")

    data = json.loads(seen.state_file().read_text(encoding="utf-8"))
    assert data["other"] == 1
    assert data[seen.KEY] == "0.2.0"
