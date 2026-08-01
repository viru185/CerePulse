"""The single-instance guard.

Two copies are actively harmful, not just untidy: two SQLite connections to one file, two
tray icons, two refresh timers, and two clients driving one stateful WebForms session whose
``__VIEWSTATE`` each would invalidate for the other.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cerepulse.core import paths
from cerepulse.ui.single_instance import SingleInstance, instance_key


@pytest.fixture
def guard(qapp: object) -> Iterator[SingleInstance]:
    held = SingleInstance("cerepulse-test")
    yield held
    held.release()


def test_the_first_launch_claims_the_lock(guard: SingleInstance) -> None:
    assert guard.try_claim()


def test_a_second_launch_is_refused(guard: SingleInstance, qapp: object) -> None:
    assert guard.try_claim()

    second = SingleInstance("cerepulse-test")
    assert not second.try_claim()


def test_releasing_lets_the_next_launch_through(guard: SingleInstance, qapp: object) -> None:
    """Otherwise a restart would be blocked by the copy that just exited."""
    assert guard.try_claim()
    guard.release()

    second = SingleInstance("cerepulse-test")
    assert second.try_claim()
    second.release()


def test_a_stale_pipe_does_not_lock_the_app_out(qapp: object) -> None:
    """A crash leaves a named pipe behind; it must not become a permanent barricade."""
    from PySide6.QtNetwork import QLocalServer

    orphan = QLocalServer()
    orphan.listen("cerepulse-orphan")
    orphan.close()  # listening stops, the name may linger

    guard = SingleInstance("cerepulse-orphan")
    assert guard.try_claim()
    guard.release()


# --- keying -------------------------------------------------------------------------------


def test_the_key_follows_the_data_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A portable copy and an installed copy are separate apps and must not block each other."""
    monkeypatch.setenv("CEREPULSE_DATA_DIR", str(tmp_path / "one"))
    paths.data_root.cache_clear()
    first = instance_key()

    monkeypatch.setenv("CEREPULSE_DATA_DIR", str(tmp_path / "two"))
    paths.data_root.cache_clear()
    second = instance_key()

    assert first != second


def test_the_key_is_stable_for_one_directory() -> None:
    assert instance_key() == instance_key()


def test_the_key_is_pipe_safe() -> None:
    """Data paths contain characters and lengths a named pipe will not take."""
    key = instance_key()
    assert key.isascii()
    assert "\\" not in key and "/" not in key
    assert len(key) < 64
