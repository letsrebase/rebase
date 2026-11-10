from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    insert,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.mailboxes.models import Mailbox, MailboxPurpose


class GoogleAccount(Base, PrimaryKeyMixin, TimestampMixin):
    """One connected mailbox, per CRM user.

    `status` has four values, and every distinction between them is load-bearing
    because each calls for something different from the person:

    * `active` -- nothing to do.
    * `expired` is what we *predicted*: the consent window has passed with no successful
      refresh since. A warning to show early.
    * `revoked` is what Google *told us* (`invalid_grant`). A fact to record.
    * `disconnected` is what the *user* did, from Impostazioni → Gmail.

    The last one used to share `revoked`'s value, told apart only by `disconnected_at`
    being non-null. That was a real defect and not a tidiness point: every screen and
    every gate reading `status` told somebody who had just unhooked their own mailbox
    that Google had revoked their consent, and offered to reconnect what they had
    deliberately disconnected. Two events, two states -- the same argument that keeps
    `expired` and `revoked` apart, and a view is not the place to reconstruct it from a
    nullable timestamp.

    `disconnected_at` stays, because *when* is still worth knowing; it is no longer what
    carries the meaning.

    `scopes_granted` deliberately does not feed `status`. Capability is derived from it
    at the point of use; the health of the credential is `status`. Google may grant a
    subset, and an account with `gmail.send` but not `gmail.readonly` is a healthy
    credential on which sync is unavailable.

    Nothing on this class is safe to print. There is deliberately no `__repr__`:
    SQLAlchemy's declarative base does not add one, so the inherited `object.__repr__`
    prints the class and an address and nothing else, which is the behaviour the two
    `refresh_token_*` columns need from every traceback and every debugger frame.
    """

    __tablename__ = "google_accounts"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    google_sub: Mapped[str] = mapped_column(String(255), nullable=False)
    email_address: Mapped[str] = mapped_column(String(320), nullable=False)
    # AES-256-GCM, key from PIGROCRM_GOOGLE_TOKEN_KEY. Never logged, never returned by
    # any schema, never in an exception message.
    refresh_token_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    refresh_token_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    scopes_granted: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    # When the *consent* must be renewed, not when an access token expires. Set to
    # connected_at + 7 days while PIGROCRM_GOOGLE_APP_UNVERIFIED is true, because that
    # is Google's Testing-mode behaviour and Google exposes no API to detect it.
    consent_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # The sentence the user reads, in Italian. Never a stack trace, never an upstream
    # body, never a token.
    last_error: Mapped[str | None] = mapped_column(String(500), default=None)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    sync_watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Off means headers and Gmail's snippet only, with the read-through degradation of
    # spec 5.4 point 2 as a declared consequence rather than a hidden one.
    gmail_store_bodies: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


@event.listens_for(GoogleAccount, "after_insert")
def _wrap_in_a_mailbox(
    _mapper: Mapper[GoogleAccount], connection: Connection, account: GoogleAccount
) -> None:
    """Every Google account is a mailbox (REB-1014, § 2.2 of the incoming-documents design): its
    owner is the account's user, its purpose `correspondence`, which is what Gmail sync has always
    done. Made here, at the insert, so no way of creating an account (the consent flow, a script,
    a test) can leave one unwrapped; the rows go when the account does (`ON DELETE CASCADE`).
    Nothing the account holds is copied: see `mailboxes/models.py`."""
    mailbox_id = uuid7()
    connection.execute(
        insert(Mailbox).values(
            id=mailbox_id,
            owner_user_id=account.user_id,
            provider="gmail",
            google_account_id=account.id,
        )
    )
    connection.execute(
        insert(MailboxPurpose).values(
            id=uuid7(), mailbox_id=mailbox_id, space_owned=False, purpose="correspondence"
        )
    )


