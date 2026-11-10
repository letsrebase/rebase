"""A mailbox: a connected account PigroCRM reads, and what it reads it for (REB-1014, § 2.2 of
`2026-11-09-incoming-documents-inbox-design.md`).

Two tables. `mailboxes` says whose the account is and how it is reached; `mailbox_purposes`
says what PigroCRM looks for in it and how far it has read.

**Gmail is wrapped, not copied.** A personal Gmail is the `google_accounts` row, one to one:
the credential, the consent window, the status and the last error stay there, so the unique-per-user
rule (`gmail/models.py`), the Drive-sibling rule (`gmail/oauth.py`) and every Gmail test are what
they were. The `mailboxes` row of a Gmail account holds nothing the account holds: its health is
read through `health.py`, and `status` is `NULL`. A mailbox the CRM reads itself (IMAP, REB-1015)
has no account behind it and carries its own health in `status`.

**One watermark per mailbox and purpose** is `mailbox_purposes.sync_watermark`, with one
exception that keeps the wrapping honest: the `correspondence` watermark of a Gmail mailbox is
still `GoogleAccount.sync_watermark`, the column the Gmail sync reads and writes; its purpose row
holds `NULL` there and `purpose_watermark` in `repository.py` is the one place that knows it.

**A personal mailbox never sources an inbox item** (REB-960). `billing` is allowed on a
space-owned mailbox only, and the database says so: `mailboxes.space_owned` is generated from the
owner, a purpose row carries a copy of it through a composite foreign key, and a check refuses
`billing` where the copy is false. An owner cannot change under a purpose either, since the
generated column would stop agreeing with the copy.
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin

PROVIDERS: Final = ("gmail", "imap")
PURPOSES: Final = ("correspondence", "billing")
# What a person reads, for every mailbox whatever its provider. `failing` is a transient failure
# the next cycle retries; `auth_failed` is one a person must act on (a changed password, a revoked
# grant); `disconnected` is what the person did.
HEALTH_STATES: Final = ("active", "failing", "auth_failed", "disconnected")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class Mailbox(Base, PrimaryKeyMixin, TimestampMixin):
    """`owner_user_id` is the person whose mailbox it is, `NULL` for the space's (a shared address
    such as fatture@ or a PEC, connected by an admin). `google_account_id` is set for Gmail and
    only for Gmail. A person has one Gmail through OAuth and a space has none in this project:
    space-owned mailboxes are IMAP only, which keeps an admin's personal Gmail and Drive from
    colliding with a space Gmail."""

    __tablename__ = "mailboxes"
    __table_args__ = (
        CheckConstraint(f"provider IN ({_in(PROVIDERS)})", name="ck_mailboxes_provider"),
        CheckConstraint(
            "(provider = 'gmail') = (google_account_id IS NOT NULL)",
            name="ck_mailboxes_gmail_wraps_an_account",
        ),
        CheckConstraint(
            "provider <> 'gmail' OR owner_user_id IS NOT NULL",
            name="ck_mailboxes_gmail_is_personal",
        ),
        CheckConstraint(
            f"(provider = 'gmail' AND status IS NULL) "
            f"OR (provider <> 'gmail' AND status IS NOT NULL AND status IN ({_in(HEALTH_STATES)}))",
            name="ck_mailboxes_status_belongs_to_the_mailbox_that_has_no_account",
        ),
        # The target of `mailbox_purposes`' composite foreign key.
        UniqueConstraint("id", "space_owned", name="uq_mailboxes_id_space_owned"),
        Index(
            "uq_mailboxes_one_gmail_per_user",
            "owner_user_id",
            unique=True,
            postgresql_where=text("provider = 'gmail'"),
        ),
    )

    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, default=None
    )
    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    google_account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("google_accounts.id", ondelete="CASCADE"), unique=True, default=None
    )
    status: Mapped[str | None] = mapped_column(String(20), default=None)
    space_owned: Mapped[bool] = mapped_column(
        Boolean, Computed("owner_user_id IS NULL", persisted=True), nullable=False
    )


class MailboxPurpose(Base, PrimaryKeyMixin, TimestampMixin):
    """One purpose of one mailbox and its watermark: where the next read starts. A first
    connection backfills 90 days by default; a purpose added later to an established mailbox
    backfills from its own watermark, which is why it is a row of its own."""

    __tablename__ = "mailbox_purposes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["mailbox_id", "space_owned"],
            ["mailboxes.id", "mailboxes.space_owned"],
            ondelete="CASCADE",
            name="fk_mailbox_purposes_mailbox",
        ),
        CheckConstraint(f"purpose IN ({_in(PURPOSES)})", name="ck_mailbox_purposes_purpose"),
        CheckConstraint(
            "purpose <> 'billing' OR space_owned", name="ck_mailbox_purposes_billing_is_the_spaces"
        ),
        UniqueConstraint("mailbox_id", "purpose", name="uq_mailbox_purposes_mailbox_purpose"),
    )

    mailbox_id: Mapped[UUID] = mapped_column(nullable=False)
    # A copy of `Mailbox.space_owned`, held by the composite foreign key; see the module docstring.
    space_owned: Mapped[bool] = mapped_column(Boolean, nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    sync_watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
