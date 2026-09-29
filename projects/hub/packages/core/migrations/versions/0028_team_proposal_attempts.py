"""team_proposals.persone and errore: every «Proponi il team» is a row

Revision ID: 0028
Revises: 0027

Ivan, 2026-09-29: «è importante salvare tutte le richieste dopo il click proponi team
anche se non ancora finalizzate/inviate così raccogliamo metriche di uso». A proposal
that Claude answered was already a row; the number of people asked for reached only the
PostHog event, and an ask that ended in «Claude non disponibile» or «Troppe richieste»
left nothing at all. Two nullable columns: `persone`, the headcount the visitor picked
(REB-591), and `errore`, the domain code of the refusal (`llm_unavailable`,
`team_builder_busy`) on a row that is an attempt and not a proposal, `NULL` on every
proposal that answered. Both `ADD COLUMN IF NOT EXISTS`, and each check dropped `IF
EXISTS` before it is added, so a retried deploy adds the same two again. Every row
already there is a proposal that answered, with no headcount kept, so both stay `NULL`
on them and the checks hold. `team_proposals` is not read by PostHog's warehouse
(`test_warehouse_contract.py`).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECKS = (
    ("ck_team_proposals_persone", "persone IS NULL OR (persone >= 1 AND persone <= 10)"),
    ("ck_team_proposals_errore", "errore IN ('llm_unavailable', 'team_builder_busy')"),
)


def upgrade() -> None:
    op.execute("ALTER TABLE team_proposals ADD COLUMN IF NOT EXISTS persone INTEGER")
    op.execute("ALTER TABLE team_proposals ADD COLUMN IF NOT EXISTS errore VARCHAR(30)")
    for name, condition in _CHECKS:
        op.execute(f"ALTER TABLE team_proposals DROP CONSTRAINT IF EXISTS {name}")
        op.execute(f"ALTER TABLE team_proposals ADD CONSTRAINT {name} CHECK ({condition})")


def downgrade() -> None:
    """Back to 0027's shape. The attempts that failed go with the column: they were
    never proposals, and nothing but the admin's list read them."""
    op.execute("DELETE FROM team_proposals WHERE errore IS NOT NULL")
    for name, _ in _CHECKS:
        op.execute(f"ALTER TABLE team_proposals DROP CONSTRAINT IF EXISTS {name}")
    op.execute("ALTER TABLE team_proposals DROP COLUMN IF EXISTS errore")
    op.execute("ALTER TABLE team_proposals DROP COLUMN IF EXISTS persone")
