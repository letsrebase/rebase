"""inbox_items: the message a file came in by mail from

Revision ID: 0099
Revises: 0098

REB-1016, § 2.2 of `2026-11-09-incoming-documents-inbox-design.md`.

The `billing` read of a space mailbox files each attachment of an accepted type as an inbox item
with `source = 'mailbox'`, the mailbox, the message (UIDVALIDITY and UID, so that a message read
twice is one message), and its sender, subject and date. Never the body. A check ties `source` to
the message columns. `mailbox_id` is cleared if a mailbox row is deleted; disconnecting one leaves
the row and the items. Nothing is backfilled: no item came by mail before.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0099"
down_revision: str | Sequence[str] | None = "0098"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("inbox_items", sa.Column("mailbox_id", sa.Uuid(), nullable=True))
    op.add_column("inbox_items", sa.Column("mail_uidvalidity", sa.BigInteger(), nullable=True))
    op.add_column("inbox_items", sa.Column("mail_uid", sa.BigInteger(), nullable=True))
    op.add_column("inbox_items", sa.Column("mail_message_id", sa.String(length=998), nullable=True))
    op.add_column("inbox_items", sa.Column("mail_sender", sa.String(length=320), nullable=True))
    op.add_column("inbox_items", sa.Column("mail_subject", sa.String(length=998), nullable=True))
    op.add_column("inbox_items", sa.Column("mail_date", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_inbox_items_mailbox_id_mailboxes",
        "inbox_items",
        "mailboxes",
        ["mailbox_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint("ck_inbox_items_source", "inbox_items", type_="check")
    op.create_check_constraint(
        "ck_inbox_items_source", "inbox_items", "source IN ('upload', 'mailbox')"
    )
    op.create_check_constraint(
        "ck_inbox_items_mail_message",
        "inbox_items",
        "(source = 'mailbox') = (mail_uid IS NOT NULL AND mail_uidvalidity IS NOT NULL)",
    )
    op.create_index(
        "ix_inbox_items_mail_message",
        "inbox_items",
        ["mailbox_id", "mail_uidvalidity", "mail_uid"],
    )


def downgrade() -> None:
    # The older schema allows uploads only, and the items are the space's: they stay, as uploads
    # that no longer say which message they came in.
    op.drop_index("ix_inbox_items_mail_message", table_name="inbox_items")
    op.drop_constraint("ck_inbox_items_mail_message", "inbox_items", type_="check")
    op.execute("UPDATE inbox_items SET source = 'upload' WHERE source = 'mailbox'")
    op.drop_constraint("ck_inbox_items_source", "inbox_items", type_="check")
    op.create_check_constraint("ck_inbox_items_source", "inbox_items", "source IN ('upload')")
    op.drop_constraint("fk_inbox_items_mailbox_id_mailboxes", "inbox_items", type_="foreignkey")
    for column in (
        "mail_date",
        "mail_subject",
        "mail_sender",
        "mail_message_id",
        "mail_uid",
        "mail_uidvalidity",
        "mailbox_id",
    ):
        op.drop_column("inbox_items", column)
