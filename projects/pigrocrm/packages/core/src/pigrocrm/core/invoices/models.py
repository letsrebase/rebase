"""The three tables, and the constraints that carry the invariants.

Spec 14.4 asks for immutability "imposed by the database, not by the service", and
that is the organising idea of this file: the legal `(tipo, stato)` pairs, the
agreement between a zero rate and a `Natura`, the impossibility of a numbered row
carrying a `deleted_at`, and the uniqueness of `(anno, numero)` are all table
constraints. An invariant only the service defends is an invariant an importer, a
fix-up script or a psql session walks straight past -- and on a fiscal register that is
not a hypothetical.

`String(n)` + a Pydantic `Literal`, never a Postgres `ENUM`: that is how every closed
set in this schema is already spelled (`pipeline_stages.tipo` is `String(10)`,
`documents.tipo` is `String(20)`), and it means a future value costs a schema constant
rather than an `ALTER TYPE` migration. The closed sets that must also hold across write
paths get a `CHECK` on top, which an `ENUM` would not have made unnecessary anyway --
an `ENUM` cannot express that `stato` depends on `tipo`.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, SoftDeleteMixin, TimestampMixin
from pigrocrm.core.emitter.models import default_azienda_id

PROFORMA_SEQUENCE_NAME = "proforma_riferimento_seq"

# Attached to the metadata, not only created by the migration. The test suite builds its
# schema with `Base.metadata.create_all` (packages/core/tests/conftest.py), so a sequence
# that exists only as raw SQL inside `0005_invoices.py` is absent under test and present
# in production -- which is the worst of the two, because the difference only shows up
# the first time something calls `nextval`. Declaring it here emits it on both paths.
PROFORMA_SEQUENCE = Sequence(PROFORMA_SEQUENCE_NAME, start=1, increment=1, metadata=Base.metadata)


class Invoice(Base, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """A fiscal invoice or a proforma. One table, because lists, timeline and search
    stay one query and `tipo` already carries the difference."""

    __tablename__ = "invoices"

    customer_id: Mapped[UUID] = mapped_column(
        ForeignKey("customers.id"), nullable=False, index=True
    )
    deal_id: Mapped[UUID | None] = mapped_column(ForeignKey("deals.id"), default=None, index=True)
    # The azienda that issues, or issued, this document (REB-619, spec 2026-10-03 §1.4):
    # the register below is keyed by it, and so is the fiscal profile the snapshot is
    # built from. The service names it on every row it writes (the default azienda
    # until the customer chain of milestone 3 decides it); the column default is for a
    # row built without one, see `default_azienda_id`.
    azienda_id: Mapped[UUID] = mapped_column(
        ForeignKey("emitter_profile.id"), nullable=False, index=True, default=default_azienda_id
    )
    # `String(20)`, matching `documents.tipo`, not the 10 that would just fit the two
    # legal values. The point of this file is that the `CHECK` carries the invariant, and
    # at 10 a wrong `tipo` longer than that dies on the column width first: Postgres
    # answers `StringDataRightTruncation` instead of a named constraint violation, which
    # is the raw-exception-reaching-the-caller family this project has already closed six
    # times. Width to spare is what lets the constraint be the thing that refuses.
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    stato: Mapped[str] = mapped_column(String(12), nullable=False)
    # `NULL` until emission. That is what makes "a failed creation cannot burn a
    # number" true by construction rather than by care.
    anno: Mapped[int | None] = mapped_column(Integer, default=None)
    numero: Mapped[int | None] = mapped_column(Integer, default=None)
    riferimento: Mapped[str | None] = mapped_column(String(30), default=None)
    # `Date`, never a timestamp: this is the date printed on the document and the one
    # that decides the fiscal year, not an instant. The previous system's `toISOString()` moved an
    # invoice issued on 31 December at 23:30 CET into the next year.
    data_emissione: Mapped[date | None] = mapped_column(Date, default=None)
    data_scadenza: Mapped[date | None] = mapped_column(Date, default=None)
    # The accrual period: the days the work belongs to, as opposed to the day the
    # document was issued (ORB-61). Header level, because every real case so far has
    # been one month for the whole document; a per-line period would be a second place
    # for the same fact. Both or neither, in order -- the two CHECKs below -- and frozen
    # at issue like `causale`, since the XML writes it on every `DettaglioLinee`.
    competenza_da: Mapped[date | None] = mapped_column(Date, default=None)
    competenza_a: Mapped[date | None] = mapped_column(Date, default=None)
    tipo_documento: Mapped[str] = mapped_column(String(4), nullable=False, default="TD01")
    divisa: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    # Derived by the service and stored. Never recomputed by a client: a total computed
    # in the browser is the structural defect this slice exists to remove.
    imponibile: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    imposta: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    bollo: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    totale: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    causale: Mapped[str | None] = mapped_column(String(200), default=None)
    # Both parties' identity and the fiscal parameters as they were at emission.
    # `NULL` on a draft and on a proforma.
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    # An integer because a snapshot written today is read by code from three years
    # hence, and an unversioned JSON payload is interpreted by guessing.
    snapshot_versione: Mapped[int | None] = mapped_column(Integer, default=None)
    stato_pagamento: Mapped[str] = mapped_column(String(14), nullable=False, default="da_incassare")
    data_incasso: Mapped[date | None] = mapped_column(Date, default=None)
    # Why this column exists even though the slice performs no transmission: the XML
    # is a deliverable the user hands to their own intermediary. Without it the system
    # could not tell an invoice that never left -- annullable -- from one already
    # deposited with the Agenzia delle Entrate, and would treat both the same.
    trasmessa_esternamente_il: Mapped[date | None] = mapped_column(Date, default=None)
    xml_hash_sha256: Mapped[str | None] = mapped_column(String(64), default=None)
    pdf_document_id: Mapped[UUID | None] = mapped_column(ForeignKey("documents.id"), default=None)
    xml_document_id: Mapped[UUID | None] = mapped_column(ForeignKey("documents.id"), default=None)
    # A conversion is a new row pointing at the proforma, not a state change in place:
    # otherwise "immutable after emission" would be a property of something that *was*
    # mutable, verifiable only by reconstructing the history.
    origine_proforma_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("invoices.id"), default=None
    )
    annullata_il: Mapped[date | None] = mapped_column(Date, default=None)
    motivo_annullamento: Mapped[str | None] = mapped_column(String(500), default=None)
    note_interne: Mapped[str | None] = mapped_column(Text, default=None)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # `'esterno'` for a row registered by slice 9's import: issued elsewhere, numbered
    # elsewhere, and therefore without an XML or a PDF this CRM produced. `NULL` is the
    # ordinary case. A string rather than a boolean because the *kind* of provenance is
    # the fact worth keeping: a second migration one day would not be "imported = true"
    # twice. It names no product -- the value reaches the API and the screen, and what a
    # reader needs there is that the document came from outside, not from where.
    importata_da: Mapped[str | None] = mapped_column(String(20), default=None)

    __table_args__ = (
        # Two state machines share one column, so the legal pairs are a table
        # constraint and not a convention of the service: `stato = 'consumata'` on a
        # `fattura` is not storable at all.
        CheckConstraint(
            "(tipo = 'fattura' AND stato IN ('bozza', 'emessa', 'annullata')) "
            "OR (tipo = 'proforma' AND stato IN ('bozza', 'confermata', 'consumata'))",
            name="ck_invoices_tipo_stato",
        ),
        CheckConstraint(
            "(anno IS NULL) = (numero IS NULL)", name="ck_invoices_anno_numero_together"
        ),
        CheckConstraint(
            "numero IS NULL OR (tipo = 'fattura' AND stato <> 'bozza')",
            name="ck_invoices_numero_requires_issued_fattura",
        ),
        CheckConstraint("numero IS NULL OR numero > 0", name="ck_invoices_numero_positive"),
        CheckConstraint(
            "riferimento IS NULL OR tipo = 'proforma'",
            name="ck_invoices_riferimento_only_on_proforma",
        ),
        CheckConstraint(
            "(snapshot IS NULL) = (snapshot_versione IS NULL)",
            name="ck_invoices_snapshot_together",
        ),
        CheckConstraint(
            "(annullata_il IS NULL AND motivo_annullamento IS NULL) "
            "OR (stato = 'annullata' AND annullata_il IS NOT NULL "
            "AND motivo_annullamento IS NOT NULL)",
            name="ck_invoices_annullamento_complete",
        ),
        CheckConstraint(
            "data_incasso IS NULL OR stato_pagamento = 'incassato'",
            name="ck_invoices_incasso_requires_state",
        ),
        # A half period is not a period: the XML writer would have to invent the other
        # end. Two constraints rather than one so the violation names which rule broke.
        CheckConstraint(
            "(competenza_da IS NULL) = (competenza_a IS NULL)",
            name="ck_invoices_competenza_together",
        ),
        CheckConstraint(
            "competenza_da IS NULL OR competenza_da <= competenza_a",
            name="ck_invoices_competenza_ordered",
        ),
        # Spec 4: what never consumed a number is deletable; what consumed one is not,
        # not even by direct SQL -- and neither is a `consumata` proforma, which is the
        # antecedent of an immutable document.
        CheckConstraint(
            "deleted_at IS NULL OR (numero IS NULL AND stato <> 'consumata')",
            name="ck_invoices_no_delete_once_consumed",
        ),
        # The net under the row lock, not the mechanism (spec 3): it turns any future
        # path that bypasses the lock -- a direct INSERT, an importer, a second service
        # -- into an error instead of a duplicate. Partial, because every unnumbered
        # draft would otherwise be a duplicate of every other. Per azienda since
        # REB-619: two aziende of one space each keep a register of their own, so
        # `2026/1` exists once per azienda and never twice for one.
        Index(
            "uq_invoices_azienda_anno_numero",
            "azienda_id",
            "anno",
            "numero",
            unique=True,
            postgresql_where=text("numero IS NOT NULL"),
        ),
        # The lookup path the unique index above no longer offers: a number searched
        # across the space's aziende (`SearchRepository._invoices_by_number`, «2026/7»
        # typed in the palette) filters on `(anno, numero)` with no azienda, and a
        # B-tree led by `azienda_id` cannot serve it. Partial like its sibling, since an
        # unnumbered draft is never what a number search looks for.
        Index(
            "ix_invoices_anno_numero",
            "anno",
            "numero",
            postgresql_where=text("numero IS NOT NULL"),
        ),
        Index("ix_invoices_custom_fields", "custom_fields", postgresql_using="gin"),
        # The tenth trigram index (spec 6 §8.3), and the one that lets the global palette
        # look at invoices at all: `causale` is the only free-text column on this table and
        # `SearchRepository.invoices` matches it with `ILIKE '%…%'`, which no B-tree can
        # serve. Declared here and not only in migration 0024 because
        # `Base.metadata.create_all` is what builds the test schema -- an index that exists
        # only in a migration is invisible to every plan assertion in the suite.
        #
        # Partial on `deleted_at IS NULL` like the other nine: it is the condition every
        # search carries, so the index is smaller and residuo R7 closes for this table too.
        # The other half of the branch -- a fiscal number matched by equality -- needs no
        # index of its own: `ix_invoices_anno_numero` above already serves it.
        Index(
            "ix_invoices_causale_trgm",
            "causale",
            postgresql_using="gin",
            postgresql_ops={"causale": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class InvoiceLine(Base, PrimaryKeyMixin, TimestampMixin):
    """One `DettaglioLinee`.

    The previous system sent one line per invoice -- `NumeroLinea` hardcoded to 1, quantity 1, the
    whole total as the unit price -- so the detail of the work never reached the
    customer.

    `numero_linea` is unique per invoice, starts at 1, and is contiguous. The first two
    are database constraints; **contiguity is a service invariant**, maintained by
    `InvoiceService.replace_lines` renumbering the whole list from 1, because no
    single-row `CHECK` can see the other rows. Saying so plainly is better than
    implying the database guarantees more than it does.
    """

    __tablename__ = "invoice_lines"

    invoice_id: Mapped[UUID] = mapped_column(ForeignKey("invoices.id"), nullable=False, index=True)
    numero_linea: Mapped[int] = mapped_column(Integer, nullable=False)
    # String(1000), mirroring FPR12's own `String1000LatinType`, with the matching
    # Pydantic `max_length` in schemas.py. Not `Text`: a width the schema already
    # imposes is a width worth having on the column, so an over-long value is refused
    # before it reaches an SdI validator.
    descrizione: Mapped[str] = mapped_column(String(1000), nullable=False)
    # Numeric(12, 6): a factor, not an amount. See the class docstring on Invoice.
    quantita: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    unita_misura: Mapped[str | None] = mapped_column(String(10), default=None)
    prezzo_unitario: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    sconto_percentuale: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    sconto_importo: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    prezzo_totale: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    aliquota_iva: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    natura: Mapped[str | None] = mapped_column(String(4), default=None)
    riferimento_normativo: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (
        UniqueConstraint("invoice_id", "numero_linea", name="uq_invoice_lines_invoice_numero"),
        # The two checks the SdI applies as a pair, as one table constraint: a zero
        # rate without a Natura is rejected, and a Natura with a non-zero rate is
        # rejected. The invalid combination is therefore not storable, so it is not
        # exportable either.
        CheckConstraint(
            "(aliquota_iva = 0) = (natura IS NOT NULL)",
            name="ck_invoice_lines_natura_agrees_with_rate",
        ),
        CheckConstraint("numero_linea >= 1", name="ck_invoice_lines_numero_positive"),
    )


class InvoiceCounter(Base):
    """One row per azienda and year, locked with `SELECT ... FOR UPDATE` inside the
    emission transaction (spec 3).

    **Not a `SEQUENCE`**, and the reason is the property a sequence proudly does not
    have: `nextval()` in Postgres is deliberately non-transactional and does not roll
    back, so every aborted transaction would leave a permanent gap -- which is exactly
    what "progressive numbering with no gaps" forbids. A sequence guarantees uniqueness
    and prohibits the one property that is actually required here.

    Keyed by `(azienda_id, anno)` rather than by a UUID: the pair *is* the identity,
    and a surrogate key would need a uniqueness constraint on it anyway. Per azienda
    since REB-619 (spec 2026-10-03 §1.4): the lock is narrower, which is a property
    and not a cost, since issuing for one azienda never waits on the other's lock. No
    `TimestampMixin` either -- this row is a lock target, not a record of anything.
    """

    __tablename__ = "invoice_counters"

    azienda_id: Mapped[UUID] = mapped_column(
        ForeignKey("emitter_profile.id"), primary_key=True, default=default_azienda_id
    )
    anno: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    ultimo_numero: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint("ultimo_numero >= 0", name="ck_invoice_counters_non_negative"),
    )


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

    azienda_id: Mapped[UUID] = mapped_column(
        ForeignKey("emitter_profile.id"), nullable=False, index=True, default=default_azienda_id
    )
    anno: Mapped[int] = mapped_column(Integer, nullable=False)
    numero: Mapped[int] = mapped_column(Integer, nullable=False)
    motivo: Mapped[str] = mapped_column(String(500), nullable=False)
    dichiarato_da: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    dichiarato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        UniqueConstraint(
            "azienda_id", "anno", "numero", name="uq_invoice_register_gaps_azienda_anno_numero"
        ),
        CheckConstraint("numero >= 1", name="ck_invoice_register_gaps_numero_positive"),
    )


__all__ = [
    "PROFORMA_SEQUENCE_NAME",
    "Invoice",
    "InvoiceCounter",
    "InvoiceLine",
    "InvoiceRegisterGap",
]
