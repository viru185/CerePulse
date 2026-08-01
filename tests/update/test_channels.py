"""Release channels, downloading and rollback.

The rule that shapes most of this: a beta installation sees *both* channels, so once a
stable release overtakes the beta someone is running they are offered it and land back on
the stable track without doing anything.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
import respx

from cerepulse.update import downloader
from cerepulse.update.channel import Channel
from cerepulse.update.checker import RELEASES_URL, check_for_update
from cerepulse.update.downloader import DownloadError, download_installer, fetch_checksum
from cerepulse.update.version import Version, is_newer


def release(tag: str, *, prerelease: bool = False, asset: bool = True) -> dict[str, object]:
    assets = (
        [
            {
                "name": f"CerePulse-{tag.lstrip('v')}-Setup.exe",
                "browser_download_url": f"https://example.test/{tag}/Setup.exe",
                "size": 1024,
            }
        ]
        if asset
        else []
    )
    return {
        "tag_name": tag,
        "name": f"CerePulse {tag}",
        "body": "notes",
        "html_url": f"https://example.test/{tag}",
        "prerelease": prerelease,
        "draft": False,
        "assets": assets,
    }


# --- channels ---------------------------------------------------------------------------


def test_a_typo_in_the_channel_never_opts_someone_into_betas() -> None:
    assert Channel.parse("beta") is Channel.BETA
    assert Channel.parse("Beta ") is Channel.BETA
    assert Channel.parse("bета") is Channel.STABLE  # Cyrillic lookalike
    assert Channel.parse("") is Channel.STABLE


@respx.mock
def test_stable_never_sees_a_prerelease() -> None:
    respx.get(RELEASES_URL).mock(
        return_value=httpx.Response(200, json=[release("v0.5.0-beta.1", prerelease=True)])
    )
    assert check_for_update("0.4.0", channel=Channel.STABLE) is None


@respx.mock
def test_beta_sees_prereleases() -> None:
    respx.get(RELEASES_URL).mock(
        return_value=httpx.Response(200, json=[release("v0.5.0-beta.1", prerelease=True)])
    )
    found = check_for_update("0.4.0", channel=Channel.BETA)

    assert found is not None
    assert found.version == "0.5.0-beta.1"
    assert found.prerelease


@respx.mock
def test_beta_is_still_offered_stable_releases() -> None:
    """Otherwise a beta user is stranded once the stable release overtakes them."""
    respx.get(RELEASES_URL).mock(
        return_value=httpx.Response(
            200,
            json=[release("v0.5.0"), release("v0.5.0-beta.1", prerelease=True)],
        )
    )
    found = check_for_update("0.5.0-beta.1", channel=Channel.BETA)

    assert found is not None
    assert found.version == "0.5.0"
    assert not found.prerelease


@respx.mock
def test_the_newest_wins_whatever_order_the_api_returns() -> None:
    respx.get(RELEASES_URL).mock(
        return_value=httpx.Response(
            200, json=[release("v0.3.0"), release("v0.9.0"), release("v0.5.0")]
        )
    )
    found = check_for_update("0.2.0", channel=Channel.STABLE)
    assert found is not None and found.version == "0.9.0"


@respx.mock
def test_a_draft_is_never_offered() -> None:
    draft = release("v9.9.9")
    draft["draft"] = True
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=[draft]))
    assert check_for_update("0.1.0", channel=Channel.BETA) is None


@respx.mock
def test_a_release_with_no_installer_is_reported_but_not_installable() -> None:
    respx.get(RELEASES_URL).mock(
        return_value=httpx.Response(200, json=[release("v0.5.0", asset=False)])
    )
    found = check_for_update("0.4.0")
    assert found is not None and not found.is_installable


@respx.mock
def test_an_outage_is_silent_rather_than_fatal() -> None:
    respx.get(RELEASES_URL).mock(side_effect=httpx.ConnectError("offline"))
    assert check_for_update("0.1.0") is None


# --- version ordering ---------------------------------------------------------------------


def test_a_prerelease_sorts_below_its_release() -> None:
    assert Version.parse("1.0.0-beta.1") < Version.parse("1.0.0")
    assert not is_newer("1.0.0-beta.1", "1.0.0")


def test_beta_10_is_newer_than_beta_9() -> None:
    """Lexically it is not, which would offer a beta user an older build than they have."""
    assert is_newer("0.5.0-beta.10", "0.5.0-beta.9")
    assert not is_newer("0.5.0-beta.9", "0.5.0-beta.10")


def test_a_prerelease_of_a_later_version_still_wins() -> None:
    assert is_newer("0.6.0-beta.1", "0.5.0")


# --- downloading --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def staged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "updates"
    monkeypatch.setattr(downloader, "downloads_dir", lambda: directory)
    return directory


@respx.mock
def test_a_download_is_verified_against_its_checksum(staged: Path) -> None:
    body = b"pretend installer"
    respx.get("https://example.test/Setup.exe").mock(return_value=httpx.Response(200, content=body))

    result = download_installer(
        "https://example.test/Setup.exe",
        "0.5.0",
        expected_sha256=hashlib.sha256(body).hexdigest(),
    )
    assert result.verified
    assert result.path.read_bytes() == body


@respx.mock
def test_a_mismatched_checksum_is_discarded_rather_than_run(staged: Path) -> None:
    """This file is about to be executed, so it fails closed."""
    respx.get("https://example.test/Setup.exe").mock(
        return_value=httpx.Response(200, content=b"tampered")
    )

    with pytest.raises(DownloadError, match="did not match"):
        download_installer("https://example.test/Setup.exe", "0.5.0", expected_sha256="0" * 64)
    assert not list(staged.glob("*Setup.exe"))


@respx.mock
def test_a_failed_download_leaves_nothing_runnable(staged: Path) -> None:
    respx.get("https://example.test/Setup.exe").mock(side_effect=httpx.ConnectError("dropped"))

    with pytest.raises(DownloadError):
        download_installer("https://example.test/Setup.exe", "0.5.0")
    assert not list(staged.glob("*"))


@respx.mock
def test_an_already_downloaded_installer_is_not_fetched_again(staged: Path) -> None:
    staged.mkdir(parents=True, exist_ok=True)
    downloader.installer_path("0.5.0").write_bytes(b"already here")
    route = respx.get("https://example.test/Setup.exe").mock(
        return_value=httpx.Response(200, content=b"x")
    )

    download_installer("https://example.test/Setup.exe", "0.5.0")
    assert not route.called


@respx.mock
def test_a_missing_checksum_file_is_not_an_error() -> None:
    """Older releases published none, and refusing to update over that would be worse."""
    respx.get("https://example.test/SHA256SUMS.txt").mock(return_value=httpx.Response(404))
    assert fetch_checksum("https://example.test/SHA256SUMS.txt", "Setup.exe") is None


@respx.mock
def test_the_checksum_for_the_right_asset_is_picked() -> None:
    respx.get("https://example.test/SHA256SUMS.txt").mock(
        return_value=httpx.Response(
            200, text="aaa  CerePulse-0.5.0-portable.zip\nbbb  CerePulse-0.5.0-Setup.exe\n"
        )
    )
    assert (
        fetch_checksum("https://example.test/SHA256SUMS.txt", "CerePulse-0.5.0-Setup.exe") == "bbb"
    )


# --- rollback -----------------------------------------------------------------------------


def test_rollback_offers_only_staged_versions_other_than_the_running_one(staged: Path) -> None:
    from cerepulse.update.installer import rollback_candidates

    staged.mkdir(parents=True, exist_ok=True)
    for version in ("0.3.0", "0.4.0", "0.5.0"):
        downloader.installer_path(version).write_bytes(b"x")

    assert rollback_candidates("0.5.0") == ["0.4.0", "0.3.0"]


def test_rollback_refuses_a_version_it_does_not_have(staged: Path) -> None:
    from cerepulse.update.installer import InstallError, rollback_to

    with pytest.raises(InstallError, match="kept locally"):
        rollback_to("0.1.0")
