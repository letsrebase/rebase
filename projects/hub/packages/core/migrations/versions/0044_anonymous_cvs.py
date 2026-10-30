"""anonymous_cvs, and the `cv_anonimo` AI task

Revision ID: 0044
Revises: 0043

REB-862: an admin downloads a person's CV as an anonymous PDF, written by a call of its own
(never the card's, whose prompt and guard are not this one's).

* `ai_tasks.task` and `ai_usage.task` accept a third value, `cv_anonimo`: both CHECKs are
  dropped and added again.
* `ai_tasks` gets the `cv_anonimo` row, copied from the `card` row as it stands (provider,
  model, effort, enabled) with a 16000-token ceiling (a two-page CV is 3-5k tokens of
  JSON; the card's ceiling is far too small) and no daily cap. Without a `card` row it is
  Anthropic's `claude-opus-5` at `medium`, off, as 0036 seeds the others.
* `anonymous_cvs`: the last CV written for a freelancer, keyed by the SHA-256 of the CV
  file it came from, so the same CV is not paid for twice. It goes with the person
  (`ON DELETE CASCADE`).

`ADMIN_ACTION_KINDS` gains `anonymous_cv_downloaded` in Python only, as the column holds
no CHECK. `IF NOT EXISTS` and `ON CONFLICT DO NOTHING`, so a retried deploy lands on the
same schema.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0044"
down_revision: str | Sequence[str] | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _checks(tasks: str) -> None:
    op.execute("ALTER TABLE ai_tasks DROP CONSTRAINT IF EXISTS ck_ai_tasks_task")
    op.execute(f"ALTER TABLE ai_tasks ADD CONSTRAINT ck_ai_tasks_task CHECK (task IN ({tasks}))")
    op.execute("ALTER TABLE ai_usage DROP CONSTRAINT IF EXISTS ck_ai_usage_task")
    op.execute(f"ALTER TABLE ai_usage ADD CONSTRAINT ck_ai_usage_task CHECK (task IN ({tasks}))")


def upgrade() -> None:
    _checks("'card', 'team_proposal', 'cv_anonimo'")
    op.execute(
        """
        INSERT INTO ai_tasks (task, provider, model, effort, max_output_tokens, enabled)
        SELECT 'cv_anonimo', provider, model, effort, 16000, enabled
        FROM ai_tasks WHERE task = 'card'
        ON CONFLICT (task) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO ai_tasks (task, provider, model, effort, max_output_tokens, enabled)
        VALUES ('cv_anonimo', 'anthropic', 'claude-opus-5', 'medium', 16000, false)
        ON CONFLICT (task) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS anonymous_cvs (
            freelancer_id UUID PRIMARY KEY REFERENCES freelancers (id) ON DELETE CASCADE,
            cv_sha256 VARCHAR(64) NOT NULL,
            prompt_version INTEGER NOT NULL,
            body JSONB NOT NULL,
            provider VARCHAR(20) NOT NULL,
            model VARCHAR(60) NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            thinking_tokens INTEGER NOT NULL DEFAULT 0,
            generated_at TIMESTAMPTZ NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS anonymous_cvs")
    op.execute("DELETE FROM ai_usage WHERE task = 'cv_anonimo'")
    op.execute("DELETE FROM ai_tasks WHERE task = 'cv_anonimo'")
    _checks("'card', 'team_proposal'")
