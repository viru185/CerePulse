"""Encrypt bytes to the signed-in Windows account, and back.

Salary figures are more sensitive than attendance, and the cache is a plain SQLite file in
a folder any process running as the user can read. The Windows Data Protection API binds a
blob to the user's logon credentials with no key of ours to manage: nothing else on the
machine can read it, another account on the same machine cannot, and a portable copy moved
to another PC simply re-fetches. ``ctypes`` against ``crypt32`` — no new dependency.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from cerepulse import __about__ as about

#: Never show a UI prompt, whatever the policy on the machine says.
_UI_FORBIDDEN = 0x01
#: Mixed into the key so a blob written by another program cannot be handed to this one.
_ENTROPY = f"{about.NAME}.pay".encode()


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def available() -> bool:
    return sys.platform == "win32"


def protect(data: bytes) -> bytes:
    """Encrypt ``data`` for the current user. Raises ``OSError`` when Windows refuses."""
    return _call("CryptProtectData", data)


def unprotect(blob: bytes) -> bytes:
    """Decrypt a blob written by :func:`protect` under the same account."""
    return _call("CryptUnprotectData", blob)


def _call(name: str, payload: bytes) -> bytes:
    if not available():
        raise OSError("DPAPI is only available on Windows")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    def blob(raw: bytes) -> _Blob:
        buffer = ctypes.create_string_buffer(raw, len(raw))
        return _Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))

    source = blob(payload)
    entropy = blob(_ENTROPY)
    out = _Blob()
    ok = getattr(crypt32, name)(
        ctypes.byref(source),
        None,
        ctypes.byref(entropy),
        None,
        None,
        _UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        raise OSError(f"{name} failed: {ctypes.GetLastError()}")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


__all__ = ["available", "protect", "unprotect"]
