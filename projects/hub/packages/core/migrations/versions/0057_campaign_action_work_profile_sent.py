"""campaigns.action: «ha inviato un profilo di lavoro» as a seventh measured action

Revision ID: 0057
Revises: 0056

REB-1131. A campaign that invites freelancers to send a work profile measures
`work_profile_sent`: the recipient's freelancer has a `work_profiles` row whose `submitted_at`
follows the mail. Only the CHECK constraint on `campaigns.action` changes. Conditional: the
constraint is dropped with `IF EXISTS` before it is added again. The downgrade refuses while a
campaign measures `work_profile_sent` or filters on `work_profile`: rewriting it would change what
the campaign measures and whom it reached, so that is a restore, not a rollback.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0057"
down_revision: str | Sequence[str] | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIONS = (
    "'signed_in', 'cv', 'card_complete', 'profile_created', 'request_updated', 'pigro_customer'"
)


def _replace(actions: str) -> None:
    op.execute("ALTER TABLE campaigns DROP CONSTRAINT IF EXISTS ck_campaigns_action")
    op.execute(
        f"ALTER TABLE campaigns ADD CONSTRAINT ck_campaigns_action CHECK (action IN ({actions}))"
    )


def upgrade() -> None:
    _replace(f"{_ACTIONS}, 'work_profile_sent', 'click'")


def downgrade() -> None:
    used = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM campaigns "
                "WHERE action = 'work_profile_sent' OR jsonb_exists(filters, 'work_profile')"
            )
        )
        .scalar_one()
    )
    if used:
        raise RuntimeError(
            f"0057 cannot be downgraded: {used} campaign(s) measure work_profile_sent or filter "
            "on work_profile; delete them or restore from backup"
        )
    _replace(f"{_ACTIONS}, 'click'")
