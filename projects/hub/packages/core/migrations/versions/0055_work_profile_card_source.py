"""ai_sources: the work profile as a source of the anonymous card

Revision ID: 0055
Revises: 0054

REB-1134, § 6 of the work-profile design (2026-11-17). The card writer may read an approved,
unexpired work profile as one more source, beside `github` and `links`. It gets the same kind of
switch, `work_profile`, **off** until the privacy notice covers it.

`ai_sources.kind` was `VARCHAR(10)`, which `work_profile` (12 characters) does not fit: the column
becomes `VARCHAR(20)` and `ck_ai_sources_kind` names the three kinds. The switch is seeded off.
Downgrading removes the row and puts the old column and constraint back.

Conditional like every migration of this package: `IF EXISTS`, `ON CONFLICT DO NOTHING`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0055"
down_revision: str | Sequence[str] | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_sources ALTER COLUMN kind TYPE VARCHAR(20)")
    op.execute("ALTER TABLE ai_sources DROP CONSTRAINT IF EXISTS ck_ai_sources_kind")
    op.execute(
        "ALTER TABLE ai_sources ADD CONSTRAINT ck_ai_sources_kind "
        "CHECK (kind IN ('github', 'links', 'work_profile'))"
    )
    op.execute(
        "INSERT INTO ai_sources (kind, enabled) VALUES ('work_profile', false) "
        "ON CONFLICT (kind) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM ai_sources WHERE kind = 'work_profile'")
    op.execute("ALTER TABLE ai_sources DROP CONSTRAINT IF EXISTS ck_ai_sources_kind")
    op.execute(
        "ALTER TABLE ai_sources ADD CONSTRAINT ck_ai_sources_kind "
        "CHECK (kind IN ('github', 'links'))"
    )
    op.execute("ALTER TABLE ai_sources ALTER COLUMN kind TYPE VARCHAR(10)")
