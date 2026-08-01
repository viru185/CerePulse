"""The batch file that installs the update after the app has quit.

This is the one piece of the update flow that runs when nothing is left to report a failure,
and it shipped broken in 0.4.0: launched with ``DETACHED_PROCESS`` the helper had no console
at all, and every tool its wait loop was built from — ``tasklist``, ``find``, ``timeout`` —
is a console utility. The script started, hung in the loop forever, and the installer was
never reached. The app quit and simply never came back.

The assertions below are deliberately about the *shape* of the generated script rather than
its behaviour, because the behaviour only manifests in a process with no console and no
parent, which a test cannot be.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cerepulse.update import installer as handoff


@pytest.fixture
def script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(handoff, "downloads_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("cerepulse.update.downloader.downloads_dir", lambda: tmp_path, raising=True)
    written = handoff._handoff_script(tmp_path / "Setup.exe", 4242, restart=True)
    return written.read_text(encoding="utf-8")


def test_the_helper_gets_a_console_even_though_it_shows_no_window() -> None:
    """CREATE_NO_WINDOW, not DETACHED_PROCESS. The distinction is the whole bug."""
    assert handoff._NO_WINDOW == 0x08000000
    assert not hasattr(handoff, "_DETACHED")


def test_the_wait_does_not_use_timeout(script: str) -> None:
    """timeout reads the console to allow cancellation and dies when stdin is redirected."""
    assert "timeout" not in script
    assert "ping -n" in script


def test_the_wait_is_bounded(script: str) -> None:
    """An unbounded loop is how a failed handoff becomes a silent one, forever."""
    assert "GTR" in script
    assert "installing anyway" in script


def test_it_watches_the_pid_it_was_given(script: str) -> None:
    assert 'tasklist /NH /FI "PID eq 4242"' in script


def test_it_runs_the_installer_and_relaunches(script: str) -> None:
    assert "Setup.exe" in script
    for flag in handoff.SILENT_FLAGS:
        assert flag in script
    assert "start " in script


def test_no_echo_ends_in_a_digit_before_its_redirect(script: str) -> None:
    """``exited with %ERRORLEVEL%>>log`` reads the trailing digit as a stream handle.

    The exit code then vanishes from the log written specifically to capture exit codes.
    """
    offenders = [line for line in script.splitlines() if re.search(r"\d>>", line)]
    assert offenders == []


def test_it_keeps_a_log_because_nothing_else_can(script: str) -> None:
    """Everything here happens after the app is gone, so the app cannot report any of it."""
    assert "apply-update.log" in script
    assert "%ERRORLEVEL%" in script


def test_skipping_the_relaunch_is_possible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cerepulse.update.downloader.downloads_dir", lambda: tmp_path, raising=True)
    written = handoff._handoff_script(tmp_path / "Setup.exe", 1, restart=False)
    assert "no relaunch requested" in written.read_text(encoding="utf-8")
