from typing import Any

from sqlalchemy import Boolean, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, SoftDeleteMixin, TimestampMixin


class Customer(Base, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """The first full business entity. `People` and `Deals` copy this file's shape.

    Fiscal fields (`partita_iva`, `codice_fiscale`, `codice_sdi`, `pec`) are first-class
    columns rather than custom fields, so slice 3 can build FatturaPA invoicing directly
    on them. `custom_fields` carries everything else a tenant defines through
    `FieldDefinitionService`; the GIN index below is what keeps `list()`'s JSONB
    containment filter (`custom_fields @> {...}`) from ever needing a full table scan.
    """

    __tablename__ = "customers"
    __table_args__ = (
        Index("ix_customers_custom_fields", "custom_fields", postgresql_using="gin"),
        Index("ix_customers_ragione_sociale", "ragione_sociale"),
        # Trigram indexes, one per column `CustomerRepository.list` ORs together. All
        # four are needed, not just the name: a four-way OR is planned as a BitmapOr
        # over four bitmap index scans, and a single unindexed branch collapses the
        # whole thing back into one sequential scan.
        #
        # Partial on `deleted_at IS NULL` because that is the condition every search
        # carries (spec §8.3): the index is smaller and residuo R7 closes for this
        # table. No `lower()` in the expression -- `similarity()` normalises to lower
        # case internally, and wrapping the column would make ILIKE on the raw column
        # unable to use the index (spec §8.2).
        Index(
            "ix_customers_ragione_sociale_trgm",
            "ragione_sociale",
            postgresql_using="gin",
            postgresql_ops={"ragione_sociale": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_customers_partita_iva_trgm",
            "partita_iva",
            postgresql_using="gin",
            postgresql_ops={"partita_iva": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_customers_codice_fiscale_trgm",
            "codice_fiscale",
            postgresql_using="gin",
            postgresql_ops={"codice_fiscale": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_customers_email_trgm",
            "email",
            postgresql_using="gin",
            postgresql_ops={"email": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Residuo R9. One `(column, id)` B-tree per key admitted by `CUSTOMER_SORTS`:
        # the ordering contract is `ORDER BY <col> <dir>, id <dir>`, and the tie-break
        # following the direction is what lets a single ascending index serve `desc` as a
        # backward scan. None of these is nullable, so none costs a second index -- and,
        # for the same reason, `order_by` must not spell `NULLS LAST` on any of them: that
        # spelling matches no index here, which is what made `dir=desc` a sequential scan
        # until `test_sort_plan.py` was written. See `people/models.py` for the one column
        # that does carry a second index.
        #
        # Not partial on `deleted_at IS NULL`, unlike the trigram indexes above: those
        # serve a predicate that always carries that clause, while an ordering has to
        # remain usable for any listing, including one that asks for the deleted rows.
        #
        # `ix_customers_ragione_sociale` (slice 1, single column) stays: it is still the
        # cheaper index for an equality lookup, and dropping an index is a separate
        # decision from adding one.
        Index("ix_customers_created_at_id", "created_at", "id"),
        Index("ix_customers_updated_at_id", "updated_at", "id"),
        Index("ix_customers_ragione_sociale_id", "ragione_sociale", "id"),
    )

    ragione_sociale: Mapped[str] = mapped_column(String(255), nullable=False)
    partita_iva: Mapped[str | None] = mapped_column(String(11), default=None, index=True)
    codice_fiscale: Mapped[str | None] = mapped_column(String(16), default=None)
    codice_sdi: Mapped[str | None] = mapped_column(String(7), default=None)
    pec: Mapped[str | None] = mapped_column(String(320), default=None)
    indirizzo: Mapped[str | None] = mapped_column(String(255), default=None)
    cap: Mapped[str | None] = mapped_column(String(10), default=None)
    comune: Mapped[str | None] = mapped_column(String(120), default=None)
    provincia: Mapped[str | None] = mapped_column(String(2), default=None)
    nazione: Mapped[str] = mapped_column(String(2), nullable=False, default="IT")
    # Indexed for the same reason people.email is: Gmail relevance resolution
    # (gmail/roster.py) looks an address up in both tables on every message it
    # considers, and an unindexed lookup there is a sequential scan per message.
    email: Mapped[str | None] = mapped_column(String(320), default=None, index=True)
    telefono: Mapped[str | None] = mapped_column(String(40), default=None)
    sito_web: Mapped[str | None] = mapped_column(String(255), default=None)
    stato: Mapped[str | None] = mapped_column(String(40), default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    # The payment terms agreed with this customer (REB-326): days after the invoice
    # date, and whether the due date then slides to the end of its month («30 giorni
    # data fattura fine mese»). `giorni_pagamento` null means the fiscal profile's
    # `giorni_scadenza` applies; the switch is never null, since "unknown" and "no" would
    # print the same date. Read by `InvoiceService.issue` and by nothing else: an issued
    # invoice carries the date it was born with, and a term changed later does not move
    # it.
    giorni_pagamento: Mapped[int | None] = mapped_column(Integer, default=None)
    pagamento_fine_mese: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
