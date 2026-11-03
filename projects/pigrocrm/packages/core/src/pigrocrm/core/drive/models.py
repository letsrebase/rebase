from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin


class GoogleDriveAccount(Base, PrimaryKeyMixin, TimestampMixin):
    """One connected Drive, per CRM user (spec 9 §5.2).

    A **second** credential, not a wider `GoogleAccount`, because a Drive connection and
    a Gmail connection are two separate grants that a person can hold, revoke and reconnect
    independently of one another: consenting to Drive says nothing about Gmail, and
    disconnecting Gmail must not touch a Drive credential that was never in question.
    `google_sub` on this row can, and normally will, equal the `google_sub` on that
    user's `GoogleAccount` -- the same Google identity, granting the CRM a second,
    separate set of scopes -- but the two rows share no foreign key between them and
    fail independently.

    `status` carries the same four values, and the same distinction, that `GoogleAccount.
    status`'s docstring works through for Gmail: `active` (nothing to do), `expired` (our
    own prediction that the consent window has lapsed), `revoked` (what Google told us),
    `disconnected` (what the user did, from Impostazioni → Drive). The same argument
    applies unchanged -- a mailbox and a Drive folder are different resources, but the
    four things that can be true of a Google OAuth grant are the same four things
    whichever resource it grants access to -- so the column is kept apart from status
    rather than shared with it, for the same reason `disconnected` was split from
    `revoked` there: a view is not the place to reconstruct which of two events happened
    from a nullable timestamp.

    `root_folder_ids`, `storage_folder_id` and `storage_folder_verified` have no
    equivalent on `GoogleAccount`: they
    are Drive's own configuration, not the credential's. `root_folder_ids` is the set of
    folders the CRM is allowed to read from; `storage_folder_id` is the one folder it may
    write generated documents into. Both are Drive file ids, never a query nor free text,
    which is what `drive/schemas.py`'s `DriveRootsUpdate` pattern enforces before either
    ever reaches this table.

    Nothing on this class is safe to print. There is deliberately no `__repr__`, for the
    same reason `GoogleAccount` has none: the inherited `object.__repr__` prints the
    class and an address and nothing else, which is the behaviour the two
    `refresh_token_*` columns need from every traceback and every debugger frame.
    """

    __tablename__ = "google_drive_accounts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'revoked', 'expired', 'disconnected')",
            name="ck_google_drive_accounts_status",
        ),
    )

    # Unique: one Drive connection per CRM user, the same cardinality `GoogleAccount`
    # enforces for Gmail and for the same reason -- a second row would leave two
    # credentials both claiming to be *the* Drive for one person, with no rule for
    # which one a caller should use.
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    google_sub: Mapped[str] = mapped_column(String(255), nullable=False)
    email_address: Mapped[str] = mapped_column(String(320), nullable=False)
    # AES-256-GCM, key from PIGROCRM_GOOGLE_TOKEN_KEY -- the same key and the same
    # `seal`/`unseal` in `core/crypto.py`, since sealing is a property of the column
    # shape and not of which Google product granted the token. Never logged, never
    # returned by any schema, never in an exception message.
    refresh_token_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    refresh_token_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    scopes_granted: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # When the *consent* must be renewed, not when an access token expires -- see
    # `GoogleAccount.consent_expires_at`.
    consent_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # The folders the CRM may read from. A list and not a single id: spec 9 §5.2 lets a
    # person pick more than one root, and an empty list is a configured-but-unset Drive
    # rather than "everything" -- there is no implicit root.
    root_folder_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # The one folder generated documents are written into. `None` until a person sets
    # it, and distinct from `root_folder_ids` because reading and writing are different
    # permissions granted by different scopes (`drive.readonly` vs `drive.file`).
    storage_folder_id: Mapped[str | None] = mapped_column(String(128), default=None)
    # Whether anybody has ever proven `storage_folder_id` is reachable with *this*
    # credential and is actually a folder. Not derivable from the id itself, which is
    # why it is a column: `set_roots` can only verify a folder while the credential is
    # `active` and holds both Drive scopes, so a folder chosen from a revoked or expired
    # account is saved unverified -- and the panel resends the same id on every save, so
    # without this flag "unchanged, skip it" would mean that folder is never proven at
    # all and the first generated document discovers the mistake as a failed upload.
    # `False` is the only honest default for a row that predates the column, and for a
    # cleared folder: there is no verified `None`.
    storage_folder_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The sentence the user reads, in Italian. Never a stack trace, never an upstream
    # body, never a token -- same rule as `GoogleAccount.last_error`.
    last_error: Mapped[str | None] = mapped_column(String(500), default=None)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
