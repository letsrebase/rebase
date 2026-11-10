"""The `crm_lead` AI task

Revision ID: 0045
Revises: 0044

REB-867: a fourth task in «Modelli AI» that drafts a CRM deal from what a company wrote
(`rebase_core.crm_lead`). Spec: `docs/superpowers/specs/2026-10-30-company-requests-into-the-
rebase-crm-design.md` § 3.

* `ai_tasks.task` and `ai_usage.task` accept a fourth value, `crm_lead`: both CHECKs are
  dropped and added again.
* `ai_tasks` gets the `crm_lead` row, with the provider and model the `team_proposal` row
  has as it stands, effort `low`, a 1500-token ceiling, a daily cap of 1 euro, and **off**:
  an admin switches it on once the privacy page names what it sends to the provider. Without
  a `team_proposal` row it is Anthropic's `claude-opus-5`, as 0036 seeds the others.

`ON CONFLICT DO NOTHING`, so a retried deploy lands on the same schema.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0045"
down_revision: str | Sequence[str] | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _checks(tasks: str) -> None:
    op.execute("ALTER TABLE ai_tasks DROP CONSTRAINT IF EXISTS ck_ai_tasks_task")
    op.execute(f"ALTER TABLE ai_tasks ADD CONSTRAINT ck_ai_tasks_task CHECK (task IN ({tasks}))")
    op.execute("ALTER TABLE ai_usage DROP CONSTRAINT IF EXISTS ck_ai_usage_task")
    op.execute(f"ALTER TABLE ai_usage ADD CONSTRAINT ck_ai_usage_task CHECK (task IN ({tasks}))")


def upgrade() -> None:
    _checks("'card', 'team_proposal', 'cv_anonimo', 'crm_lead'")
    op.execute(
        """
        INSERT INTO ai_tasks (task, provider, model, effort, max_output_tokens, daily_cap_eur,
                              enabled)
        SELECT 'crm_lead', provider, model, 'low', 1500, 1, false
        FROM ai_tasks WHERE task = 'team_proposal'
        ON CONFLICT (task) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO ai_tasks (task, provider, model, effort, max_output_tokens, daily_cap_eur,
                              enabled)
        VALUES ('crm_lead', 'anthropic', 'claude-opus-5', 'low', 1500, 1, false)
        ON CONFLICT (task) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM ai_usage WHERE task = 'crm_lead'")
    op.execute("DELETE FROM ai_tasks WHERE task = 'crm_lead'")
    _checks("'card', 'team_proposal', 'cv_anonimo'")
