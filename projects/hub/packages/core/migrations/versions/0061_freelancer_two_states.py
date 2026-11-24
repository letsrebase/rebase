"""freelancers.status: only `new` and `discarded` remain

Revision ID: 0061
Revises: 0060

REB-1164. `contacted` and `active` were labels nobody used: the relationship with a
freelancer is tracked in the CRM and in notes. Those cards go back to `new`, and the check
constraint keeps the two states that have effects. A stored campaign filter on the Talenti
list that selects `contacted` or `active` would no longer validate: a draft's, a sent one's
or a cancelled one's is rewritten to `new` (those cards are `new` now). A scheduled or
sending campaign rebuilds its audience from the filter when it sends, so `new` would mail
every card in play: the upgrade stops and names it instead. The downgrade restores the
four-value constraint; rows and filters stay as they are.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

_STALE_TALENT_FILTER = (
    "filters ->> 'list' = 'talents' AND filters ->> 'status' IN ('contacted', 'active')"
)

revision: str = "0061"
down_revision: str | Sequence[str] | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    live = (
        op.get_bind()
        .execute(
            text(
                f"SELECT name FROM campaigns WHERE status IN ('scheduled', 'sending') "
                f"AND {_STALE_TALENT_FILTER}"
            )
        )
        .scalars()
        .all()
    )
    if live:
        raise RuntimeError(
            "Campaigns about to send to contacted or active talents, a state 0061 removes: "
            f"{', '.join(live)}. Cancel or reschedule them, then upgrade again."
        )
    op.execute("UPDATE freelancers SET status = 'new' WHERE status IN ('contacted', 'active')")
    op.execute(
        "UPDATE campaigns SET filters = jsonb_set(filters, '{status}', '\"new\"') "
        f"WHERE {_STALE_TALENT_FILTER}"
    )
    op.execute("ALTER TABLE freelancers DROP CONSTRAINT IF EXISTS ck_freelancers_status")
    op.execute(
        "ALTER TABLE freelancers ADD CONSTRAINT ck_freelancers_status "
        "CHECK (status IN ('new', 'discarded'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE freelancers DROP CONSTRAINT IF EXISTS ck_freelancers_status")
    op.execute(
        "ALTER TABLE freelancers ADD CONSTRAINT ck_freelancers_status "
        "CHECK (status IN ('new', 'contacted', 'active', 'discarded'))"
    )
