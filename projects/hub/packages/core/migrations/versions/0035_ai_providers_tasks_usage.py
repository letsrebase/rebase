"""ai_providers, ai_tasks, ai_prices, ai_usage: the model providers, configured in the hub

Revision ID: 0035
Revises: 0034

REB-830 (design 2026-10-27, `docs/superpowers/specs/`): the Anthropic key and the model
used to live in the server's `.env`; an admin now chooses, per task, which provider and
model answer, in «Modelli AI».

* `ai_providers`: one row per provider. The key is write-only and encrypted at rest
  (`rebase_core.secrets`, AES-256-GCM): the only places it can sit are `key_ciphertext`
  and `key_nonce`, both bytea, and `key_last4` is what the page may show again. No
  text column holds a key.
* `ai_tasks`: one row per task (`card`, `team_proposal`): provider, model, effort, max
  output tokens, an optional daily cap in euros.
* `ai_prices`: what a model costs, in euro per million tokens, entered by an admin. No
  exchange rate in code.
* `ai_usage`: one row per call, whatever its outcome: `task` is the configured task whose
  cap the call consumes, `purpose` says why it ran (`run`, `prova`, `eval`).

The three providers and the two tasks are seeded here, empty: no key, every provider off,
both tasks on Anthropic's `claude-opus-5` with `medium` effort and the output ceilings
the callers had in code (2000 for the card, 8000 for the team proposal). Turning
Anthropic on with the old key is a command (`rebase ai-seed`), never a migration.

`IF NOT EXISTS` throughout and `ON CONFLICT DO NOTHING` for the seed, so a retried deploy
lands on the same schema. None of the four tables is synced to PostHog's warehouse.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0035"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_providers (
            provider VARCHAR(20) PRIMARY KEY,
            key_ciphertext BYTEA,
            key_nonce BYTEA,
            key_last4 VARCHAR(4),
            saved_by UUID REFERENCES users (id),
            saved_at TIMESTAMPTZ,
            enabled BOOLEAN NOT NULL DEFAULT false,
            CONSTRAINT ck_ai_providers_provider
                CHECK (provider IN ('anthropic', 'openai', 'google')),
            CONSTRAINT ck_ai_providers_key_pair
                CHECK ((key_ciphertext IS NULL) = (key_nonce IS NULL))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_tasks (
            task VARCHAR(20) PRIMARY KEY,
            provider VARCHAR(20) NOT NULL REFERENCES ai_providers (provider),
            model VARCHAR(60) NOT NULL,
            effort VARCHAR(10),
            max_output_tokens INTEGER NOT NULL,
            daily_cap_eur NUMERIC(10, 2),
            enabled BOOLEAN NOT NULL DEFAULT true,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_ai_tasks_task CHECK (task IN ('card', 'team_proposal')),
            CONSTRAINT ck_ai_tasks_max_output_tokens CHECK (max_output_tokens > 0),
            CONSTRAINT ck_ai_tasks_daily_cap CHECK (daily_cap_eur IS NULL OR daily_cap_eur >= 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_prices (
            provider VARCHAR(20) NOT NULL,
            model VARCHAR(60) NOT NULL,
            input_eur_per_mtok NUMERIC(12, 4) NOT NULL,
            output_eur_per_mtok NUMERIC(12, 4) NOT NULL,
            thinking_eur_per_mtok NUMERIC(12, 4),
            cache_read_eur_per_mtok NUMERIC(12, 4),
            PRIMARY KEY (provider, model),
            CONSTRAINT ck_ai_prices_provider
                CHECK (provider IN ('anthropic', 'openai', 'google')),
            CONSTRAINT ck_ai_prices_not_negative
                CHECK (input_eur_per_mtok >= 0 AND output_eur_per_mtok >= 0
                    AND (thinking_eur_per_mtok IS NULL OR thinking_eur_per_mtok >= 0)
                    AND (cache_read_eur_per_mtok IS NULL OR cache_read_eur_per_mtok >= 0))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_usage (
            id UUID PRIMARY KEY,
            at TIMESTAMPTZ NOT NULL DEFAULT now(),
            task VARCHAR(20) NOT NULL,
            purpose VARCHAR(20) NOT NULL DEFAULT 'run',
            provider VARCHAR(20) NOT NULL,
            model VARCHAR(60) NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            thinking_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cost_eur NUMERIC(12, 6),
            priced BOOLEAN NOT NULL,
            freelancer_id UUID REFERENCES freelancers (id) ON DELETE SET NULL,
            CONSTRAINT ck_ai_usage_task CHECK (task IN ('card', 'team_proposal')),
            CONSTRAINT ck_ai_usage_purpose CHECK (purpose IN ('run', 'prova', 'eval')),
            CONSTRAINT ck_ai_usage_priced CHECK (priced = (cost_eur IS NOT NULL))
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_ai_usage_task_at ON ai_usage (task, at)")
    op.execute(
        """
        INSERT INTO ai_providers (provider, enabled)
        VALUES ('anthropic', false), ('openai', false), ('google', false)
        ON CONFLICT (provider) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO ai_tasks (task, provider, model, effort, max_output_tokens)
        VALUES ('card', 'anthropic', 'claude-opus-5', 'medium', 2000),
               ('team_proposal', 'anthropic', 'claude-opus-5', 'medium', 8000)
        ON CONFLICT (task) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_usage")
    op.execute("DROP TABLE IF EXISTS ai_prices")
    op.execute("DROP TABLE IF EXISTS ai_tasks")
    op.execute("DROP TABLE IF EXISTS ai_providers")
