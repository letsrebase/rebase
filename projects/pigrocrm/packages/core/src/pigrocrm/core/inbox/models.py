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

from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin
from pigrocrm.core.inbox.filetypes import FILE_TYPES

ITEM_SOURCES: tuple[str, ...] = ("upload",)
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
ITEM_STATES: tuple[str, ...] = ("new", "proposed", "filed", "duplicate", "discarded")

FILE_NAME_MAX_LENGTH = 255
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
        # A duplicate names its original and nothing else does.
        CheckConstraint(
            "(state = 'duplicate') = (duplicate_of_id IS NOT NULL)",
            name="ck_inbox_items_duplicate_of",
        ),
    )
