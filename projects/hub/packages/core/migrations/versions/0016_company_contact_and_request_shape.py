"""users.telefono, and the company request's shape: remoto, giorni_presenza,
numero_risorse, figura_richiesta

Revision ID: 0016
Revises: 0015

REB-380: a phone number for the referente (`users.telefono`, nullable -- only a
company's own wizard requires it, at the Pydantic layer, the same way `Freelancer`'s
signup-born columns are nullable at the database and required by the wizard's schema
alone), and four new answers on a company's own request: `remoto`/`giorni_presenza`
mirroring `Freelancer.remoto`'s own shape but `NOT NULL` (a company request has no
"born from a signup, completed later" case), `numero_risorse` (how many people the
request needs) and `figura_richiesta` (the role, free text, the same shape and width
as `Freelancer.posizione`, with the same trigram index for the admin's search).

`companies` already holds real rows (0013's own docstring: "safe on a database where
freelancers, companies, signups and users already hold real rows"), so the three
`NOT NULL` columns are added with a `DEFAULT` that backfills every existing request
in the same statement Postgres runs to add the column, then the default is dropped:
the application always supplies these three going forward (the model declares no
default of its own), and a value on an old row is a placeholder for "the wizard never
asked", not a real fact reintroduced under a false certainty. `remoto` gets `'remoto'`
(`REMOTE_OPTIONS[0]`, and the least presumptive of the three: it makes no claim about
an office nobody described), `numero_risorse` gets `1` (also this column's own `CHECK`
floor), `figura_richiesta` gets `'Non specificata'`. None of the three backfilled
values trips the new `CHECK` constraints below.

`giorni_presenza` stays nullable and untouched: `NULL` already satisfies the
together-`CHECK` for every backfilled row, since none of them is `'ibrido'`.

Every statement is conditional, the same discipline migration 0001's own docstring
states for this package (`ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`): a
deploy that is retried, or that fails partway through this one migration and is run
again, must not error on a column, constraint or index the previous attempt already
created. Postgres has no `ADD CONSTRAINT IF NOT EXISTS`, so the three `CHECK`
constraints are each guarded by a `pg_constraint` lookup inside a `DO` block instead.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PLACEHOLDER_FIGURA_RICHIESTA = "Non specificata"

_CHECK_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    (
        "ck_companies_giorni_presenza_range",
        "giorni_presenza IS NULL OR (giorni_presenza >= 1 AND giorni_presenza <= 4)",
    ),
    (
        "ck_companies_giorni_presenza_together",
        "(remoto = 'ibrido') = (giorni_presenza IS NOT NULL)",
    ),
    ("ck_companies_numero_risorse_positive", "numero_risorse >= 1"),
)


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS telefono VARCHAR(40)")

    op.execute(
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS remoto VARCHAR(10) "
        "NOT NULL DEFAULT 'remoto'"
    )
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS giorni_presenza INTEGER")
    op.execute(
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS numero_risorse INTEGER NOT NULL DEFAULT 1"
    )
    op.execute(
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS figura_richiesta VARCHAR(160) "
        f"NOT NULL DEFAULT '{_PLACEHOLDER_FIGURA_RICHIESTA}'"
    )
    # Dropping a default that is already gone (a retried run) is a no-op, not an error.
    op.execute("ALTER TABLE companies ALTER COLUMN remoto DROP DEFAULT")
    op.execute("ALTER TABLE companies ALTER COLUMN numero_risorse DROP DEFAULT")
    op.execute("ALTER TABLE companies ALTER COLUMN figura_richiesta DROP DEFAULT")

    for name, condition in _CHECK_CONSTRAINTS:
        op.execute(
            f"DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') THEN "
            f"ALTER TABLE companies ADD CONSTRAINT {name} CHECK ({condition}); "
            f"END IF; END $$;"
        )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_figura_richiesta_trgm "
        "ON companies USING gin (figura_richiesta gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_companies_figura_richiesta_trgm")
    for name, _condition in reversed(_CHECK_CONSTRAINTS):
        op.execute(f"ALTER TABLE companies DROP CONSTRAINT IF EXISTS {name}")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS figura_richiesta")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS numero_risorse")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS giorni_presenza")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS remoto")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS telefono")
