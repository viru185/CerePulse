"""Client-side password encryption, reproducing the portal's login JavaScript.

The login page does not send the password in plaintext. ``login.aspx`` defines::

    var txtEnc = function (v, k) {
        return CryptoJS.AES.encrypt(CryptoJS.enc.Utf8.parse(v), k, {
            keySize: 128 / 8, iv: k, mode: CryptoJS.mode.CBC, padding: CryptoJS.pad.Pkcs7
        });
    }

    function InputCheck(txtp) {
        var sd = CryptoJS.enc.Utf8.parse($("#hEnSa").val());
        txtp.val(txtEnc(txtp.val(), sd));
    }

So the key *and* the IV are both the raw UTF-8 bytes of the ``hEnSa`` hidden field — a
16-digit number, hence AES-128. CryptoJS receives a WordArray key rather than a passphrase,
which means it skips the OpenSSL ``Salted__`` header and the resulting string is plain
base64 of the ciphertext.

``hEnSa`` is re-rendered on every page load, so it must be scraped fresh each time and
never cached.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# AES block size in bits, and therefore the required length of hEnSa in ASCII characters.
_BLOCK_BITS = 128
_KEY_BYTES = _BLOCK_BITS // 8


def _key_material(h_en_sa: str) -> bytes:
    key = h_en_sa.strip().encode("ascii", errors="strict")
    if len(key) != _KEY_BYTES:
        raise ValueError(
            f"hEnSa must be exactly {_KEY_BYTES} ASCII characters to form an AES-128 key, "
            f"got {len(key)}"
        )
    return key


def encrypt_password(plain: str, h_en_sa: str) -> str:
    """Return the base64 value the portal expects in the ``txtPassword`` field."""
    key = _key_material(h_en_sa)
    padder = padding.PKCS7(_BLOCK_BITS).padder()
    padded = padder.update(plain.encode("utf-8")) + padder.finalize()

    encryptor = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode("ascii")


def decrypt_password(encoded: str, h_en_sa: str) -> str:
    """Inverse of :func:`encrypt_password`. Exists so the scheme can be round-trip tested."""
    key = _key_material(h_en_sa)
    ciphertext = base64.b64decode(encoded)

    decryptor = Cipher(algorithms.AES(key), modes.CBC(key)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()

    unpadder = padding.PKCS7(_BLOCK_BITS).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")
