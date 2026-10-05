"""referrals.stato, verified_at, verified_via: a referral counts once the referred person
is verified

Revision ID: 0030
Revises: 0029

REB-658 (design record 2026-10-05): the public routes do not verify the email a referral
is posted with, so a referral is `da_verificare` when it is made and `verificato` only
once the referred person proves they hold the address (a magic-link login after the
referral was recorded, or a referred freelancer's own signature of a letter).

Three columns on `referrals`: `stato` (`NOT NULL DEFAULT 'da_verificare'`, so a row
inserted by anything that predates this revision's code is pending, never silently
counted), `verified_at` and `verified_via`, and a check that ties the three together.
The check names `verified_via IS NOT NULL` outright: an `IN` test on a NULL is NULL, which a
CHECK lets through.

Existing rows are not all pending: a referral that already counts must not stop
counting. A row becomes `verificato` when it already has a reward (`storico`: it counted
and may be confirmed or paid), or its referred freelancer already signed a letter
(`lettera`, at that signature), or the referred person logged in at or after the moment
the referral was made (`accesso`, at that login). A login from before the referral
proves nothing about it and does not count, the same rule the code applies going
forward. `verified_at` is the earliest of the evidence that holds. Every other row stays
`da_verificare`. The statements add columns and constraints `IF NOT EXISTS` and promote
only rows still pending, so a retried deploy lands on the same rows.

The downgrade drops the three columns: every referral counts again, as it did before.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "ck_referrals_verification"

_BACKFILL = """
WITH evidence AS (
    SELECT
        r.id,
        (
            SELECT MIN(l.logged_at)
            FROM logins AS l
            WHERE l.user_id = COALESCE(f.user_id, c.user_id) AND l.logged_at >= r.created_at
        ) AS login_at,
        (
            SELECT MIN(d.signed_at)
            FROM contract_documents AS d
            JOIN matches AS m ON m.id = d.match_id
            WHERE r.kind = 'freelancer'
              AND m.freelancer_id = r.entity_id
              AND d.kind = 'lettera'
              AND d.stato = 'firmato'
              AND d.signed_at IS NOT NULL
        ) AS letter_at,
        (SELECT MIN(w.created_at) FROM referral_rewards AS w WHERE w.referral_id = r.id)
            AS reward_at
    FROM referrals AS r
    LEFT JOIN freelancers AS f ON r.kind = 'freelancer' AND f.id = r.entity_id
    LEFT JOIN companies AS c ON r.kind = 'company' AND c.id = r.entity_id
    WHERE r.stato = 'da_verificare'
)
UPDATE referrals AS r
SET stato = 'verificato',
    verified_at = LEAST(e.login_at, e.letter_at, e.reward_at),
    verified_via = CASE LEAST(e.login_at, e.letter_at, e.reward_at)
        WHEN e.login_at THEN 'accesso'
        WHEN e.letter_at THEN 'lettera'
        ELSE 'storico'
    END
FROM evidence AS e
WHERE e.id = r.id
  AND (e.login_at IS NOT NULL OR e.letter_at IS NOT NULL OR e.reward_at IS NOT NULL)
"""


def upgrade() -> None:
    op.execute(
        "ALTER TABLE referrals ADD COLUMN IF NOT EXISTS stato VARCHAR(14) "
        "NOT NULL DEFAULT 'da_verificare'"
    )
    op.execute(
        "ALTER TABLE referrals ADD COLUMN IF NOT EXISTS verified_at TIMESTAMP WITH TIME ZONE"
    )
    op.execute("ALTER TABLE referrals ADD COLUMN IF NOT EXISTS verified_via VARCHAR(10)")
    op.execute(_BACKFILL)
    op.execute(f"ALTER TABLE referrals DROP CONSTRAINT IF EXISTS {_CHECK}")
    op.execute(
        f"ALTER TABLE referrals ADD CONSTRAINT {_CHECK} CHECK ("
        "(stato = 'da_verificare' AND verified_at IS NULL AND verified_via IS NULL) "
        "OR (stato = 'verificato' AND verified_at IS NOT NULL "
        "AND verified_via IS NOT NULL "
        "AND verified_via IN ('accesso', 'lettera', 'storico')))"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE referrals DROP CONSTRAINT IF EXISTS {_CHECK}")
    for column in ("verified_via", "verified_at", "stato"):
        op.execute(f"ALTER TABLE referrals DROP COLUMN IF EXISTS {column}")
