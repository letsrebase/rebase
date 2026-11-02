"""closed-set checks, a stored CV hash, spend holds and token expiry

Revision ID: 0047
Revises: 0046

The hub review of 2026-11-02 (REB-889, REB-891, REB-892):

* `ai_usage.reserved_until` (HCORE-01): a call reserves its estimated cost in an `ai_usage`
  row *before* the network call, under a per-task advisory lock, so concurrent calls cannot
  all pass the daily cap on the same spent sum. The row carries the estimate in `cost_eur`
  and `reserved_until` as long as the call is in flight; settling it clears the column and
  writes the real numbers. A hold that is never settled (a crashed process) stops counting
  once `reserved_until` is past.
* `freelancers.cv_sha256` (REB-804): the digest of `cv_bytes`, generated and stored by the
  database, so the card staleness query compares two columns instead of hashing every CV in
  SQL on each pass. Existing rows are filled by `ADD COLUMN` itself.
* `admin_tokens.expires_at` (REB-803): an optional end for an agent's token; `NULL` is the
  old behaviour, no expiry.
* `CHECK`s for the closed value sets that only `schemas.py` guarded (HCORE-16):
  `freelancers.stato`, `compilata_da`, `remoto`, `companies.stato`, `remoto`, `notes.fonte`.
  Each is added `NOT VALID` and then validated; a legacy row that breaks one is reported as
  a warning and leaves the constraint enforcing every new write instead of failing the deploy.

Conditional like every migration of this package: `IF NOT EXISTS`, and each `CHECK` behind a
`pg_constraint` guard.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0047"
down_revision: str | Sequence[str] | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECKS = (
    (
        "freelancers",
        "ck_freelancers_stato",
        "stato IN ('nuovo', 'contattato', 'attivo', 'scartato')",
    ),
    ("freelancers", "ck_freelancers_compilata_da", "compilata_da IN ('persona', 'admin')"),
    (
        "freelancers",
        "ck_freelancers_remoto",
        "remoto IS NULL OR remoto IN ('remoto', 'ibrido', 'in_sede')",
    ),
    ("companies", "ck_companies_stato", "stato IN ('nuovo', 'contattato', 'in_corso', 'chiuso')"),
    ("companies", "ck_companies_remoto", "remoto IN ('remoto', 'ibrido', 'in_sede')"),
    (
        "notes",
        "ck_notes_fonte",
        "fonte IS NULL OR fonte IN ('web', 'mcp', 'persona', 'sistema')",
    ),
)


def upgrade() -> None:
    op.execute("ALTER TABLE ai_usage ADD COLUMN IF NOT EXISTS reserved_until TIMESTAMPTZ")
    op.execute("ALTER TABLE admin_tokens ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ")
    op.execute(
        "ALTER TABLE freelancers ADD COLUMN IF NOT EXISTS cv_sha256 VARCHAR(64) "
        "GENERATED ALWAYS AS (encode(sha256(cv_bytes), 'hex')) STORED"
    )
    for table, name, predicate in _CHECKS:
        op.execute(
            "DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') THEN "
            f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({predicate}) NOT VALID; "
            "BEGIN "
            f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}; "
            "EXCEPTION WHEN check_violation THEN "
            f"RAISE WARNING '{name}: rows break it; it enforces new writes only'; "
            "END; "
            "END IF; END $$"
        )


def downgrade() -> None:
    for table, name, _ in reversed(_CHECKS):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
    op.execute("ALTER TABLE freelancers DROP COLUMN IF EXISTS cv_sha256")
    op.execute("ALTER TABLE admin_tokens DROP COLUMN IF EXISTS expires_at")
    op.execute("ALTER TABLE ai_usage DROP COLUMN IF EXISTS reserved_until")