class GoogleOAuthState(Base, PrimaryKeyMixin, TimestampMixin):
    """One in-flight authorisation.

    It exists because **PKCE's `code_verifier` has to stay server-side** between
    `/start` and `/callback`: putting it inside the signed `state` would make it
    readable by the browser and cancel PKCE entirely. It is also the registry that
    makes the JWT's `jti` single-use in fact rather than in principle.

    `consumed_at` is a nullable timestamp and not a boolean because single-use has to
    survive a race: the redemption is one conditional `UPDATE ... WHERE consumed_at IS
    NULL`, which Postgres serialises on the row lock, so two simultaneous callbacks
    produce exactly one winner. A read-then-write pair over a boolean would not.

    Expired rows are pruned whenever a flow starts a consent (`google/states.py`) --
    unlike `refresh_tokens`, which residuo R8 records as never pruned at all.

    `purpose` is what spec 9 §5.2 adds for the Drive credential: one in-flight
    authorisation registry serves both flows, so the callback for one cannot be replayed
    as if it belonged to the other. It defaults to `"gmail"` because every row this table
    held before Drive existed was one, and the existing Gmail flow never sets it -- the
    column has to keep meaning "this state started a Gmail connection" for a row nobody
    touched, not "this state's purpose happens to be unknown".
    """

    __tablename__ = "google_oauth_states"

    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    purpose: Mapped[str] = mapped_column(String(10), nullable=False, default="gmail")


class GmailMessage(Base, PrimaryKeyMixin, TimestampMixin):
    """One synchronised message.

    The unique constraint on `(google_account_id, gmail_message_id)` is what makes the
    watermark's deliberate 24-hour overlap free and every re-run idempotent, *and* it is
    the only thing that holds when two cycles overlap in time: a `SELECT` before the
    `INSERT` is a check both of them pass. The previous system kept its send record in a JSON file
    on disk with a non-atomic read-modify-write, so two concurrent sends lost the count; a
    unique constraint cannot lose anything.

    Like `GoogleAccount`, this class deliberately has no `__repr__`. `body_text` holds
    somebody's private correspondence, and a generated `repr` would print it into every
    traceback, every `logger.debug("%s", row)` and every pytest failure dump.

    `String(998)` on the header columns is RFC 5322's maximum line length minus the
    field name: the real bound rather than a guessed one.
    """

    __tablename__ = "gmail_messages"
    __table_args__ = (
        UniqueConstraint(
            "google_account_id", "gmail_message_id", name="uq_gmail_messages_account_message"
        ),
        # The thread view reads a whole conversation in date order, which is the only
        # access pattern this table has that is not a lookup by gmail id.
        Index("ix_gmail_messages_thread_date", "gmail_thread_id", "internal_date"),
    )

    google_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("google_accounts.id", ondelete="CASCADE"), nullable=False
    )
    gmail_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    gmail_thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    message_id_header: Mapped[str] = mapped_column(String(998), nullable=False, default="")
    in_reply_to: Mapped[str] = mapped_column(String(998), nullable=False, default="")
    # `Text`, not `String(998)`: `References` accumulates one Message-ID per reply and a
    # long thread runs past any line limit, folded across several lines.
    references: Mapped[str] = mapped_column(Text, nullable=False, default="")
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    from_address: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    to_addresses: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    cc_addresses: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    subject: Mapped[str] = mapped_column(String(998), nullable=False, default="")
    snippet: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    internal_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    body_html_discarded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # name, mime, size. No bytes, ever (spec 5.4).
    attachments: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list
    )


