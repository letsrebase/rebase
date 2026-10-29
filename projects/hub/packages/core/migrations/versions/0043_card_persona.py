"""freelancer_cards.persona

Revision ID: 0043
Revises: 0042

REB-855: the call that writes a person's anonymous card also reads who the person is out
of the CV (name, email, phone, city, links, the companies named). It is kept beside the
card, for the admin's page and MCP tool only, and never written into `users`.

* `freelancer_cards.persona` JSONB, nullable: the `CardPersonaRead` shape plus the hash of
  the CV it was read from. `NULL` for every card written before prompt version 3, which
  reads as stale until an admin's «Rigenera» (nothing here calls the model).

Revises 0042 (the notes system, written in parallel); the two touch nothing in common.
`IF NOT EXISTS`, so a retried deploy lands on the same schema.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0043"
down_revision: str | Sequence[str] | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE freelancer_cards ADD COLUMN IF NOT EXISTS persona JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE freelancer_cards DROP COLUMN IF EXISTS persona")
