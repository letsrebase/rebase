"""mailboxes: the connection, the sealed password and the health of an IMAP mailbox

Revision ID: 0097
Revises: 0096

REB-1015, § 2.2 of `2026-11-09-incoming-documents-inbox-design.md`.

An IMAP or PEC mailbox is a `mailboxes` row with `provider = 'imap'` and, in place of a Google
account, its address, host, port, security (`tls` or `starttls`, never plaintext), user name, the
password sealed with `PIGROCRM_MAILBOX_TOKEN_KEY` (ciphertext and nonce), the last error a person
reads, when the current run of transient failures began, and when it was disconnected. The checks
say an IMAP mailbox holds all of it and a Gmail one none of it, so the two shapes cannot blur.
Nothing is backfilled: 0087 created no IMAP mailbox.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0097"
down_revision: str | Sequence[str] | None = "0096"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMAP_COLUMNS = (
    "address",
    "imap_host",
    "imap_port",
    "imap_security",
    "imap_username",
    "password_ciphertext",
    "password_nonce",
)
_CHECKS = (
    "ck_mailboxes_imap_holds_its_connection",
    "ck_mailboxes_gmail_holds_no_imap_connection",
    "ck_mailboxes_imap_security",
    "ck_mailboxes_imap_port",
)


def upgrade() -> None:
    op.add_column("mailboxes", sa.Column("address", sa.String(length=320), nullable=True))
    op.add_column("mailboxes", sa.Column("imap_host", sa.String(length=255), nullable=True))
    op.add_column("mailboxes", sa.Column("imap_port", sa.Integer(), nullable=True))
    op.add_column("mailboxes", sa.Column("imap_security", sa.String(length=10), nullable=True))
    op.add_column("mailboxes", sa.Column("imap_username", sa.String(length=320), nullable=True))
    op.add_column("mailboxes", sa.Column("password_ciphertext", sa.LargeBinary(), nullable=True))
    op.add_column("mailboxes", sa.Column("password_nonce", sa.LargeBinary(), nullable=True))
    op.add_column("mailboxes", sa.Column("last_error", sa.String(length=500), nullable=True))
    op.add_column(
        "mailboxes", sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "mailboxes", sa.Column("failing_since", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "mailboxes", sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_mailboxes_imap_holds_its_connection",
        "mailboxes",
        "provider <> 'imap' OR (address IS NOT NULL AND imap_host IS NOT NULL "
        "AND imap_port IS NOT NULL AND imap_security IS NOT NULL AND imap_username IS NOT NULL "
        "AND (status = 'disconnected' OR (password_ciphertext IS NOT NULL "
        "AND password_nonce IS NOT NULL)))",
    )
    op.create_index(
        "uq_mailboxes_imap_address",
        "mailboxes",
        [
            sa.text("coalesce(owner_user_id, '00000000-0000-0000-0000-000000000000')"),
            sa.text("lower(address)"),
        ],
        unique=True,
        postgresql_where=sa.text("provider = 'imap'"),
    )
    op.create_check_constraint(
        "ck_mailboxes_gmail_holds_no_imap_connection",
        "mailboxes",
        "provider <> 'gmail' OR (address IS NULL AND imap_host IS NULL AND imap_port IS NULL "
        "AND imap_security IS NULL AND imap_username IS NULL AND password_ciphertext IS NULL "
        "AND password_nonce IS NULL)",
    )
    op.create_check_constraint(
        "ck_mailboxes_imap_security",
        "mailboxes",
        "imap_security IS NULL OR imap_security IN ('tls', 'starttls')",
    )
    op.create_check_constraint(
        "ck_mailboxes_imap_port",
        "mailboxes",
        "imap_port IS NULL OR (imap_port BETWEEN 1 AND 65535)",
    )


def downgrade() -> None:
    # An IMAP mailbox has nothing to be without its connection: the rows go, and a space that
    # downgrades loses them, as it would any table the older schema never had.
    op.execute("DELETE FROM mailboxes WHERE provider = 'imap'")
    op.drop_index("uq_mailboxes_imap_address", table_name="mailboxes")
    for check in _CHECKS:
        op.drop_constraint(check, "mailboxes", type_="check")
    for column in ("disconnected_at", "failing_since", "last_error_at", "last_error"):
        op.drop_column("mailboxes", column)
    for column in reversed(_IMAP_COLUMNS):
        op.drop_column("mailboxes", column)