class GmailMessageLink(Base, PrimaryKeyMixin, TimestampMixin):
    """Many-to-many, and not three nullable foreign keys: one email concerns the
    person, that person's customer and a deal all at once, and a single FK would force
    a choice the data does not support."""

    __tablename__ = "gmail_message_links"
    __table_args__ = (
        UniqueConstraint(
            "gmail_message_id", "entity_type", "entity_id", name="uq_gmail_message_links_triple"
        ),
        Index("ix_gmail_message_links_entity", "entity_type", "entity_id"),
    )

    gmail_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("gmail_messages.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # Deliberately not a foreign key: it points at a customer, a person or a deal
    # depending on `entity_type`, and no single FK can express that. The `resolve` of
    # `gmail/roster.py` is what keeps it pointing at rows that exist.
    entity_id: Mapped[UUID] = mapped_column(nullable=False)


class GmailKnownAddress(Base, PrimaryKeyMixin, TimestampMixin):
    """Which roster addresses this account has already looked for.

    This is how "an address added to the CRM gets backfilled" happens *without*
    `PersonService` learning what Gmail is. The Gmail service compares the roster
    against this table at the start of each cycle and treats the difference as a
    backfill -- so a new address is synchronised on the next cycle, not in the instant
    it was saved, and the interface says so (spec 4.4) instead of leaving the user to
    discover it.

    Per account, and the unique constraint says so: two users may both correspond with
    the same client, and each mailbox has to be searched back over that address once on
    its own. A global register would give whichever mailbox synced second nothing.

    Deliberately not a column on `people` or `organizations`. A CRM row is not the place to
    record what one particular Google mailbox has been asked; putting it there would
    also mean a customer's address could only ever be backfilled for one user, and it
    would make the address book carry Gmail's bookkeeping into every other feature.
    """

    __tablename__ = "gmail_known_addresses"
    __table_args__ = (
        UniqueConstraint("google_account_id", "address", name="uq_gmail_known_addresses"),
    )

    google_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("google_accounts.id", ondelete="CASCADE"), nullable=False
    )
    address: Mapped[str] = mapped_column(String(320), nullable=False)
    # When this mailbox first went looking for that address. Kept because it is the only
    # answer to "why did three months of mail from this person appear on Tuesday".
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EmailDraft(Base, PrimaryKeyMixin, TimestampMixin):
    """A message being written, and the record of what happened to it.

    Written to the database **before** Gmail is called. Two reasons, and the second is
    the load-bearing one: losing hand-written text to an HTTP error is unforgivable, and
    the `message_id_header` minted here is what makes an unknown send outcome resolvable
    by an exact lookup instead of a guess (spec 6.3).

    The previous system kept this in a JSON file with a non-atomic read-modify-write, so two
    concurrent sends lost the count. The columns are the same ones -- they were the right
    columns -- on a support that cannot lose a write.

    Like `GmailMessage`, this class deliberately has no `__repr__`: `body_markdown` and
    `to_addresses` are somebody's private correspondence, and a generated one would put
    them in every traceback and every test failure dump.
    """

    __tablename__ = "email_drafts"
    __table_args__ = (
        UniqueConstraint("message_id_header", name="uq_email_drafts_message_id"),
        Index("ix_email_drafts_entity", "entity_type", "entity_id"),
        Index("ix_email_drafts_send_state", "send_state"),
    )

    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # Deliberately not a foreign key, for the same reason as `GmailMessageLink.entity_id`:
    # it points at a customer, a person or a deal depending on `entity_type`, and no
    # single FK can express that. `EmailDraftService` validates it against the table
    # `entity_type` names, so a well-formed UUID is a `NotFound` and never an
    # `IntegrityError` from the driver.
    entity_id: Mapped[UUID] = mapped_column(nullable=False)
    # **Which mailbox this draft was sent from.** Written by the send claim, not by
    # `create`: until somebody presses Invia there is no mailbox involved, and the
    # column's whole job is to say whose outcome an unresolved send is.
    #
    # It exists because of a defect B2-6 could mitigate and not remove. `reconcile_all`
    # walks every draft in `uncertain`/`sending`, and it runs inside each user's own sync
    # cycle -- so on a multi-user install, user A's cycle would pick up user B's
    # unresolved draft, look for it in *A's* mailbox, find nothing, and past the grace
    # window write `failed` on a message sitting in B's client's inbox. Telling somebody
    # an email failed when it was delivered is the one outcome in this slice that cannot
    # be walked back, because the reply to it is to send the message again.
    #
    # `SET NULL` and not `CASCADE`, like `in_reply_to_message_id` above and for the same
    # reason: a draft is the user's own text, and disconnecting the mailbox it went out
    # from is no reason to delete it. NULL therefore means "no mailbox is known for this
    # one", which is also what an install that had two accounts when 0020 ran is left
    # with -- and which the reconciliation reads as "not mine", never as "everyone's".
    google_account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("google_accounts.id", ondelete="SET NULL"), default=None
    )
    to_addresses: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    cc_addresses: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    subject: Mapped[str] = mapped_column(String(998), nullable=False, default="")
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attachment_version_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Ours, minted at creation. Unique, because the reconciliation looks a draft up by
    # it: two drafts sharing one id would make that lookup ambiguous exactly when it
    # matters most. The constraint is the database's and not uuid7's good luck --
    # `test_email_drafts.py` races two concurrent creates onto one id to prove it.
    message_id_header: Mapped[str] = mapped_column(String(998), nullable=False)
    # The thread this reply or reminder belongs to, so In-Reply-To/References can be set.
    # `SET NULL` rather than `CASCADE`: a draft is the user's own text, and the message
    # it answers going away is no reason to delete it -- it only stops being a reply.
    in_reply_to_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("gmail_messages.id", ondelete="SET NULL"), default=None
    )
    send_state: Mapped[str] = mapped_column(String(10), nullable=False, default="draft")
    send_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # The sentence the user reads, in Italian. Never an upstream body, never a token,
    # never the recipient -- same rule as `GoogleAccount.last_error`.
    last_error: Mapped[str | None] = mapped_column(String(500), default=None)
    # The sent message, once Gmail has answered. Never before.
    sent_gmail_message_id: Mapped[str | None] = mapped_column(String(128), default=None)
    # The reminder this draft carries, once there is one. The foreign key B2-3 recorded
    # as deliberately absent -- `payment_reminders` did not exist yet -- is here now, and
    # 0020 adds it to the table that 0018 created without it.
    #
    # `SET NULL`, so deleting a reminder row leaves the text somebody wrote; the reverse
    # direction (`payment_reminders.email_draft_id`) is `SET NULL` too, for the same
    # reason in the other order.
    #
    # `use_alter=True` because the two tables point at each other, and without it
    # `Base.metadata.create_all` -- which is how the test schema is built -- cannot sort
    # them and raises `CircularDependencyError` before a single test runs. With it the
    # constraint is emitted as its own `ALTER TABLE` after both tables exist, which is
    # exactly what 0020 does by hand.
    payment_reminder_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "payment_reminders.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_email_drafts_payment_reminder",
        ),
        default=None,
    )


