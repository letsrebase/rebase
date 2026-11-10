"""reminder_job_run: the last failed attempt of a scheduled job, beside its last success

Revision ID: 0061
Revises: 0060

REB-764. `reminder_job_run` already holds one row per scheduled job with the time it last
ran (`ran_at`, written by the reminder pass since 0054). The four cron commands now all
write it, and Impostazioni → Esecuzioni reads it, so the row also has to say when the job
last failed and why. Two nullable columns, and `ran_at` becomes nullable too, because a job
whose first attempt failed has a failure and no success to show.

`ran_at` keeps its meaning, the last time the job ran to the end: `job_late` reads it, so a
job that ran once and fails on every run since goes late (one that never ran to the end has
no time to measure from, and is the panel's «non riuscita»). `failed_at` and `failure`
describe the latest attempt only when it failed, and a success clears them, so
«failed_at is not null» is «the last attempt failed». `failure` is a short word, an
exception's type name or one of the product's own machine words, never a message: a
message can quote an address. Two CHECKs keep the row honest: `failed_at` and `failure`
come together, and a row has at least one of its two times.

Nothing is backfilled: the rows that exist are the reminder pass's successes. The
downgrade drops the rows that have no success (they carry nothing the old shape can hold)
before it puts the NOT NULL back.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0061"
down_revision: str | Sequence[str] | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "reminder_job_run"
PAIR = "ck_reminder_job_run_failure_pair"
HAS_A_TIME = "ck_reminder_job_run_has_a_time"


def upgrade() -> None:
    op.alter_column(TABLE, "ran_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.add_column(TABLE, sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(TABLE, sa.Column("failure", sa.String(length=200), nullable=True))
    op.create_check_constraint(PAIR, TABLE, "(failed_at IS NULL) = (failure IS NULL)")
    op.create_check_constraint(HAS_A_TIME, TABLE, "ran_at IS NOT NULL OR failed_at IS NOT NULL")


def downgrade() -> None:
    op.drop_constraint(HAS_A_TIME, TABLE, type_="check")
    op.drop_constraint(PAIR, TABLE, type_="check")
    op.execute(f"DELETE FROM {TABLE} WHERE ran_at IS NULL")
    op.drop_column(TABLE, "failure")
    op.drop_column(TABLE, "failed_at")
    op.alter_column(TABLE, "ran_at", existing_type=sa.DateTime(timezone=True), nullable=False)
