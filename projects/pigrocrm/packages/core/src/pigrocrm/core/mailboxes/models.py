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
    Integer,
    LargeBinary,
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
# How an IMAP connection is secured. There is no plaintext choice: `tls` is TLS from the first
# byte (port 993), `starttls` upgrades a plain connection before anything is sent (port 143).
IMAP_SECURITY: Final = ("tls", "starttls")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class Mailbox(Base, PrimaryKeyMixin, TimestampMixin):
    """`owner_user_id` is the person whose mailbox it is, `NULL` for the space's (a shared address
    such as fatture@ or a PEC, connected by an admin). `google_account_id` is set for Gmail and
    only for Gmail. A person has one Gmail through OAuth and a space has none in this project:
    space-owned mailboxes are IMAP only, which keeps an admin's personal Gmail and Drive from
    colliding with a space Gmail.

    An IMAP mailbox (which covers a PEC) carries its connection and its own health. It has no
    `__repr__` that prints a column, so a password's ciphertext cannot reach a log through it."""

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
        CheckConstraint(
            "provider <> 'imap' OR (address IS NOT NULL AND imap_host IS NOT NULL "
            "AND imap_port IS NOT NULL AND imap_security IS NOT NULL AND imap_username IS NOT NULL "
            "AND (status = 'disconnected' OR (password_ciphertext IS NOT NULL "
            "AND password_nonce IS NOT NULL)))",
            name="ck_mailboxes_imap_holds_its_connection",
        ),
        CheckConstraint(
            "provider <> 'gmail' OR (address IS NULL AND imap_host IS NULL AND imap_port IS NULL "
            "AND imap_security IS NULL AND imap_username IS NULL AND password_ciphertext IS NULL "
            "AND password_nonce IS NULL)",
            name="ck_mailboxes_gmail_holds_no_imap_connection",
        ),
        CheckConstraint(
            f"imap_security IS NULL OR imap_security IN ({_in(IMAP_SECURITY)})",
            name="ck_mailboxes_imap_security",
        ),
        CheckConstraint(
            "imap_port IS NULL OR (imap_port BETWEEN 1 AND 65535)", name="ck_mailboxes_imap_port"
        ),
        # A space's mailbox has no owner, and two NULLs are two owners to a unique index.
        Index(
            "uq_mailboxes_imap_address",
            text("coalesce(owner_user_id, '00000000-0000-0000-0000-000000000000')"),
            text("lower(address)"),
            unique=True,
            postgresql_where=text("provider = 'imap'"),
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
    # IMAP only (REB-1015); every one is NULL on a Gmail mailbox, which holds none of it. `address`
    # is the mailbox's own address, what its sent mail is told apart by; `imap_username` is what
    # the server logs in (often the same, and for some hosts not).
    address: Mapped[str | None] = mapped_column(String(320), default=None)
    imap_host: Mapped[str | None] = mapped_column(String(255), default=None)
    imap_port: Mapped[int | None] = mapped_column(Integer, default=None)
    imap_security: Mapped[str | None] = mapped_column(String(10), default=None)
    imap_username: Mapped[str | None] = mapped_column(String(320), default=None)
    # AES-256-GCM under `PIGROCRM_MAILBOX_TOKEN_KEY` derived for the space. Never returned,
    # never logged: a `Mailbox` has no `__repr__` that prints a column.
    password_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    password_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    # The sentence a person reads (Italian), never a line the server sent and never a password.
    last_error: Mapped[str | None] = mapped_column(String(500), default=None)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # When the current run of transient failures began, `NULL` while the mailbox reads: what the
    # `failing` ladder is counted from.
    failing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


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
