"""AES-256-GCM for the model providers' API keys at rest (REB-829).

Same scheme as PigroCRM's `gmail/crypto.py`, written again here because nothing in the
hub imports `pigrocrm*`: a 12-byte nonce from `os.urandom`, no associated data,
`seal(plaintext, key) -> (ciphertext, nonce)`. The key is `REBASE_SECRETS_KEY`, 32 bytes
in base64, and lives in the server's `.env`, outside the database: that is the point,
because a dump or a backup must not carry a key that opens the providers' accounts.

Nothing here falls back to plain text. With the secrets key missing the app refuses to
save or to use a provider key, and says so.
"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from rebase_core.config import Settings
from rebase_core.errors import SecretsKeyInvalid, SecretsKeyMissing

__all__ = [
    "SecretsKeyInvalid",
    "SecretsKeyMissing",
    "last_four",
    "seal",
    "secrets_key_bytes",
    "unseal",
]

# 96 bits, the size AES-GCM is specified for.
NONCE_BYTES = 12
KEY_BYTES = 32


def secrets_key_bytes(settings: Settings) -> bytes:
    """The 32 raw bytes behind `REBASE_SECRETS_KEY`, or `SecretsKeyMissing` when it is
    empty or is not base64 of exactly 32 bytes. The message never echoes the value."""
    raw = settings.secrets_key.strip()
    if not raw:
        raise SecretsKeyMissing()
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise SecretsKeyMissing() from None
    if len(key) != KEY_BYTES:
        raise SecretsKeyMissing()
    return key


def seal(plaintext: str, key: bytes) -> tuple[bytes, bytes]:
    """Returns `(ciphertext, nonce)`. A fresh nonce per call."""
    nonce = os.urandom(NONCE_BYTES)
    return AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None), nonce


def unseal(ciphertext: bytes, nonce: bytes, key: bytes) -> str:
    """Raises `SecretsKeyInvalid`, whose message names `REBASE_SECRETS_KEY` and nothing
    else. A wrong key and an altered row are the same instruction to the operator, and
    telling them apart would be an oracle; a nonce or key of the wrong length fails as a
    `ValueError` and means the same. `from None` keeps the cipher's own text, which can
    carry lengths, out of the chain."""
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None).decode("utf-8")
    except (InvalidTag, ValueError):
        raise SecretsKeyInvalid() from None


def last_four(plaintext: str) -> str:
    """The only part of a key that is ever shown again."""
    return plaintext[-4:]
