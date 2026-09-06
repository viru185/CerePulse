"""DPAPI binds a blob to the account that wrote it. Only Windows can say so."""

from __future__ import annotations

import pytest

from cerepulse.core import dpapi

pytestmark = pytest.mark.skipif(not dpapi.available(), reason="DPAPI is Windows-only")


def test_round_trip() -> None:
    secret = b"net pay 1,23,456.00"
    blob = dpapi.protect(secret)
    assert blob != secret
    assert secret not in blob
    assert dpapi.unprotect(blob) == secret


def test_a_tampered_blob_is_refused() -> None:
    blob = bytearray(dpapi.protect(b"figures"))
    blob[-1] ^= 0xFF
    with pytest.raises(OSError):
        dpapi.unprotect(bytes(blob))


def test_garbage_is_refused() -> None:
    with pytest.raises(OSError):
        dpapi.unprotect(b"not a blob")
