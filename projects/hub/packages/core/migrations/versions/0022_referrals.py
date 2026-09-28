"""referrals, referral_rewards, referral_settings; users.referral_code

Revision ID: 0022
Revises: 0021

P-REB-44: an existing member's link to who they brought in, the reward that matures
once at the referred entity's first signed letter, and the two rates an admin edits
from the hub admin area rather than from an environment variable (design record
2026-09-26). `referral_settings` starts with one seeded row (10%/30%,
`ReferralService.get_settings` never inserts a second), so a fresh environment reads a
rate from the first request rather than from a service-layer default that could drift
from what the database actually holds. Conditional like every migration of this
package: a retried deploy passes over what the previous attempt already added, never
run outside a throwaway test database.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = "TIMESTAMP WITH TIME ZONE"

_UPGRADE_STATEMENTS = (
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code VARCHAR(10)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_referral_code ON users (referral_code)",
    "CREATE TABLE IF NOT EXISTS referrals ("
    "id UUID PRIMARY KEY, "
    "referrer_user_id UUID NOT NULL REFERENCES users (id), "
    "kind VARCHAR(10) NOT NULL, "
    "entity_id UUID NOT NULL, "
    "code VARCHAR(10) NOT NULL, "
    f"created_at {_TS} NOT NULL DEFAULT now(), "
    f"updated_at {_TS} NOT NULL DEFAULT now(), "
    "CONSTRAINT ck_referrals_kind CHECK (kind IN ('freelancer', 'company')))",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_referrals_kind_entity ON referrals (kind, entity_id)",
    "CREATE INDEX IF NOT EXISTS ix_referrals_referrer ON referrals (referrer_user_id, created_at)",
    "CREATE TABLE IF NOT EXISTS referral_rewards ("
    "id UUID PRIMARY KEY, "
    "referral_id UUID NOT NULL REFERENCES referrals (id), "
    "document_id UUID NOT NULL REFERENCES contract_documents (id), "
    "rate NUMERIC(5, 4) NOT NULL, "
    "base_amount NUMERIC(10, 2), "
    "reward_amount NUMERIC(10, 2), "
    "stato VARCHAR(20) NOT NULL DEFAULT 'da_confermare', "
    "confirmed_by UUID REFERENCES users (id), "
    f"confirmed_at {_TS}, "
    f"paid_at {_TS}, "
    "note TEXT, "
    f"created_at {_TS} NOT NULL DEFAULT now(), "
    f"updated_at {_TS} NOT NULL DEFAULT now(), "
    "CONSTRAINT ck_referral_rewards_stato CHECK "
    "(stato IN ('da_confermare', 'confermato', 'pagato')), "
    "CONSTRAINT ck_referral_rewards_amount_together CHECK "
    "((base_amount IS NULL) = (reward_amount IS NULL)))",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_referral_rewards_referral_id "
    "ON referral_rewards (referral_id)",
    "CREATE TABLE IF NOT EXISTS referral_settings ("
    "id UUID PRIMARY KEY, "
    "rate_freelancer NUMERIC(5, 4) NOT NULL DEFAULT 0.10, "
    "rate_company NUMERIC(5, 4) NOT NULL DEFAULT 0.30, "
    "updated_by UUID REFERENCES users (id), "
    f"created_at {_TS} NOT NULL DEFAULT now(), "
    f"updated_at {_TS} NOT NULL DEFAULT now())",
    "INSERT INTO referral_settings (id, rate_freelancer, rate_company) "
    "SELECT gen_random_uuid(), 0.10, 0.30 "
    "WHERE NOT EXISTS (SELECT 1 FROM referral_settings)",
)


def upgrade() -> None:
    for statement in _UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in (
        "DROP TABLE IF EXISTS referral_settings",
        "DROP TABLE IF EXISTS referral_rewards",
        "DROP TABLE IF EXISTS referrals",
        "DROP INDEX IF EXISTS uq_users_referral_code",
        "ALTER TABLE users DROP COLUMN IF EXISTS referral_code",
    ):
        op.execute(statement)
