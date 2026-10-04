from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, SoftDeleteMixin, TimestampMixin
from pigrocrm.core.emitter.models import default_azienda_id


class Deal(Base, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """The last core CRM entity: a sales opportunity tied to exactly one `Customer`
    and sitting in exactly one `PipelineStage`.

    `customer_id` is **required**, unlike `Person.customer_id`: a deal without a
    customer has no economic meaning, whereas a contact may exist before anyone
    knows which company they work for. `pipeline_stage_id` is also required --
    `DealService.create` fills it in from `PipelineService.default_stage()` when the
    caller does not supply one, so no deal is ever without a stage.

    Both foreign key column names are load-bearing beyond this module:
    `CustomerRepository.count_active_deals` and `PipelineRepository.
    count_deals_in_stage` already query this table by name through `Base.metadata`
    (Tasks 10 and 9, written before this table existed) and raise `RuntimeError` if
    they find it without exactly these column names.

    `ore_preventivate`/`valore_preventivato` are written now but not read until the
    slice 4 estimate-vs-actual report -- costs nothing today, and backfilling a
    column onto a live `deals` table later would be far more disruptive than adding
    it while the table is brand new.

    `custom_fields` copies `Customer`/`Person`'s own JSONB column and GIN index, for
    the same reason: `list()`'s containment filter (`custom_fields @> {...}`) must
    never fall back to a full table scan.
    """

    __tablename__ = "deals"
    __table_args__ = (
        Index("ix_deals_custom_fields", "custom_fields", postgresql_using="gin"),
        # `DealRepository.list` searches `nome` and nothing else -- see
        # `Customer.__table_args__` for why the index is partial on `deleted_at IS NULL`
        # with no `lower()` in the expression.
        Index(
            "ix_deals_nome_trgm",
            "nome",
            postgresql_using="gin",
            postgresql_ops={"nome": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Residuo R9. See `customers/models.py` for why each admitted sort key costs a
        # `(column, id)` B-tree and why none of them is partial.
        Index("ix_deals_created_at_id", "created_at", "id"),
        Index("ix_deals_updated_at_id", "updated_at", "id"),
        Index("ix_deals_nome_id", "nome", "id"),
        # Slice 6 §4.1. Every period figure on the commercial dashboard filters on this
        # column, and `deals` grows without bound. Declared here and not only in the
        # migration: `Base.metadata.create_all` builds the test schema, so an index
        # declared only in a migration is one no test ever sees.
        Index("ix_deals_chiuso_il", "chiuso_il"),
    )

    nome: Mapped[str] = mapped_column(String(255), nullable=False)
    # Required: see the class docstring above.
    customer_id: Mapped[UUID] = mapped_column(
        ForeignKey("customers.id"), nullable=False, index=True
    )
    # The azienda the deal was born under: the customer's at creation (REB-623, §1.7),
    # kept whatever the customer does afterwards. The column default serves a row built
    # without one.
    azienda_id: Mapped[UUID] = mapped_column(
        ForeignKey("emitter_profile.id"), nullable=False, index=True, default=default_azienda_id
    )
    pipeline_stage_id: Mapped[UUID] = mapped_column(
        ForeignKey("pipeline_stages.id"), nullable=False, index=True
    )
    # Numeric, never Float: a binary float cannot represent 1234.56 exactly, and that
    # drift is a bug the moment it reaches an invoice.
    valore_previsto: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    probabilita: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data_chiusura_prevista: Mapped[date | None] = mapped_column(Date, default=None)
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    # Written now, consumed by the slice 4 estimate-vs-actual report. Costs nothing
    # today. ore_preventivate is Numeric(8,2) -- hours, not money -- valore_preventivato
    # is money and shares valore_previsto's Numeric(12,2).
    ore_preventivate: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), default=None)
    valore_preventivato: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    # Numeric(12,6), not (12,2): a rate is a factor, and slice 3 makes
    # `invoice_lines.prezzo_unitario` Numeric(12,6) for the same reason ("3 hours at
    # 33.3333 EUR/h is not expressible at two places"). A rate and a unit price are
    # the same quantity seen from two tables, so a rate at two places would change
    # value the moment these hours became an invoice line, and slice 4B's
    # reconciliation would fail by cents. Level 2 of the resolution order in slice 4
    # §5.1; no report ever reads it, because the resolved value is copied onto the row.
    tariffa_oraria: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # `Date`, not `timestamptz`, against slice 1 §5's general convention and for slice 3
    # §6.2's precise reason: a date that decides which period a figure falls in is not an
    # instant. Written by `DealService.move_stage` when the destination stage has
    # `tipo != 'open'`, and cleared when the deal returns to an open stage.
    #
    # Deliberately NOT backfilled (spec §4.1): `move_stage` records the stage *names* in
    # its activity payload, and a name is renamable (residuo R15), so deducing a
    # historical closure date from the timeline would mean matching a mutable string. The
    # period dashboards exclude `chiuso_il IS NULL` and say how many rows they excluded,
    # rather than counting them as zero or attributing them to the wrong month.
    chiuso_il: Mapped[date | None] = mapped_column(Date, default=None)
