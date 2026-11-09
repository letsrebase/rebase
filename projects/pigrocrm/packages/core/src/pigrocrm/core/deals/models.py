from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, SoftDeleteMixin, TimestampMixin
from pigrocrm.core.legal_entities.models import default_legal_entity_id


class Deal(Base, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """The last core CRM entity: a sales opportunity tied to exactly one `Organization`
    and sitting in exactly one `PipelineStage`.

    `customer_id` is **required**, unlike `Person.customer_id`: a deal without a
    customer has no economic meaning, whereas a contact may exist before anyone
    knows which company they work for. `pipeline_stage_id` is also required --
    `DealService.create` fills it in from `PipelineService.default_stage()` when the
    caller does not supply one, so no deal is ever without a stage.

    Both foreign key column names are load-bearing beyond this module:
    `OrganizationRepository.count_active_deals` and `PipelineRepository.count_deals_in_stage`
    name them as columns of a bare `deals` table, which a rename would break at run time.

    `budgeted_hours`/`budgeted_value` are written now but not read until the
    slice 4 estimate-vs-actual report -- costs nothing today, and backfilling a
    column onto a live `deals` table later would be far more disruptive than adding
    it while the table is brand new.

    `custom_fields` copies `Organization`/`Person`'s own JSONB column and GIN index, for
    the same reason: `list()`'s containment filter (`custom_fields @> {...}`) must
    never fall back to a full table scan.
    """

    __tablename__ = "deals"
    __table_args__ = (
        Index("ix_deals_custom_fields", "custom_fields", postgresql_using="gin"),
        # `DealRepository.list` searches `name` and nothing else -- see
        # `Organization.__table_args__` for why the index is partial on `deleted_at IS NULL`
        # with no `lower()` in the expression.
        Index(
            "ix_deals_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Residuo R9. See `organizations/models.py` for why each admitted sort key costs a
        # `(column, id)` B-tree and why none of them is partial.
        Index("ix_deals_created_at_id", "created_at", "id"),
        Index("ix_deals_updated_at_id", "updated_at", "id"),
        Index("ix_deals_name_id", "name", "id"),
        # Slice 6 §4.1. Every period figure on the commercial dashboard filters on this
        # column, and `deals` grows without bound. Declared here and not only in the
        # migration: `Base.metadata.create_all` builds the test schema, so an index
        # declared only in a migration is one no test ever sees.
        Index("ix_deals_closed_at", "closed_at"),
        # `find_by_rebase_ref`'s containment lookup, run on every retry of the leads route
        # and of the letters door.
        Index("ix_deals_rebase_refs", "rebase_refs", postgresql_using="gin"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Required: see the class docstring above.
    customer_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    # The legal entity the deal was born under: the customer's at creation (REB-623, §1.7),
    # kept whatever the customer does afterwards. The column default serves a row built
    # without one.
    legal_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_entities.id"),
        nullable=False,
        index=True,
        default=default_legal_entity_id,
    )
    pipeline_stage_id: Mapped[UUID] = mapped_column(
        ForeignKey("pipeline_stages.id"), nullable=False, index=True
    )
    # Numeric, never Float: a binary float cannot represent 1234.56 exactly, and that
    # drift is a bug the moment it reaches an invoice.
    expected_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    probability: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expected_close_date: Mapped[date | None] = mapped_column(Date, default=None)
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None, index=True)
    # The references a request or a match of rebase's engagements door left on this deal
    # (`rebase:request=<uuid>`, `rebase:match=<uuid>`): the keys a retry finds the deal
    # again by (`DealRepository.find_by_rebase_ref`). They were the last line of the free
    # `note` text, where an edit could remove them; a deal linked to two requests carries
    # both, as it did then. Written by the two doors only, never by a Create or Update.
    rebase_refs: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'::text[]")
    )
    # Written now, consumed by the slice 4 estimate-vs-actual report. Costs nothing
    # today. ore_preventivate is Numeric(8,2) -- hours, not money -- valore_preventivato
    # is money and shares valore_previsto's Numeric(12,2).
    budgeted_hours: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), default=None)
    budgeted_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    # Numeric(12,6), not (12,2): a rate is a factor, and slice 3 makes
    # `invoice_lines.unit_price` Numeric(12,6) for the same reason ("3 hours at
    # 33.3333 EUR/h is not expressible at two places"). A rate and a unit price are
    # the same quantity seen from two tables, so a rate at two places would change
    # value the moment these hours became an invoice line, and slice 4B's
    # reconciliation would fail by cents. Level 2 of the resolution order in slice 4
    # §5.1; no report ever reads it, because the resolved value is copied onto the row.
    hourly_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)
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
    closed_at: Mapped[date | None] = mapped_column(Date, default=None)


VALUE_LINE_UNITS: tuple[str, ...] = ("flat", "day", "hour")
VALUE_LINE_PERIODS: tuple[str, ...] = ("one_off", "monthly", "annual")


class DealValueLine(Base, PrimaryKeyMixin):
    """One priced line of a deal: a lump sum, a fee or a day or hour rate, paid once or
    every month or year. A deal's value is what its lines add up to
    (`pigrocrm.core.deals.value`), and `DealService` writes that total into
    `deals.expected_value` in the same transaction, so no caller sets it.

    No `SoftDeleteMixin`: an owned child row of its deal, the shape of `RateCard` under
    `Contract`, replaced as a whole list on every write.

    `start` null follows the deal's `expected_close_date`. `duration_months` null on a
    recurring line is open-ended (counted at 12 months); on a one-off day or hour line it is
    an open window; on a flat one-off line it is always null.
    """

    __tablename__ = "deal_value_lines"
    __table_args__ = (
        UniqueConstraint("deal_id", "position", name="uq_deal_value_lines_deal_id_position"),
        CheckConstraint(f"unit IN {VALUE_LINE_UNITS!r}", name="ck_deal_value_lines_unit"),
        CheckConstraint(
            f"delivery_period IN {VALUE_LINE_PERIODS!r}",
            name="ck_deal_value_lines_delivery_period",
        ),
        CheckConstraint(
            "unit <> 'flat' OR quantity = 1", name="ck_deal_value_lines_flat_quantity_one"
        ),
        CheckConstraint(
            "NOT (unit = 'flat' AND delivery_period = 'one_off') OR duration_months IS NULL",
            name="ck_deal_value_lines_flat_one_off_no_duration",
        ),
        CheckConstraint(
            "duration_months IS NULL OR duration_months >= 1",
            name="ck_deal_value_lines_duration_positive",
        ),
        CheckConstraint("unit_price >= 0", name="ck_deal_value_lines_price_not_negative"),
        CheckConstraint("quantity > 0", name="ck_deal_value_lines_quantity_positive"),
    )

    deal_id: Mapped[UUID] = mapped_column(ForeignKey("deals.id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), default=None)
    unit: Mapped[str] = mapped_column(String(10), nullable=False)
    delivery_period: Mapped[str] = mapped_column(String(10), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    start: Mapped[date | None] = mapped_column(Date, default=None)
    duration_months: Mapped[int | None] = mapped_column(Integer, default=None)
