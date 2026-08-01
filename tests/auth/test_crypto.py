"""The password scheme recovered from login.aspx (see cerepulse.auth.crypto)."""

from __future__ import annotations

import base64

import pytest

from cerepulse.auth.crypto import decrypt_password, encrypt_password

# Synthetic. hEnSa is a 16-digit value the portal renders fresh on every page load, so any
# 16 digits exercise the scheme; using a captured one bought nothing and leaked the key that
# decrypts a captured password.
H_EN_SA = "1234567890123456"


@pytest.mark.parametrize(
    "plain",
    ["a", "hunter2", "P@ssw0rd!", "exactly-15-chr", "sixteen-chars-16", "a much longer passphrase"],
)
def test_round_trip(plain: str) -> None:
    assert decrypt_password(encrypt_password(plain, H_EN_SA), H_EN_SA) == plain


def test_output_is_base64_of_whole_blocks() -> None:
    """PKCS7 means the ciphertext is always a whole number of 16-byte blocks."""
    raw = base64.b64decode(encrypt_password("hunter2", H_EN_SA))
    assert len(raw) % 16 == 0


def test_short_password_yields_a_single_block() -> None:
    """The captured txtPassword was 24 base64 chars — one 16-byte block. Match that shape."""
    encoded = encrypt_password("hunter2", H_EN_SA)
    assert len(encoded) == 24
    assert len(base64.b64decode(encoded)) == 16


def test_exactly_16_bytes_pads_to_two_blocks() -> None:
    """PKCS7 always adds padding, so a full block becomes two."""
    assert len(base64.b64decode(encrypt_password("0123456789abcdef", H_EN_SA))) == 32


def test_is_deterministic() -> None:
    """The IV is the key, not a random value, so the same input always encodes the same."""
    assert encrypt_password("hunter2", H_EN_SA) == encrypt_password("hunter2", H_EN_SA)


def test_different_salt_gives_different_ciphertext() -> None:
    """hEnSa is rendered fresh on every page load, so it must change the output."""
    assert encrypt_password("hunter2", H_EN_SA) != encrypt_password("hunter2", "6543210987654321")


def test_unicode_password_survives_round_trip() -> None:
    """CryptoJS.enc.Utf8.parse means the plaintext is UTF-8, not latin-1."""
    plain = "pä55wörd–ünïcode"
    assert decrypt_password(encrypt_password(plain, H_EN_SA), H_EN_SA) == plain


@pytest.mark.parametrize("bad", ["", "12345", "123456789012345", "12345678901234567"])
def test_wrong_length_salt_is_rejected(bad: str) -> None:
    """A 16-character hEnSa is what makes this AES-128; anything else is a protocol change."""
    with pytest.raises(ValueError, match="16 ASCII characters"):
        encrypt_password("hunter2", bad)


def test_surrounding_whitespace_in_salt_is_tolerated() -> None:
    assert encrypt_password("hunter2", f"  {H_EN_SA} ") == encrypt_password("hunter2", H_EN_SA)


def test_non_ascii_salt_is_rejected() -> None:
    with pytest.raises(UnicodeEncodeError):
        encrypt_password("hunter2", "86735827676156ä3")
