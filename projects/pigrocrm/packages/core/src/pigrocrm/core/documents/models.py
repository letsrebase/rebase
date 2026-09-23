from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, SoftDeleteMixin, TimestampMixin


class Document(Base, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """A document belongs to a customer, a deal, or a contract -- exactly one, never
    more than one, never none (REB-358 widened this from two owners to three: once
    `contracts` exists, a contract's own signed document, and any later addendum,
    should be discoverable from the contract itself, not only from the proposal row
    that produced it).

    `tipo` and `stato` are `String` + a Pydantic `Literal`, not a Postgres `ENUM`:
    that is how every closed set in this schema is already spelled (`pipeline_stages.
    tipo` is `String(10)`), and it means a future value costs a schema constant rather
    than an `ALTER TYPE` migration. The one thing that *is* a database constraint is
    the exclusivity rule below, which the spec names explicitly and which no
    application-level check can guarantee under concurrency.

    `stato` is only meaningful for `tipo = 'offerta'`; it is `NULL` for every other
    type. `DocumentService.set_offer_state` is the only writer.
    """

    __tablename__ = "documents"

    customer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("customers.id"), default=None, index=True
    )
    deal_id: Mapped[UUID | None] = mapped_column(ForeignKey("deals.id"), default=None, index=True)
    # REB-358 §11: a contract's own signed document (and any later addendum). Widens
    # the ownership from two mutually-exclusive columns to three, the identical
    # situation mastro already solved once for its own single-discriminator
    # `document.ownerType` (`0011_approval_constraints.sql:50-57`).
    contract_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contracts.id"), default=None, index=True
    )
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    titolo: Mapped[str] = mapped_column(String(200), nullable=False)
    stato: Mapped[str | None] = mapped_column(String(20), default=None)
    versione_corrente: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The day the current `stato` was set. Written by `DocumentService.set_offer_state`,
    # the only writer of `documents.stato`. `Date` for the same reason as
    # `deals.chiuso_il`. Backfilled in migration 0023 -- unlike `chiuso_il` -- because the
    # offer timeline's payload is `{"da": "inviata", "a": "accettata"}`, literals of
    # `OfferState` rather than user-editable text.
    #
    # `NULL` on an offer that has never left `bozza`, because a draft has no state change
    # to date and "ferma da N giorni" is not a question anyone asks about a draft.
    stato_dal: Mapped[date | None] = mapped_column(Date, default=None)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint(
            # Postgres's own `num_nonnulls` -- built in since 9.5 -- is the "exactly
            # one of three" test in one call, the natural three-way widening of the
            # original two-way `(customer_id IS NOT NULL) <> (deal_id IS NOT NULL)`.
            # Same constraint name: this is a widening of the existing rule, not a
            # second one beside it.
            "num_nonnulls(customer_id, deal_id, contract_id) = 1",
            name="ck_documents_customer_xor_deal",
        ),
        Index("ix_documents_custom_fields", "custom_fields", postgresql_using="gin"),
        # The global search matches a document by its title -- see
        # `Customer.__table_args__` for why the index is partial on `deleted_at IS NULL`
        # with no `lower()` in the expression.
        Index(
            "ix_documents_titolo_trgm",
            "titolo",
            postgresql_using="gin",
            postgresql_ops={"titolo": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Residuo R9. See `customers/models.py` for why each admitted sort key costs a
        # `(column, id)` B-tree and why none of them is partial.
        Index("ix_documents_created_at_id", "created_at", "id"),
        Index("ix_documents_updated_at_id", "updated_at", "id"),
        Index("ix_documents_titolo_id", "titolo", "id"),
        # Slice 6 §4.1. See `Deal.__table_args__` for why the index is declared on the
        # model rather than only in the migration.
        Index("ix_documents_stato_dal", "stato_dal"),
    )


class DocumentVersion(Base, PrimaryKeyMixin, TimestampMixin):
    """Every change makes a version; nothing is ever overwritten (spec 4.2).

    Keeping `template_id` **and** `variabili` together is what makes a version
    reproducible: the PDF of a six-month-old offer can be regenerated without the
    person who wrote it being in the room (spec 11 criterion 4).

    `creato_da` is nullable and is *not* validated as a foreign key on input, unlike
    every other FK in this slice. It is never supplied by a caller -- it is read from
    the already-authenticated `Actor`, whose `id` is `None` for a `system` actor
    (actor.py). That is the documented exception to the FK-validation rule: no schema
    in this slice accepts `creato_da` from a request body, so there is nothing to
    validate as a foreign key on input in the first place, and the column stays
    nullable to admit a system actor's `None` without a sentinel value.
    """

    __tablename__ = "document_versions"

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id"), nullable=False, index=True
    )
    numero: Mapped[int] = mapped_column(Integer, nullable=False)
    sorgente_markdown: Mapped[str | None] = mapped_column(Text, default=None)
    template_id: Mapped[UUID | None] = mapped_column(ForeignKey("templates.id"), default=None)
    variabili: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    dimensione: Mapped[int] = mapped_column(Integer, nullable=False)
    # Deduplication and integrity check. 64 hex characters, exactly.
    hash_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    creato_da: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)

    __table_args__ = (
        UniqueConstraint("document_id", "numero", name="uq_document_versions_document_numero"),
    )
