"""AES-256-GCM for the secrets this application stores at rest: the Google refresh
token, the Slack bot token, the per-space integration settings.

Why encrypt at all, when the database belongs to the user: not to protect them from
themselves, but because a dump, a backup, or a `pg_dump` attached to a bug report are
different exposure surfaces from the running system -- and these credentials grant
access to *third-party* accounts, not merely to this application. The key lives
outside the database. That is the entire point.

Access tokens are never stored, here or anywhere: they live in memory for the duration
of one sync or one send. An access token is valid for an hour; persisting it would add
a second secret to protect for no gain. `storage/gdrive.py` already made that call.
"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from pigrocrm.core.errors import Conflict

# AES-256 takes a 32-byte key.
KEY_BYTES = 32

# 96 bits, the size AES-GCM is specified for. A longer nonce is hashed down and a
# shorter one narrows the space needlessly.
NONCE_BYTES = 12


def decode_aes256_key(encoded: str, variable: str) -> bytes:
    """Base64 text of the environment variable `variable` -> the 32 raw key bytes.

    Raises `ValueError` naming the variable and never quoting the value. Callers run it
    at startup when the key is needed, so a missing or malformed key fails the boot
    rather than the first use. `from exc` keeps the cause for the traceback while the
    message -- the one anything might format on its own -- names only the variable
    (`binascii.Error` quotes the offending input in some builds).
    """
    if not encoded:
        raise ValueError(f"{variable} is not set, so the secrets stored with it cannot be read")
    try:
        key = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{variable} is not valid base64") from exc
    if len(key) != KEY_BYTES:
        raise ValueError(
            f"{variable} must decode to exactly {KEY_BYTES} bytes for AES-256-GCM, got {len(key)}"
        )
    return key


def seal(plaintext: str, key: bytes) -> tuple[bytes, bytes]:
    """Returns `(ciphertext, nonce)`. A fresh nonce per call, from `os.urandom`."""
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return ciphertext, nonce


def unseal(ciphertext: bytes, nonce: bytes, key: bytes) -> str:
    """Raises `Conflict` saying the secret cannot be read with the configured key, and
    nothing else: a caller that knows which variable and which integration are meant
    re-words it for them (`google/credential.py`, `slack/token.py`).

    Never the ciphertext, never the key, never a partial decryption: an authentication
    failure means either the key is wrong or the row was altered, and both are the
    same instruction to the operator. Distinguishing them in the message would leak
    which one, and an oracle on 'is this the right key' is the one thing GCM's
    authenticator exists to withhold.

    `ValueError` is caught alongside `InvalidTag` because a nonce or key of the wrong
    *length* fails there instead -- a distinction that matters to the cipher and not at
    all to the caller, for whom every branch means the same thing: this row cannot be
    read with this key. `from exc` keeps the real cause in the traceback, where an
    operator debugging their own installation can see it, without putting it in the
    message that anything might format on its own.
    """
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None).decode("utf-8")
    except (InvalidTag, ValueError) as exc:
        raise Conflict(
            "secret",
            "il segreto memorizzato non è decifrabile con la chiave configurata",
        ) from exc