class PaymentReminder(Base, PrimaryKeyMixin, TimestampMixin):
    """One reminder for one invoice, at one position in the sequence.

    The unique constraint on `(invoice_id, sequence)` is the first of the three layers of
    spec 7.3, and it is the database that guarantees it rather than application code: two
    concurrent creates both pass the count that precedes them, and only the constraint
    stops the second -- which then becomes a `Conflict` instead of a second letter.

    The previous system had none of this. `wasSent = emailSentCount > 0` chose between a courtesy
    copy and a reminder, and nothing anywhere checked a due date, an interval or a ceiling.
    Pressing the button ten times sent ten emails -- and the choice was wrong even when it
    worked: a courtesy copy resent because the first bounced became, on the second send, a
    letter of demand.

    **`sent_at` is filled by the send path and never here.** A row exists from the moment
    the reminder is *prepared*, which is what `create_reminder` does; the draft then goes
    out through the one send path in the slice, and `EmailSendService._record_sent` is
    what stamps this column. The two are therefore not synonyms and the service reads
    them differently: the ceiling counts rows (three prepared reminders occupy the three
    positions the sequence has), while the escalation of the wording counts `sent_at`,
    because «nonostante il precedente sollecito» about a letter still sitting in the
    drafts folder is a sentence that describes something that never happened.

    `email_draft_id` is `SET NULL` and not `CASCADE`: deleting the draft is allowed only
    while it is still editable, and a reminder that lost its unsent text is still the
    record that a reminder was prepared at that position.
    """

    __tablename__ = "payment_reminders"
    __table_args__ = (
        UniqueConstraint("invoice_id", "sequence", name="uq_payment_reminders_invoice_sequence"),
    )

    invoice_id: Mapped[UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    email_draft_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("email_drafts.id", ondelete="SET NULL"), default=None, index=True
    )
