"""card_eval_profiles, card_eval_runs, card_eval_results: the evaluation set of the cards

Revision ID: 0037
Revises: 0036

REB-835 (design 2026-10-27, «Evaluation before a change goes live»): about fifteen real
profiles, each with the card an admin wrote by hand as the expected answer, and the runs
that measure a provider, a model or a prompt against them.

* `card_eval_profiles`: one row per person of the set, keyed by the freelancer
  (`ON DELETE CASCADE`: the expected card is personal data and goes with the person).
  `status` is `bozza` until an admin accepts the corrected card; an accepted profile has
  a card.
* `card_eval_runs`: one row per `rebase cards-eval`, with the task, provider, model,
  effort, the prompt version and the totals.
* `card_eval_results`: what a run made of one person, the card it wrote included, so it
  goes with the run and with the person (both `ON DELETE CASCADE`).

`IF NOT EXISTS` throughout, so a retried deploy lands on the same schema. None of the
three tables is synced to PostHog's warehouse: they hold personal data.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0037"
down_revision: str | Sequence[str] | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS card_eval_profiles (
            freelancer_id UUID PRIMARY KEY REFERENCES freelancers (id) ON DELETE CASCADE,
            label VARCHAR(40) NOT NULL,
            expected_card JSONB,
            status VARCHAR(10) NOT NULL DEFAULT 'bozza',
            corrected_by UUID REFERENCES users (id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_card_eval_profiles_status CHECK (status IN ('bozza', 'accettata')),
            CONSTRAINT ck_card_eval_profiles_accepted_has_card
                CHECK (status = 'bozza' OR expected_card IS NOT NULL)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS card_eval_runs (
            id UUID PRIMARY KEY,
            task VARCHAR(20) NOT NULL,
            provider VARCHAR(20) NOT NULL,
            model VARCHAR(60) NOT NULL,
            effort VARCHAR(10),
            prompt_version INTEGER NOT NULL,
            summary JSONB NOT NULL,
            created_by UUID REFERENCES users (id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_card_eval_runs_task CHECK (task IN ('card', 'team_proposal'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_card_eval_runs_task_created "
        "ON card_eval_runs (task, created_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS card_eval_results (
            run_id UUID NOT NULL REFERENCES card_eval_runs (id) ON DELETE CASCADE,
            freelancer_id UUID NOT NULL REFERENCES freelancers (id) ON DELETE CASCADE,
            label VARCHAR(40) NOT NULL,
            anni_ok BOOLEAN,
            seniority_ok BOOLEAN,
            guard_clean BOOLEAN,
            failure VARCHAR(20),
            latency_ms INTEGER,
            cost_eur NUMERIC(12, 6),
            card JSONB,
            ratings JSONB,
            PRIMARY KEY (run_id, freelancer_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS card_eval_results")
    op.execute("DROP TABLE IF EXISTS card_eval_runs")
    op.execute("DROP TABLE IF EXISTS card_eval_profiles")
