"""matches: the expected days, the letter's numbers and the Pigro link

Revision ID: 0021
Revises: 0020

REB-497, milestone B1. `giorni_previsti` is an admin's estimate of the engagement's
billable days, read on «Crea match» and nowhere validated against anything but its own
`CHECK (1 AND 366)`. `lettera_data_inizio`, `lettera_data_fine` and `lettera_compenso`
are `create`'s own copy of what the letter was told, a stable place for the report to
read them from even if the letter itself is later regenerated.

`pigro_stato` is `NULL` until a match turns `attivo`: a later task sets it to
`da_collegare` the moment that happens, `collegato` once `pigro_slug`, `pigro_deal_id`
and `pigro_url` point at the freelancer's deal, `errore` with the sentence in
`pigro_errore` and the moment in `pigro_attempted_at` when a retryable attempt failed,
or `rifiutato` when the CRM itself refused the link outright (a 4xx, never retried).
`pigro_linked_at` is set only the once, when `collegato` is first reached;
`pigro_attempted_at` on every attempt, successful or not. `pigro_mail_sent_at` is the
freelancer's own notification mail, sent once on the first `collegato`.

The backfill (§ 3.1 of the design) reaches every match already `attivo` when this ships:
without it, an engagement signed before this migration would stay `pigro_stato = NULL`
forever, since nothing re-checks a match once it is no longer freshly activated. It
runs after the columns exist and before the `CHECK` on `pigro_stato`, which a bare
`da_collegare` already satisfies.

Conditional like every migration of this package: a retried deploy passes over what the
previous attempt already added, never run outside a throwaway test database. The two
check constraints are added through a `pg_constraint` guard, since `ADD CONSTRAINT` has
no `IF NOT EXISTS`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPGRADE_STATEMENTS = (
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS giorni_previsti INTEGER",
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS lettera_data_inizio DATE",
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS lettera_data_fine DATE",
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS lettera_compenso NUMERIC(7, 2)",
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS pigro_stato VARCHAR(20)",
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS pigro_slug VARCHAR(32)",
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS pigro_deal_id UUID",
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS pigro_url TEXT",
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS pigro_linked_at TIMESTAMPTZ",
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS pigro_attempted_at TIMESTAMPTZ",
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS pigro_errore TEXT",
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS pigro_mail_sent_at TIMESTAMPTZ",
    "UPDATE matches SET pigro_stato = 'da_collegare' WHERE stato = 'attivo' "
    "AND pigro_stato IS NULL",
    "DO $$ BEGIN "
    "IF NOT EXISTS (SELECT 1 FROM pg_constraint "
    "WHERE conname = 'ck_matches_giorni_previsti') THEN "
    "ALTER TABLE matches ADD CONSTRAINT ck_matches_giorni_previsti "
    "CHECK (giorni_previsti IS NULL OR giorni_previsti BETWEEN 1 AND 366); "
    "END IF; END $$",
    "DO $$ BEGIN "
    "IF NOT EXISTS (SELECT 1 FROM pg_constraint "
    "WHERE conname = 'ck_matches_pigro_stato') THEN "
    "ALTER TABLE matches ADD CONSTRAINT ck_matches_pigro_stato "
    "CHECK (pigro_stato IS NULL OR pigro_stato IN "
    "('da_collegare', 'collegato', 'errore', 'rifiutato')); "
    "END IF; END $$",
)


def upgrade() -> None:
    for statement in _UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in (
        "ALTER TABLE matches DROP CONSTRAINT IF EXISTS ck_matches_pigro_stato",
        "ALTER TABLE matches DROP CONSTRAINT IF EXISTS ck_matches_giorni_previsti",
        "ALTER TABLE matches DROP COLUMN IF EXISTS pigro_mail_sent_at",
        "ALTER TABLE matches DROP COLUMN IF EXISTS pigro_errore",
        "ALTER TABLE matches DROP COLUMN IF EXISTS pigro_attempted_at",
        "ALTER TABLE matches DROP COLUMN IF EXISTS pigro_linked_at",
        "ALTER TABLE matches DROP COLUMN IF EXISTS pigro_url",
        "ALTER TABLE matches DROP COLUMN IF EXISTS pigro_deal_id",
        "ALTER TABLE matches DROP COLUMN IF EXISTS pigro_slug",
        "ALTER TABLE matches DROP COLUMN IF EXISTS pigro_stato",
        "ALTER TABLE matches DROP COLUMN IF EXISTS lettera_compenso",
        "ALTER TABLE matches DROP COLUMN IF EXISTS lettera_data_fine",
        "ALTER TABLE matches DROP COLUMN IF EXISTS lettera_data_inizio",
        "ALTER TABLE matches DROP COLUMN IF EXISTS giorni_previsti",
    ):
        op.execute(statement)
