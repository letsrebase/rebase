"""The evaluation set keeps only who is in it; a result keeps what the checks found

Revision ID: 0039
Revises: 0038

REB-842: the evaluation is automatic now (`rebase_core.card_eval`): each card a run writes
is cross-checked against the sources it was written from, so nobody writes an expected
card by hand and nobody rates anything.

* `card_eval_profiles` keeps the person and the failure they cover (`label`), which is the
  fixed sample `rebase cards-eval` draws from. Its `expected_card`, `status`, `corrected_by`
  and `updated_at` go, and with them every expected card an admin or a model wrote. The ids
  stay: the fifteen people drafted under REB-835 are the sample.
* `card_eval_results` loses `ratings` and gains `checks` (what the rules found: years,
  seniority band, anonymity), `judgement` (the verdict of the judging call per field, what it
  flagged as invented and the identifiers it found), and the judging call's own latency and
  cost, beside the generation's `latency_ms` and `cost_eur`. The earlier rows keep their three
  measures and read `NULL` in the new columns.

`IF EXISTS` / `IF NOT EXISTS` throughout, so a retried deploy lands on the same schema. The
downgrade puts the columns back empty: what was dropped is not recoverable.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0039"
down_revision: str | Sequence[str] | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE card_eval_profiles "
        "DROP CONSTRAINT IF EXISTS ck_card_eval_profiles_accepted_has_card"
    )
    op.execute(
        "ALTER TABLE card_eval_profiles DROP CONSTRAINT IF EXISTS ck_card_eval_profiles_status"
    )
    op.execute(
        """
        ALTER TABLE card_eval_profiles
            DROP COLUMN IF EXISTS expected_card,
            DROP COLUMN IF EXISTS status,
            DROP COLUMN IF EXISTS corrected_by,
            DROP COLUMN IF EXISTS updated_at
        """
    )
    op.execute("ALTER TABLE card_eval_results DROP COLUMN IF EXISTS ratings")
    op.execute(
        """
        ALTER TABLE card_eval_results
            ADD COLUMN IF NOT EXISTS checks JSONB,
            ADD COLUMN IF NOT EXISTS judgement JSONB,
            ADD COLUMN IF NOT EXISTS judge_latency_ms INTEGER,
            ADD COLUMN IF NOT EXISTS judge_cost_eur NUMERIC(12, 6)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE card_eval_results
            DROP COLUMN IF EXISTS judge_cost_eur,
            DROP COLUMN IF EXISTS judge_latency_ms,
            DROP COLUMN IF EXISTS judgement,
            DROP COLUMN IF EXISTS checks
        """
    )
    op.execute("ALTER TABLE card_eval_results ADD COLUMN IF NOT EXISTS ratings JSONB")
    op.execute(
        """
        ALTER TABLE card_eval_profiles
            ADD COLUMN IF NOT EXISTS expected_card JSONB,
            ADD COLUMN IF NOT EXISTS status VARCHAR(10) NOT NULL DEFAULT 'bozza',
            ADD COLUMN IF NOT EXISTS corrected_by UUID REFERENCES users (id),
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """
    )
    op.execute(
        """
        ALTER TABLE card_eval_profiles
            ADD CONSTRAINT ck_card_eval_profiles_status CHECK (status IN ('bozza', 'accettata')),
            ADD CONSTRAINT ck_card_eval_profiles_accepted_has_card
                CHECK (status = 'bozza' OR expected_card IS NOT NULL)
        """
    )
