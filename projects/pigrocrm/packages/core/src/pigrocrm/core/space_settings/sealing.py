"""The two secrets of a space's own Google client, sealed at rest (B-02).

`google_client_secret` and `google_token_key` used to sit in `space_settings` as plain
text, in the same database as the refresh tokens that key opens: a backup or a replica
leak gave away ciphertext and key together. They are now AES-256-GCM sealed with a key
derived (HKDF-SHA256) from `PIGROCRM_JWT_SECRET`, which lives in the environment and
never in a database, so a database dump alone opens nothing.

Rotating `PIGROCRM_JWT_SECRET` therefore also makes these two rows unreadable: they are
then read as not set, and the space re-enters its client secret and reconnects Gmail and
Drive (its refresh tokens were sealed with the lost key anyway).

Stored as `sealed:v1:` and the URL-safe base64 of nonce plus ciphertext. Migration 0065
seals the rows written before this existed; it carries its own frozen copy of the
derivation, so a later change here cannot change what that revision wrote.
"""

import base64
import logging

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from pigrocrm.core.crypto import KEY_BYTES, NONCE_BYTES, seal, unseal
from pigrocrm.core.errors import Conflict

logger = logging.getLogger(__name__)

SEALED_PREFIX = "sealed:v1:"
_KEY_INFO = b"pigrocrm/space-settings/v1"


def _key(jwt_secret: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None, info=_KEY_INFO).derive(
        jwt_secret.encode("utf-8")
    )


def seal_value(plaintext: str, jwt_secret: str) -> str:
    ciphertext, nonce = seal(plaintext, _key(jwt_secret))
    return SEALED_PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def unseal_value(stored: str, jwt_secret: str) -> str:
    """Raises `Conflict` for a value that is not sealed or cannot be opened with this
    installation's secret."""
    if not stored.startswith(SEALED_PREFIX):
        raise Conflict("space_settings", "il valore non è cifrato")
    try:
        raw = base64.urlsafe_b64decode(stored.removeprefix(SEALED_PREFIX))
    except ValueError as exc:
        raise Conflict("space_settings", "il valore cifrato non è leggibile") from exc
    return unseal(raw[NONCE_BYTES:], raw[:NONCE_BYTES], _key(jwt_secret))


def open_secret(key: str, stored: str, jwt_secret: str) -> str | None:
    """The plain value of a secret row, or `None` when this installation cannot open it:
    the row is then read as not set, so one unreadable secret costs the space its Google
    client and not every request it serves. Names the key in the log, never the value."""
    try:
        return unseal_value(stored, jwt_secret)
    except Conflict:
        logger.warning("space_settings: %s cannot be opened with this JWT secret", key)
        return None
