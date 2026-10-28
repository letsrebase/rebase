"""card_batches, card_batch_items and ai_usage.batch: a regeneration through the Batch API

Revision ID: 0041
Revises: 0040

REB-853: a bulk regeneration above a threshold goes through Anthropic's Message Batches
API (half price, asynchronous), and has to survive a restart of the `cards` service.

* `card_batches`: one row per batch sent, with the provider's id, the handle that finds it
  again (model, group, creation time; no secret), who started it and when it was sent,
  ended and applied. `status` runs `submitting`, `in_progress`, `ended`, `applied`, or
  `failed` when it was never sent.
* `card_batch_items`: one row per card of a batch, keyed by batch and person (`ON DELETE
  CASCADE` on both: it goes with the batch and with the person), with the hash of the CV
  and of every input the request was built from, what became of the card, and whether its
  answer's batch price is already in `ai_usage`. Hashes only, never a CV or a card.
* `ai_usage.batch`: the call went through a batch API and its cost is at the batch
  price. Existing rows are synchronous calls: `false`.

`IF NOT EXISTS` throughout, so a retried deploy lands on the same schema. Neither new
table is synced to PostHog's warehouse.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0041"
down_revision: str | Sequence[str] | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_usage ADD COLUMN IF NOT EXISTS batch BOOLEAN NOT NULL DEFAULT false")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS card_batches (
            id UUID PRIMARY KEY,
            provider VARCHAR(20) NOT NULL,
            provider_batch_id VARCHAR(80) UNIQUE,
            handle JSONB,
            status VARCHAR(12) NOT NULL DEFAULT 'submitting',
            request_count INTEGER NOT NULL,
            started_by UUID REFERENCES users (id),
            submitted_at TIMESTAMPTZ,
            ended_at TIMESTAMPTZ,
            applied_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_card_batches_status
                CHECK (status IN ('submitting', 'in_progress', 'ended', 'applied', 'failed'))
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_card_batches_status ON card_batches (status)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS card_batch_items (
            batch_id UUID NOT NULL REFERENCES card_batches (id) ON DELETE CASCADE,
            freelancer_id UUID NOT NULL REFERENCES freelancers (id) ON DELETE CASCADE,
            cv_sha256 VARCHAR(64) NOT NULL,
            input_hashes JSONB NOT NULL,
            status VARCHAR(12) NOT NULL DEFAULT 'pending',
            usage_recorded BOOLEAN NOT NULL DEFAULT false,
            PRIMARY KEY (batch_id, freelancer_id),
            CONSTRAINT ck_card_batch_items_status CHECK (
                status IN ('pending', 'written', 'failed', 'superseded', 'unchanged', 'errored')
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_card_batch_items_freelancer "
        "ON card_batch_items (freelancer_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS card_batch_items")
    op.execute("DROP TABLE IF EXISTS card_batches")
    op.execute("ALTER TABLE ai_usage DROP COLUMN IF EXISTS batch")
