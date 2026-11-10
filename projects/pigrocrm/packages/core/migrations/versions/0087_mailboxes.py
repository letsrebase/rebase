"""mailboxes: owners, providers and purposes, wrapping every Google account

Revision ID: 0087
Revises: 0086

REB-1014, § 2.2 of `2026-11-09-incoming-documents-inbox-design.md`.

`mailboxes` is a connected account PigroCRM reads: an owner (a person, or the space when
`owner_user_id` is null), a provider (`gmail` or `imap`) and, for Gmail, the `google_accounts`
row it wraps one to one. The unique-per-user rule and the Drive-sibling rule stay on the account;
a person has at most one Gmail mailbox (a partial unique index) and a Gmail mailbox is always
personal. Only a mailbox with no account carries its own health in `status`, and the check says so.

`mailbox_purposes` is what PigroCRM looks for in a mailbox, `correspondence` (the old Gmail sync) or
`billing`, with a watermark per purpose. `space_owned` is generated on `mailboxes` and copied onto
the purpose row through a composite foreign key, so that the check `billing` implies `space_owned`
is a fact of the schema: a personal mailbox never sources an inbox item (REB-960).

Every existing Google account gets its mailbox and its `correspondence` purpose. The watermark of
that purpose stays `google_accounts.sync_watermark`, which the Gmail sync reads and writes, so the
purpose row's own is left null.

The scheduled job `gmail-sync` becomes `mail-sync` in the clock table (`reminder_job_run`), so the
space's «last run» survives the rename. `downgrade` drops both tables and puts the job name back.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0087"
down_revision: str | Sequence[str] | None = "0086"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HEALTH = "'active', 'failing', 'auth_failed', 'disconnected'"


def _rename_job(old: str, new: str) -> None:
    """The clock row of a job under its new name. A row already under the new name (a run of the
    renamed command that came first) is the newer one and wins; the old one is dropped."""
    op.execute(
        sa.text(
            "UPDATE reminder_job_run SET job = :new WHERE job = :old "
            "AND NOT EXISTS (SELECT 1 FROM reminder_job_run WHERE job = :new)"
        ).bindparams(old=old, new=new)
    )
    op.execute(sa.text("DELETE FROM reminder_job_run WHERE job = :old").bindparams(old=old))


def upgrade() -> None:
    op.create_table(
        "mailboxes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=10), nullable=False),
        sa.Column("google_account_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column(
            "space_owned",
            sa.Boolean(),
            sa.Computed("owner_user_id IS NULL", persisted=True),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["google_account_id"], ["google_accounts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("google_account_id"),
        sa.UniqueConstraint("id", "space_owned", name="uq_mailboxes_id_space_owned"),
        sa.CheckConstraint("provider IN ('gmail', 'imap')", name="ck_mailboxes_provider"),
        sa.CheckConstraint(
            "(provider = 'gmail') = (google_account_id IS NOT NULL)",
            name="ck_mailboxes_gmail_wraps_an_account",
        ),
        sa.CheckConstraint(
            "provider <> 'gmail' OR owner_user_id IS NOT NULL",
            name="ck_mailboxes_gmail_is_personal",
        ),
        sa.CheckConstraint(
            f"(provider = 'gmail' AND status IS NULL) "
            f"OR (provider <> 'gmail' AND status IS NOT NULL AND status IN ({HEALTH}))",
            name="ck_mailboxes_status_belongs_to_the_mailbox_that_has_no_account",
        ),
    )
    op.create_index("ix_mailboxes_owner_user_id", "mailboxes", ["owner_user_id"])
    op.create_index(
        "uq_mailboxes_one_gmail_per_user",
        "mailboxes",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text("provider = 'gmail'"),
    )
    op.create_table(
        "mailbox_purposes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("mailbox_id", sa.Uuid(), nullable=False),
        sa.Column("space_owned", sa.Boolean(), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("sync_watermark", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["mailbox_id", "space_owned"],
            ["mailboxes.id", "mailboxes.space_owned"],
            name="fk_mailbox_purposes_mailbox",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "purpose IN ('correspondence', 'billing')", name="ck_mailbox_purposes_purpose"
        ),
        sa.CheckConstraint(
            "purpose <> 'billing' OR space_owned", name="ck_mailbox_purposes_billing_is_the_spaces"
        ),
        sa.UniqueConstraint("mailbox_id", "purpose", name="uq_mailbox_purposes_mailbox_purpose"),
    )
    op.execute(
        "INSERT INTO mailboxes (id, owner_user_id, provider, google_account_id) "
        "SELECT gen_random_uuid(), user_id, 'gmail', id FROM google_accounts"
    )
    op.execute(
        "INSERT INTO mailbox_purposes (id, mailbox_id, space_owned, purpose) "
        "SELECT gen_random_uuid(), id, space_owned, 'correspondence' FROM mailboxes"
    )
    _rename_job("gmail-sync", "mail-sync")


def downgrade() -> None:
    _rename_job("mail-sync", "gmail-sync")
    op.drop_table("mailbox_purposes")
    op.drop_table("mailboxes")
