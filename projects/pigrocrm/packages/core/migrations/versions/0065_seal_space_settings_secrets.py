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

There is no downgrade of the data: unsealing would put the secrets back in plain text, so
`downgrade` is a no-op and the older code, which reads them as they are, will find the
sealed text and treat the Google client as not configured until it is entered again.
"""

import base64
import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from pigrocrm.core.config import get_settings

revision: str = "0065"
down_revision: str | Sequence[str] | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREFIX = "sealed:v1:"
_INFO = b"pigrocrm/space-settings/v1"
_KEYS = ("google_client_secret", "google_token_key")


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT key, value FROM space_settings WHERE key = ANY(:keys)"),
        {"keys": list(_KEYS)},
    ).all()
    plain = [(key, value) for key, value in rows if not value.startswith(_PREFIX)]
    if not plain:
        return
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(
        get_settings().jwt_secret.encode("utf-8")
    )
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
    pass
