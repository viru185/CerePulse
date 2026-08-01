"""Redaction is a security control (Chapter 14 section 8), so it is tested like one."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from loguru import logger

from cerepulse.core.logging_setup import configure_logging, redact

# Synthetic throughout. These have the shape of the real thing — a long opaque ticket, a
# base64 password blob, a 16-digit hEnSa — but none of them came from a live session. An
# earlier version of this file used a genuine capture, which put a real encrypted password
# in the repository alongside the key that decrypts it.
_AUTH_TICKET = "A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90"
_VIEW_STATE = "c3ludGhldGljVmlld1N0YXRl"
_EVENT_VALIDATION = "c3ludGhldGljRXZlbnRWYWxpZGF0aW9u"
_PASSWORD_BLOB = "F1b3V8lVXXdfjzMIDhc5hA%3D%3D"
_H_EN_SA = "1234567890123456"


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        (f"__VIEWSTATE={_VIEW_STATE}&__EVENTTARGET=btnLogin", _VIEW_STATE),
        (f"txtPassword={_PASSWORD_BLOB}&txtUser=CIPL00364", _PASSWORD_BLOB),
        (f"hEnSa={_H_EN_SA}", _H_EN_SA),
        (f"__EVENTVALIDATION={_EVENT_VALIDATION}", _EVENT_VALIDATION),
    ],
)
def test_form_secrets_are_removed(raw: str, secret: str) -> None:
    """Each field is checked against its own value, not against a shared list.

    The list version could pass while leaking: a value absent from the output because it
    was never in the input reads exactly like one that was properly redacted.
    """
    out = redact(raw)
    assert "<redacted>" in out
    assert secret not in out


def test_non_secret_fields_survive() -> None:
    out = redact("__VIEWSTATE=abc123&txtUser=CIPL00364&__EVENTTARGET=btnLogin")
    assert "txtUser=CIPL00364" in out
    assert "__EVENTTARGET=btnLogin" in out
    assert "abc123" not in out


def test_cookie_header_is_redacted_wholesale() -> None:
    out = redact(f"cookie: ASP.NET_SessionId=4de0ydkk2ak; .ASPXFORMSAUTH={_AUTH_TICKET}")
    assert _AUTH_TICKET not in out
    assert "4de0ydkk2ak" not in out


def test_set_cookie_header_is_redacted() -> None:
    out = redact(f"set-cookie: .ASPXFORMSAUTH={_AUTH_TICKET}; path=/; HttpOnly")
    assert _AUTH_TICKET not in out


def test_cookie_pair_inside_a_sentence_is_redacted() -> None:
    out = redact(f"sending request with .ASPXFORMSAUTH={_AUTH_TICKET} attached")
    assert _AUTH_TICKET not in out
    assert "<redacted>" in out


def test_dict_repr_is_redacted() -> None:
    payload = {"txtPassword": "hunter2", "txtUser": "CIPL00364"}
    out = redact(repr(payload))
    assert "hunter2" not in out
    assert "CIPL00364" in out


def test_redaction_is_case_insensitive() -> None:
    assert "secret" not in redact("TXTPASSWORD=secret")


def test_clean_text_is_untouched() -> None:
    message = "GET /Atten/MyAttendanceReport.aspx -> 200 in 412ms"
    assert redact(message) == message


def test_console_sink_is_skipped_when_there_is_no_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A windowed build has sys.stderr set to None, and loguru refuses a None sink.

    Regression test: this crashed the frozen app on launch from Explorer, before it could
    open a window. It never reproduced from a terminal, which inherits usable handles.
    """
    monkeypatch.setattr(sys, "stderr", None)

    configure_logging(level="INFO", log_dir=tmp_path, console=True)
    logger.info("still reaches the file sink")

    written = list(tmp_path.glob("*.log"))
    assert written, "the file sink must still be installed"
    assert "still reaches the file sink" in written[0].read_text(encoding="utf-8")
