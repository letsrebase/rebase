"""attivita: the trigram indexes the global search reads

Revision ID: 0052
Revises: 0051

Two partial GIN trigram indexes, on `attivita.titolo` and `attivita.note`, for REB-723:
the global search gets a sixth branch that matches both columns with `ILIKE '%...%'`,
which no B-tree serves, and slice 7's criterion 9 asks for it «con il suo indice
trigram, misurato». The shape is the one of the ten before them (0021, 0024): `gin_trgm_ops`,
partial on `deleted_at IS NULL` because every search carries that condition, and no
`lower()` around the column, which would stop the `ILIKE` from using it.

No table changes. `IF NOT EXISTS` on both, so a host that built them by hand out of band
finds them present, as 0021 says. `pg_trgm` already exists since 0021. `CONCURRENTLY` is
not used for the reason 0021 gives: Alembic runs the migration in a transaction.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0052"
down_revision: str | Sequence[str] | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRGM_INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_attivita_titolo_trgm", "titolo"),
    ("ix_attivita_note_trgm", "note"),
)


def upgrade() -> None:
    for name, column in _TRGM_INDEXES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {name} ON attivita "
            f"USING gin ({column} gin_trgm_ops) WHERE deleted_at IS NULL"
        )


def downgrade() -> None:
    for name, _column in reversed(_TRGM_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")
