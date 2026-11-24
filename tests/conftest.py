from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from cerepulse.core import paths

# Must be set before any QApplication is constructed, so it lives at import time in the
# root conftest rather than in a package that may load second.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The offscreen platform reads fonts from one folder through FreeType and knows nothing of
# the system's. Without this every glyph is a box of one fixed width, so any test that
# measures text — the layout QC above all — measures nothing real.
if os.name == "nt":
    os.environ.setdefault(
        "QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
    )


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point every test at a throwaway data root so the real profile is never touched."""
    root = tmp_path / "data"
    monkeypatch.setenv("CEREPULSE_DATA_DIR", str(root))
    paths.data_root.cache_clear()
    yield root
    paths.data_root.cache_clear()


@pytest.fixture(autouse=True)
def no_daily_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The quote and picture providers are real web services; no test may reach them.

    Any window a test builds schedules a daily fetch, and a test that lives long enough —
    the layout QC does — would otherwise ask ZenQuotes, Animechan and Bing for real. Tests
    that exercise the providers inject their own fetchers, which this leaves alone.
    """
    from cerepulse.core.errors import DailyError
    from cerepulse.daily import animechan, bing, zenquotes

    def offline(**_kwargs: object) -> object:
        raise DailyError("no network in tests")

    for module in (animechan, bing, zenquotes):
        monkeypatch.setattr(module, "fetch_today", offline)


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may reach a real server — the employer's HR portal least of all.

    A window built in a test schedules a sign-in, a sync and an update check, and any test
    that spins the event loop lets them run: one did, and its sync went looking for the live
    portal. Tests that talk HTTP use respx, a ``MockTransport`` or replace ``get`` and
    ``post``, all of which answer above the socket; only opening a real connection is refused.
    """
    import httpcore
    from httpcore._backends import sync as backend

    def refused(self: object, host: str, port: int, *_args: object, **_kwargs: object) -> object:
        raise httpcore.ConnectError(f"tests never reach the network ({host}:{port})")

    monkeypatch.setattr(backend.SyncBackend, "connect_tcp", refused)


@pytest.fixture(scope="session")
def qapp() -> Iterator[object]:
    """One QApplication for the whole session; Qt forbids a second.

    Shared beyond ``tests/ui`` because the tray is a Qt object too, and it was previously
    untested precisely because there was no application to construct it against.
    """
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()  # type: ignore[union-attr]
