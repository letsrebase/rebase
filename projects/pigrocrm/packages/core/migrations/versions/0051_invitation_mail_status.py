"""invitations: what became of the mail that carries each one

Revision ID: 0051
Revises: 0050

Three nullable columns, for REB-678. The invitation to the Rebase space was created for a
mistyped address, which the provider accepted (201) and then bounced; the Team panel kept
showing a pending invitation and nothing said the mail had never reached anybody.
`mail_id` is the provider's handle for the latest send, `mail_status` is where that mail
stands (`sent`, `delayed`, `delivered`, `bounced`, `suppressed`, `complained`, `failed`)
and `mail_sent_at` is when it was handed over.

No backfill: an invitation written before this was mailed by a background task that threw
the provider's answer away, so nothing says what became of it, and a column left `NULL`
is the honest way to say so. The downgrade drops the three columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051"
down_revision: str | Sequence[str] | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("invitations", sa.Column("mail_id", sa.String(length=64), nullable=True))
    op.add_column("invitations", sa.Column("mail_status", sa.String(length=12), nullable=True))
    op.add_column(
        "invitations", sa.Column("mail_sent_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("invitations", "mail_sent_at")
    op.drop_column("invitations", "mail_status")
    op.drop_column("invitations", "mail_id")
