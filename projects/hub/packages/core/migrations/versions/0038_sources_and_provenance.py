"""freelancer_sources, ai_sources and the card's provenance columns

Revision ID: 0038
Revises: 0037

REB-837 (design 2026-10-27, `docs/superpowers/specs/`): the card may be written from
GitHub and from the links a person gave, each source kept as text with the time it was
read and a hash, so a card can say what it was written from and a changed source marks it
stale.

* `freelancer_sources`: one row per person and source: `kind` `github` (`ref` the login) or
  `link` (`ref` the URL), the text read, its hash without what moves on its own, the
  outcome (`status`) and when. Gone with the person (`ON DELETE CASCADE`). LinkedIn is
  never a row.
* `ai_sources`: the switch of each kind (`github`, `links`), seeded off. Turning one on is
  an admin's decision on «Modelli AI», after the privacy notice covers it.
* `freelancer_cards` gains the provenance and the version it was written under:
  `prompt_version`, `provider`, `fonti`, `source_hashes`, `conflicts`, `thinking_tokens`,
  all nullable: a card written before reads as `NULL` in every one.

Revises 0037 (the evaluation set's tables, Phase 2 of the same plan); the two touch
nothing in common. `IF NOT EXISTS` throughout and `ON CONFLICT DO NOTHING` for the seed,
so a retried deploy lands on the same schema. Neither new table is synced to PostHog's
warehouse.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0038"
down_revision: str | Sequence[str] | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_sources (
            kind VARCHAR(10) PRIMARY KEY,
            enabled BOOLEAN NOT NULL DEFAULT false,
            updated_by UUID REFERENCES users (id),
            updated_at TIMESTAMPTZ,
            CONSTRAINT ck_ai_sources_kind CHECK (kind IN ('github', 'links'))
        )
        """
    )
    op.execute(
        """
        INSERT INTO ai_sources (kind, enabled)
        VALUES ('github', false), ('links', false)
        ON CONFLICT (kind) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS freelancer_sources (
            id UUID PRIMARY KEY,
            freelancer_id UUID NOT NULL REFERENCES freelancers (id) ON DELETE CASCADE,
            kind VARCHAR(10) NOT NULL,
            ref TEXT NOT NULL,
            position INTEGER NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            content_sha256 VARCHAR(64) NOT NULL DEFAULT '',
            status VARCHAR(12) NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT uq_freelancer_sources_ref UNIQUE (freelancer_id, kind, ref),
            CONSTRAINT ck_freelancer_sources_kind CHECK (kind IN ('github', 'link')),
            CONSTRAINT ck_freelancer_sources_status
                CHECK (status IN ('ok', 'unreadable', 'blocked', 'robots', 'too_large', 'error'))
        )
        """
    )
    op.execute("ALTER TABLE freelancer_cards ADD COLUMN IF NOT EXISTS prompt_version INTEGER")
    op.execute("ALTER TABLE freelancer_cards ADD COLUMN IF NOT EXISTS provider VARCHAR(20)")
    op.execute("ALTER TABLE freelancer_cards ADD COLUMN IF NOT EXISTS fonti JSONB")
    op.execute("ALTER TABLE freelancer_cards ADD COLUMN IF NOT EXISTS source_hashes JSONB")
    op.execute("ALTER TABLE freelancer_cards ADD COLUMN IF NOT EXISTS conflicts JSONB")
    op.execute("ALTER TABLE freelancer_cards ADD COLUMN IF NOT EXISTS thinking_tokens INTEGER")


def downgrade() -> None:
    for column in (
        "thinking_tokens",
        "conflicts",
        "source_hashes",
        "fonti",
        "provider",
        "prompt_version",
    ):
        op.execute(f"ALTER TABLE freelancer_cards DROP COLUMN IF EXISTS {column}")
    op.execute("DROP TABLE IF EXISTS freelancer_sources")
    op.execute("DROP TABLE IF EXISTS ai_sources")
