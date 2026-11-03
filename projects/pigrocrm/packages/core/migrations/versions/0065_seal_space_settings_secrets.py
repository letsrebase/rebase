"""space_settings: the Google client secret and token key are sealed at rest

Revision ID: 0065
Revises: 0064

PCORE-B-02. `google_client_secret` and `google_token_key` were plain text in the same
database as the refresh tokens that key opens, so a backup or a replica leak gave away
ciphertext and key together. From this revision the application writes both sealed
(AES-256-GCM, key derived with HKDF-SHA256 from `PIGROCRM_JWT_SECRET`, stored as
`sealed:v1:` plus the URL-safe base64 of nonce and ciphertext; `space_settings/sealing.py`
says why that secret), and this revision seals the rows written before.

The derivation is copied here on purpose and frozen: a migration must give the same
result whatever the application code becomes. A row that is already sealed is left alone,
so the revision can run twice. The secret is read from the environment the way the
application does (`get_settings()`), and a database with neither row needs none.

A database with plain rows and a placeholder `PIGROCRM_JWT_SECRET` (the published default)
is refused: sealing under a key anyone can derive would only look like protection, and the
boot reports the failure and leaves the space at the previous revision. The application then
keeps reading those rows as they are (`open_secret` accepts a plain value), so such a space
works until the secret is set and the next boot migrates it.

`downgrade` unseals the rows back to plain text with the same key, so the code before this
revision, which reads the two values as they are, works again. A row it cannot open (another
`PIGROCRM_JWT_SECRET` than the one that sealed it) stops the downgrade instead of leaving
text that older code would read as a secret.
"""

import base64
import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from pigrocrm.core.config import KNOWN_PLACEHOLDER_JWT_SECRETS, get_settings

revision: str = "0065"
down_revision: str | Sequence[str] | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREFIX = "sealed:v1:"
_INFO = b"pigrocrm/space-settings/v1"
_KEYS = ("google_client_secret", "google_token_key")


def _key() -> bytes:
    secret = get_settings().jwt_secret
    if secret in KNOWN_PLACEHOLDER_JWT_SECRETS:
        raise RuntimeError(
            "PIGROCRM_JWT_SECRET is a placeholder: set a real secret before the space's "
            "Google secrets are sealed with it"
        )
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(
        secret.encode("utf-8")
    )


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT key, value FROM space_settings WHERE key = ANY(:keys)"),
        {"keys": list(_KEYS)},
    ).all()
    plain = [(key, value) for key, value in rows if not value.startswith(_PREFIX)]
    if not plain:
        return
    key = _key()
    for name, value in plain:
        nonce = os.urandom(12)
        sealed = _PREFIX + base64.urlsafe_b64encode(
            nonce + AESGCM(key).encrypt(nonce, value.encode("utf-8"), None)
        ).decode("ascii")
        connection.execute(
            sa.text("UPDATE space_settings SET value = :value WHERE key = :key"),
            {"value": sealed, "key": name},
        )


def downgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT key, value FROM space_settings WHERE key = ANY(:keys)"),
        {"keys": list(_KEYS)},
    ).all()
    sealed = [(key, value) for key, value in rows if value.startswith(_PREFIX)]
    if not sealed:
        return
    key = _key()
    for name, value in sealed:
        raw = base64.urlsafe_b64decode(value.removeprefix(_PREFIX))
        try:
            plain = AESGCM(key).decrypt(raw[:12], raw[12:], None).decode("utf-8")
        except InvalidTag as exc:
            raise RuntimeError(
                f"{name} was sealed with another PIGROCRM_JWT_SECRET: it cannot be unsealed"
            ) from exc
        connection.execute(
            sa.text("UPDATE space_settings SET value = :value WHERE key = :key"),
            {"value": plain, "key": name},
        )
