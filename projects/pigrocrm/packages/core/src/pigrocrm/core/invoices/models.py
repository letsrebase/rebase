"""The three tables, and the constraints that carry the invariants.

Spec 14.4 asks for immutability "imposed by the database, not by the service", and
that is the organising idea of this file: the legal `(tipo, stato)` pairs, the
agreement between a zero rate and a `Natura`, the impossibility of a numbered row
carrying a `deleted_at`, and the uniqueness of `(anno, numero)` are all table
constraints. An invariant only the service defends is an invariant an importer, a
fix-up script or a psql session walks straight past -- and on a fiscal register that is
not a hypothetical.

`String(n)` + a Pydantic `Literal`, never a Postgres `ENUM`: that is how every closed
set in this schema is already spelled (`pipeline_stages.type` is `String(10)`,
`documents.type` is `String(20)`), and it means a future value costs a schema constant
rather than an `ALTER TYPE` migration. The closed sets that must also hold across write
paths get a `CHECK` on top, which an `ENUM` would not have made unnecessary anyway --
an `ENUM` cannot express that `status` depends on `type`.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine.default import DefaultExecutionContext
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, SoftDeleteMixin, TimestampMixin
from pigrocrm.core.legal_entities.models import default_legal_entity_id

PROFORMA_SEQUENCE_NAME = "proforma_reference_seq"

# Attached to the metadata, not only created by the migration. The test suite builds its
# schema with `Base.metadata.create_all` (packages/core/tests/conftest.py), so a sequence
# that exists only as raw SQL inside `0005_invoices.py` is absent under test and present
# in production -- which is the worst of the two, because the difference only shows up
# the first time something calls `nextval`. Declaring it here emits it on both paths.
PROFORMA_SEQUENCE = Sequence(PROFORMA_SEQUENCE_NAME, start=1, increment=1, metadata=Base.metadata)


def _net_receivable_default(context: DefaultExecutionContext) -> Decimal:
    """What a row built without a net receivable is owed: the gross total. A row that carries
    a withholding or a split payment states its net; every other row, the hand-built ones of
    the tests among them, is owed what it printed."""
    total: Decimal = context.get_current_parameters()["total"]  # type: ignore[no-untyped-call]
    return total


class Invoice(Base, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """A fiscal invoice or a proforma. One table, because lists, timeline and search
    stay one query and `type` already carries the difference."""

    __tablename__ = "invoices"

    customer_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    deal_id: Mapped[UUID | None] = mapped_column(ForeignKey("deals.id"), default=None, index=True)
    # The legal entity that issues, or issued, this document (REB-619, spec 2026-10-03 §1.4):
    # the register below is keyed by it, and so is the fiscal profile the snapshot is
    # built from. The service names it on every row it writes (the default legal entity
    # until the customer chain of milestone 3 decides it); the column default is for a
    # row built without one, see `default_legal_entity_id`.
    legal_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_entities.id"),
        nullable=False,
        index=True,
        default=default_legal_entity_id,
    )
    # `String(20)`, matching `documents.type`, not the 10 that would just fit the two
    # legal values. The point of this file is that the `CHECK` carries the invariant, and
    # at 10 a wrong `type` longer than that dies on the column width first: Postgres
    # answers `StringDataRightTruncation` instead of a named constraint violation, which
    # is the raw-exception-reaching-the-caller family this project has already closed six
    # times. Width to spare is what lets the constraint be the thing that refuses.
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    # `NULL` until emission. That is what makes "a failed creation cannot burn a
    # number" true by construction rather than by care.
    year: Mapped[int | None] = mapped_column(Integer, default=None)
    number: Mapped[int | None] = mapped_column(Integer, default=None)
    reference: Mapped[str | None] = mapped_column(String(30), default=None)
    # `Date`, never a timestamp: this is the date printed on the document and the one
    # that decides the fiscal year, not an instant. The previous system's `toISOString()` moved an
    # invoice issued on 31 December at 23:30 CET into the next year.
    issue_date: Mapped[date | None] = mapped_column(Date, default=None, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, default=None, index=True)
    # The accrual period: the days the work belongs to, as opposed to the day the
    # document was issued (ORB-61). Header level, because every real case so far has
    # been one month for the whole document; a per-line period would be a second place
    # for the same fact. Both or neither, in order -- the two CHECKs below -- and frozen
    # at issue like `memo`, since the XML writes it on every `DettaglioLinee`.
    accrual_from: Mapped[date | None] = mapped_column(Date, default=None)
    accrual_to: Mapped[date | None] = mapped_column(Date, default=None)
    # The customer's purchase order this invoice answers (REB-693): one per document,
    # written as `DatiOrdineAcquisto` in the XML and as a line on the PDF. The widths
    # are FPR12's: `IdDocumento` is String20Type, `CodiceCIG` and `CodiceCUP` are
    # String15Type. The date, the CIG and the CUP need the number (the CHECK below),
    # and all four are frozen at issue like `memo`.
    purchase_order_number: Mapped[str | None] = mapped_column(String(20), default=None)
    purchase_order_date: Mapped[date | None] = mapped_column(Date, default=None)
    purchase_order_tender_code: Mapped[str | None] = mapped_column(String(15), default=None)
    purchase_order_project_code: Mapped[str | None] = mapped_column(String(15), default=None)
    sdi_document_type: Mapped[str] = mapped_column(String(4), nullable=False, default="TD01")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    # Derived by the service and stored. Never recomputed by a client: a total computed
    # in the browser is the structural defect this slice exists to remove.
    taxable_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    vat_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    stamp_duty: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    # What the customer pays (REB-994, suppliers design § 2 «Money net of withholding and
    # split payment»): the gross `total` minus the withholding the customer keeps and, on a
    # split payment, the VAT it pays to the tax office itself. Stored when the document is
    # written (a draft, an emission, an import) and read by the due items and the cash
    # figures; `total` stays what the document prints.
    net_receivable: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=_net_receivable_default
    )
    # The withholding the customer keeps (`DatiRitenuta`) with its rate. `0.00` when there is
    # none; the rate is absent on an imported invoice that declares the amount alone.
    withholding_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    withholding_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    # VAT shown on the invoice and paid by the customer to the tax office (art. 17-ter
    # DPR 633/72): `EsigibilitaIVA` `S` on the groups that carry VAT.
    split_payment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    memo: Mapped[str | None] = mapped_column(String(200), default=None)
    # Both parties' identity and the fiscal parameters as they were at emission.
    # `NULL` on a draft and on a proforma.
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    # An integer because a snapshot written today is read by code from three years
    # hence, and an unversioned JSON payload is interpreted by guessing.
    snapshot_version: Mapped[int | None] = mapped_column(Integer, default=None)
    payment_status: Mapped[str] = mapped_column(
        String(14), nullable=False, default="unpaid", index=True
    )
    payment_date: Mapped[date | None] = mapped_column(Date, default=None)
    # Why this column exists even though the slice performs no transmission: the XML
    # is a deliverable the user hands to their own intermediary. Without it the system
    # could not tell an invoice that never left -- annullable -- from one already
    # deposited with the Agenzia delle Entrate, and would treat both the same.
    transmitted_externally_at: Mapped[date | None] = mapped_column(Date, default=None)
    xml_hash_sha256: Mapped[str | None] = mapped_column(String(64), default=None)
    pdf_document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("documents.id"), default=None, index=True
    )
    xml_document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("documents.id"), default=None, index=True
    )
    # A conversion is a new row pointing at the proforma, not a state change in place:
    # otherwise "immutable after emission" would be a property of something that *was*
    # mutable, verifiable only by reconstructing the history.
    origin_proforma_id: Mapped[UUID | None] = mapped_column(ForeignKey("invoices.id"), default=None)
    cancelled_at: Mapped[date | None] = mapped_column(Date, default=None)
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), default=None)
    internal_notes: Mapped[str | None] = mapped_column(Text, default=None)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # `'external'` for a row registered by slice 9's import: issued elsewhere, numbered
    # elsewhere, and therefore without an XML or a PDF this CRM produced. `NULL` is the
    # ordinary case. A string rather than a boolean because the *kind* of provenance is
    # the fact worth keeping: a second migration one day would not be "imported = true"
    # twice. It names no product -- the value reaches the API and the screen, and what a
    # reader needs there is that the document came from outside, not from where.
    imported_from: Mapped[str | None] = mapped_column(String(20), default=None)

    __table_args__ = (
        # Two state machines share one column, so the legal pairs are a table
        # constraint and not a convention of the service: `stato = 'consumata'` on a
        # `invoice` is not storable at all.
        CheckConstraint(
            "(type = 'invoice' AND status IN ('draft', 'issued', 'cancelled')) "
            "OR (type = 'proforma' AND status IN ('draft', 'confirmed', 'consumed'))",
            name="ck_invoices_type_status",
        ),
        CheckConstraint(
            "(year IS NULL) = (number IS NULL)", name="ck_invoices_year_number_together"
        ),
        # The withholding is never negative. A split payment and a withholding are not
        # exclusive here: the law keeps them apart for what this product issues
        # (`invoices/charges.py`), and an invoice another system issued is registered as it is.
        CheckConstraint("withholding_amount >= 0", name="ck_invoices_withholding_not_negative"),
        CheckConstraint(
            "number IS NULL OR (type = 'invoice' AND status <> 'draft')",
            name="ck_invoices_number_requires_issued_invoice",
        ),
        CheckConstraint("number IS NULL OR number > 0", name="ck_invoices_number_positive"),
        CheckConstraint(
            "reference IS NULL OR type = 'proforma'",
            name="ck_invoices_reference_only_on_proforma",
        ),
        CheckConstraint(
            "(snapshot IS NULL) = (snapshot_version IS NULL)",
            name="ck_invoices_snapshot_together",
        ),
        CheckConstraint(
            "(cancelled_at IS NULL AND cancellation_reason IS NULL) "
            "OR (status = 'cancelled' AND cancelled_at IS NOT NULL "
            "AND cancellation_reason IS NOT NULL)",
            name="ck_invoices_cancellation_complete",
        ),
        CheckConstraint(
            "payment_date IS NULL OR payment_status = 'paid'",
            name="ck_invoices_collection_requires_state",
        ),
        # A half period is not a period: the XML writer would have to invent the other
        # end. Two constraints rather than one so the violation names which rule broke.
        CheckConstraint(
            "(accrual_from IS NULL) = (accrual_to IS NULL)",
            name="ck_invoices_accrual_together",
        ),
        CheckConstraint(
            "accrual_from IS NULL OR accrual_from <= accrual_to",
            name="ck_invoices_accrual_ordered",
        ),
        # An order's date, CIG and CUP hang on its number: `IdDocumento` is the one
        # mandatory child of `DatiOrdineAcquisto`, so a CIG alone is a fact with nothing
        # to belong to (REB-693).
        CheckConstraint(
            "purchase_order_number IS NOT NULL "
            "OR (purchase_order_date IS NULL AND purchase_order_tender_code IS NULL "
            "AND purchase_order_project_code IS NULL)",
            name="ck_invoices_order_needs_number",
        ),
        # Spec 4: what never consumed a number is deletable; what consumed one is not,
        # not even by direct SQL -- and neither is a `consumed` proforma, which is the
        # antecedent of an immutable document.
        CheckConstraint(
            "deleted_at IS NULL OR (number IS NULL AND status <> 'consumed')",
            name="ck_invoices_no_delete_once_consumed",
        ),
        # The net under the row lock, not the mechanism (spec 3): it turns any future
        # path that bypasses the lock -- a direct INSERT, an importer, a second service
        # -- into an error instead of a duplicate. Partial, because every unnumbered
        # draft would otherwise be a duplicate of every other. Per legal entity since
        # REB-619: two legal entities of one space each keep a register of their own, so
        # `2026/1` exists once per legal entity and never twice for one.
        Index(
            "uq_invoices_legal_entity_year_number",
            "legal_entity_id",
            "year",
            "number",
            unique=True,
            postgresql_where=text("number IS NOT NULL"),
        ),
        # The lookup path the unique index above no longer offers: a number searched
        # across the space's legal entities (`SearchRepository._invoices_by_number`, «2026/7»
        # typed in the palette) filters on `(anno, numero)` with no legal entity, and a
        # B-tree led by `legal_entity_id` cannot serve it. Partial like its sibling, since an
        # unnumbered draft is never what a number search looks for.
        Index(
            "ix_invoices_year_number",
            "year",
            "number",
            postgresql_where=text("number IS NOT NULL"),
        ),
        Index("ix_invoices_custom_fields", "custom_fields", postgresql_using="gin"),
        # The tenth trigram index (spec 6 §8.3), and the one that lets the global palette
        # look at invoices at all: `memo` is the only free-text column on this table and
        # `SearchRepository.invoices` matches it with `ILIKE '%…%'`, which no B-tree can
        # serve. Declared here and not only in migration 0024 because
        # `Base.metadata.create_all` is what builds the test schema -- an index that exists
        # only in a migration is invisible to every plan assertion in the suite.
        #
        # Partial on `deleted_at IS NULL` like the other nine: it is the condition every
        # search carries, so the index is smaller and residuo R7 closes for this table too.
        # The other half of the branch -- a fiscal number matched by equality -- needs no
        # index of its own: `ix_invoices_year_number` above already serves it.
        Index(
            "ix_invoices_memo_trgm",
            "memo",
            postgresql_using="gin",
            postgresql_ops={"memo": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class InvoiceLine(Base, PrimaryKeyMixin, TimestampMixin):
    """One `DettaglioLinee`.

    The previous system sent one line per invoice -- `NumeroLinea` hardcoded to 1, quantity 1, the
    whole total as the unit price -- so the detail of the work never reached the
    customer.

    `line_number` is unique per invoice, starts at 1, and is contiguous. The first two
    are database constraints; **contiguity is a service invariant**, maintained by
    `InvoiceService.replace_lines` renumbering the whole list from 1, because no
    single-row `CHECK` can see the other rows. Saying so plainly is better than
    implying the database guarantees more than it does.
    """

    __tablename__ = "invoice_lines"

    invoice_id: Mapped[UUID] = mapped_column(ForeignKey("invoices.id"), nullable=False, index=True)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # String(1000), mirroring FPR12's own `String1000LatinType`, with the matching
    # Pydantic `max_length` in schemas.py. Not `Text`: a width the schema already
    # imposes is a width worth having on the column, so an over-long value is refused
    # before it reaches an SdI validator.
    description: Mapped[str] = mapped_column(String(1000), nullable=False)
    # Numeric(12, 6): a factor, not an amount. See the class docstring on Invoice.
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    unit_of_measure: Mapped[str | None] = mapped_column(String(10), default=None)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    discount_percentage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    total_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    vat_nature: Mapped[str | None] = mapped_column(String(4), default=None)
    legal_reference: Mapped[str | None] = mapped_column(Text, default=None)
    # The contract rate card this line bills and the day its period starts (REB-997): what lets
    # issuing drop the forecast of that period instead of adding to it. Both or neither; a line
    # that bills no contract carries neither. No `ondelete`: a card that was billed is not
    # deleted.
    rate_card_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("rate_cards.id"), default=None, index=True
    )
    period_start: Mapped[date | None] = mapped_column(Date, default=None)

    __table_args__ = (
        UniqueConstraint("invoice_id", "line_number", name="uq_invoice_lines_invoice_number"),
        # The two checks the SdI applies as a pair, as one table constraint: a zero
        # rate without a VAT nature is rejected, and a VAT nature with a non-zero rate is
        # rejected. The invalid combination is therefore not storable, so it is not
        # exportable either.
        CheckConstraint(
            "(vat_rate = 0) = (vat_nature IS NOT NULL)",
            name="ck_invoice_lines_vat_nature_agrees_with_rate",
        ),
        CheckConstraint("line_number >= 1", name="ck_invoice_lines_number_positive"),
        # A line either bills a contract's rate card for a period or bills neither.
        CheckConstraint(
            "(rate_card_id IS NULL) = (period_start IS NULL)",
            name="ck_invoice_lines_rate_card_with_period",
        ),
    )


class InvoiceCounter(Base):
    """One row per legal entity and year, locked with `SELECT ... FOR UPDATE` inside the
    emission transaction (spec 3).

    **Not a `SEQUENCE`**, and the reason is the property a sequence proudly does not
    have: `nextval()` in Postgres is deliberately non-transactional and does not roll
    back, so every aborted transaction would leave a permanent gap -- which is exactly
    what "progressive numbering with no gaps" forbids. A sequence guarantees uniqueness
    and prohibits the one property that is actually required here.

    Keyed by `(azienda_id, anno)` rather than by a UUID: the pair *is* the identity,
    and a surrogate key would need a uniqueness constraint on it anyway. Per legal entity
    since REB-619 (spec 2026-10-03 §1.4): the lock is narrower, which is a property
    and not a cost, since issuing for one legal entity never waits on the other's lock. No
    `TimestampMixin` either -- this row is a lock target, not a record of anything.
    """

    __tablename__ = "invoice_counters"

    legal_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_entities.id"), primary_key=True, default=default_legal_entity_id
    )
    year: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (CheckConstraint("last_number >= 0", name="ck_invoice_counters_non_negative"),)


class InvoiceRegisterGap(Base, PrimaryKeyMixin):
    """A number the register does not carry, on purpose, with the reason why.

    The register has to be gap-free (spec 3), and an import from another system meets
    numbers that were consumed there and never became an invoice -- annulled before
    transmission, a test run, a numbering slip. Refusing the import would lock the
    history out; inventing rows would forge documents. So the gap is *declared*: one
    row, one number, one reason, one author. `test_invoice_import.py` proves the import
    refuses an undeclared gap.
    """

    __tablename__ = "invoice_register_gaps"

    legal_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_entities.id"),
        nullable=False,
        index=True,
        default=default_legal_entity_id,
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    declared_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    declared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        UniqueConstraint(
            "legal_entity_id",
            "year",
            "number",
            name="uq_invoice_register_gaps_legal_entity_year_number",
        ),
        CheckConstraint("number >= 1", name="ck_invoice_register_gaps_number_positive"),
    )


__all__ = [
    "PROFORMA_SEQUENCE_NAME",
    "Invoice",
    "InvoiceCounter",
    "InvoiceLine",
    "InvoiceRegisterGap",
]
