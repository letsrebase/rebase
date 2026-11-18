"""campaigns.action: «ha inviato un profilo di lavoro» as a seventh measured action

Revision ID: 0057
Revises: 0056

REB-1131. A campaign that invites freelancers to send a work profile measures
`work_profile_sent`: the recipient's freelancer has a `work_profiles` row whose `submitted_at`
follows the mail. Only the CHECK constraint on `campaigns.action` changes. Conditional: the
constraint is dropped with `IF EXISTS` before it is added again. The downgrade turns any such
campaign back into `signed_in`, the action every other one falls back to, so the narrower
constraint can be restored.
"""

from collections.abc import Sequence

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
    op.execute("UPDATE campaigns SET action = 'signed_in' WHERE action = 'work_profile_sent'")
    _replace(f"{_ACTIONS}, 'click'")
