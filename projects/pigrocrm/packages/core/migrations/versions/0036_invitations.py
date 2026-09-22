"""a space gains people by invitation: the invitations table

Revision ID: 0036
Revises: 0035

Spec 2026-09-17 §2. Modelled on `magic_link_tokens` (0034): only the SHA-256 of the
raw token is stored, and the row is never deleted, so `accepted_at`/`revoked_at` keep
the three dead states of a spent link tellable apart after the fact.

The unique index over `lower(email)` is partial on the pending predicate (`accepted_at
IS NULL AND revoked_at IS NULL`): an address frees the moment its invitation ends, and
an expired-but-untouched row stays inside the index because expiry is `expires_at <
now()`, not a column state -- the service rewrites that row in place rather than
inserting a second one. Functional and partial at once, the shape autogenerate cannot
compare; `uq_invitations_email_lower_open` is listed in the hand-maintained set of
`tests/test_migrations.py` for exactly that reason.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | Sequence[str] | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("nome", sa.String(length=200), nullable=True),
        sa.Column("ruolo", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["invited_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_invitations_email", "invitations", ["email"], unique=False)
    op.create_index(op.f("ix_invitations_token_hash"), "invitations", ["token_hash"], unique=True)
    op.create_index(op.f("ix_invitations_invited_by"), "invitations", ["invited_by"], unique=False)
    op.create_index(
        "uq_invitations_email_lower_open",
        "invitations",
        [sa.literal_column("lower(email)")],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_invitations_email_lower_open", table_name="invitations")
    op.drop_index(op.f("ix_invitations_invited_by"), table_name="invitations")
    op.drop_index(op.f("ix_invitations_token_hash"), table_name="invitations")
    op.drop_index("ix_invitations_email", table_name="invitations")
    op.drop_table("invitations")
