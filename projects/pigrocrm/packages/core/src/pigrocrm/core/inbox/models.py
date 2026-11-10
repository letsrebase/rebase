"""`inbox_items`: one file that arrived, before it becomes anything (design § 1).

The bytes live in the existing storage under a key of the inbox's own (`inbox/<id>/<slug>`),
so an item needs no customer, deal or contract as its owner: a `Document` must have exactly
one (`ck_documents_customer_xor_deal`) and a supplier invoice has none of them.

`kind` and `state` are `String` plus a `CHECK`, as every closed set of this schema is. The
five states are the design's: `new`, `proposed` (a proposal waits for a person), `filed` (it
became a record), `duplicate` and `discarded`. Upload leaves an item `new`, or `duplicate`
(of which item) or `discarded` (why) when it can say so from the file alone, and a ZIP kept
as the parent of its entries `filed`: it became its entries, which point back at it, and no
person has anything left to do with it. `proposed` and the way to `filed` for a record are
the classifier's and the modules' (later cards).

Deduplication keys (design § 3.3): `content_sha256`, the hash of the document the file
carries (a `.p7m`'s is the XML inside), and `natural_key`, the digest of the issuer tax id,
number, date and document type of the invoices of a FatturaPA. Both are indexed and neither
is unique: uniqueness is the service's, under `InboxRepository.lock_for_dedup`, and it holds
among the items the caller can see. A unique index would make a person limited to some legal
entities fail, with a 500, on the file of an item they are not allowed to see.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, deferred, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin
from pigrocrm.core.inbox.filetypes import FILE_TYPES

ITEM_SOURCES: tuple[str, ...] = ("upload", "mailbox")
ITEM_KINDS: tuple[str, ...] = (
    "sales_invoice",
    "supplier_invoice",
    "credit_note",
    "receipt",
    "bank_statement",
    "contract",
    "tax_document",
    "payslip",
    "archive",
    "other",
)
"""The design's kinds, and `archive` for an item that is a container (a ZIP) and carries no
document of its own. Upload decides only `archive` and, for an envelope it cannot open,
`other`: the rest is the classifier's."""
PAY_KINDS: tuple[str, ...] = ("payslip", "tax_document")
"""The kinds only whoever may read pay sees (`Actor.reads_pay`, staff design § 4): a payslip and
the consultant's F24 carry pay. `inbox/access.py` leaves them out of every other reader's view."""
ITEM_STATES: tuple[str, ...] = ("new", "proposed", "filed", "duplicate", "discarded")

FILE_NAME_MAX_LENGTH = 255
EXTRACTED_TEXT_MAX = 100_000
READ_CLAIM_MINUTES = 10
MAX_READ_ATTEMPTS = 3
STORAGE_KEY_MAX_LENGTH = 255


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class InboxItem(Base, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "inbox_items"

    source: Mapped[str] = mapped_column(String(20), nullable=False, default="upload")
    original_name: Mapped[str] = mapped_column(String(FILE_NAME_MAX_LENGTH), nullable=False)
    # `NULL` for a file of a type the inbox does not take, found inside a ZIP: it is kept as a
    # discarded item with its reason and no bytes, so nothing a ZIP held goes unaccounted for.
    file_type: Mapped[str | None] = mapped_column(String(10), default=None)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # `NULL` once the bytes are somewhere else (filed) or were never kept (a file the same
    # bytes of which an item already holds).
    storage_key: Mapped[str | None] = mapped_column(String(STORAGE_KEY_MAX_LENGTH), default=None)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    natural_key: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("inbox_items.id"), default=None, index=True
    )
    # Who uploaded it. `NULL` for a system actor. The only person an unclassified item is
    # visible to besides an unscoped administrator (design § 1, Visibility).
    uploaded_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), default=None, index=True
    )
    # Set at classification (design § 3.4); `NULL` until then.
    legal_entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("legal_entities.id"), default=None, index=True
    )
    kind: Mapped[str | None] = mapped_column(String(20), default=None)
    state: Mapped[str] = mapped_column(String(10), nullable=False, default="new", index=True)
    duplicate_of_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("inbox_items.id"), default=None, index=True
    )
    # The sentence a person reads: why it is discarded or a duplicate, why its kind is
    # `other`, what a container held. Italian, like every string the user sees.
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    # What the classifier read of the file (design § 3, REB-972). The text of a PDF, for the
    # user's agent to read through MCP (cut at `EXTRACTED_TEXT_MAX` characters); `NULL` for
    # what carries none (an image, an XML). Deferred: a list of items never loads it.
    extracted_text: Mapped[str | None] = deferred(mapped_column(Text, default=None))
    # The fields a certain document states, read once: the invoices of a FatturaPA (issuer,
    # buyer, number, date, type and amounts), shown while the module that files them does not
    # exist (design § 3.5, last paragraph).
    parsed_fields: Mapped[dict[str, Any] | None] = deferred(mapped_column(JSONB, default=None))
    # A paid reading's claim: set when a run takes the item to the model, cleared when it ends,
    # and taken as abandoned after `READ_CLAIM_MINUTES`, so two runs never pay for one file.
    read_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # How many times the model was asked to read it: an automatic run stops at
    # `MAX_READ_ATTEMPTS` and reads the least tried items first, so a file that never reads
    # starves nobody.
    read_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )

    # Where a file that came by mail came from (REB-1016, design § 2.2), all `NULL` for an upload:
    # the mailbox, the message by its UIDVALIDITY and UID (what makes a message read twice one
    # message), and its sender, subject and date. Never the body. `mailbox_id` is cleared if the
    # mailbox row is ever deleted; disconnecting a mailbox leaves the row, and the items.
    mailbox_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("mailboxes.id", ondelete="SET NULL"), default=None
    )
    mail_uidvalidity: Mapped[int | None] = mapped_column(BigInteger, default=None)
    mail_uid: Mapped[int | None] = mapped_column(BigInteger, default=None)
    mail_message_id: Mapped[str | None] = mapped_column(String(998), default=None)
    mail_sender: Mapped[str | None] = mapped_column(String(320), default=None)
    mail_subject: Mapped[str | None] = mapped_column(String(998), default=None)
    mail_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    @property
    def has_file(self) -> bool:
        """Whether the item still holds the bytes (`storage_key`, which a read never shows)."""
        return self.storage_key is not None

    __table_args__ = (
        CheckConstraint(_in("source", ITEM_SOURCES), name="ck_inbox_items_source"),
        CheckConstraint(
            f"file_type IS NULL OR {_in('file_type', FILE_TYPES)}", name="ck_inbox_items_file_type"
        ),
        CheckConstraint(f"kind IS NULL OR {_in('kind', ITEM_KINDS)}", name="ck_inbox_items_kind"),
        CheckConstraint(_in("state", ITEM_STATES), name="ck_inbox_items_state"),
        CheckConstraint("size_bytes >= 0", name="ck_inbox_items_size"),
        # A file that came by mail says which message, and one that did not says nothing of one.
        CheckConstraint(
            "(source = 'mailbox') = (mail_uid IS NOT NULL AND mail_uidvalidity IS NOT NULL)",
            name="ck_inbox_items_mail_message",
        ),
        Index("ix_inbox_items_mail_message", "mailbox_id", "mail_uidvalidity", "mail_uid"),
        # A duplicate names its original and nothing else does.
        CheckConstraint(
            "(state = 'duplicate') = (duplicate_of_id IS NOT NULL)",
            name="ck_inbox_items_duplicate_of",
        ),
    )
