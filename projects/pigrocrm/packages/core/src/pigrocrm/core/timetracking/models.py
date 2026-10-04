from datetime import date, datetime
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
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, SoftDeleteMixin, TimestampMixin


class CostCategory(Base, PrimaryKeyMixin, TimestampMixin):
    """User-configurable, like pipeline stages and field definitions.

    `code` is the stable identity of a seeded category, distinct from `nome`, which
    the user is free to rename -- exactly why `pipeline_stages.code` was added during
    the slice 1 review (residual R11), after `seed_defaults` was found deduplicating
    on `nome`, the one field the user can change. Categories a user creates have no
    `code`; Postgres treats every NULL as distinct under a unique index, so any number
    of them coexist.

    `archiviata` rather than deletable, and no `deleted_at`: this is a taxonomy, not a
    record. A deleted category with costs still hanging off it produces orphan rows
    nobody can see (slice 1 §5.6, first of its three rules), and `templates.attivo` is
    the same shape for the same reason.
    """

    __tablename__ = "cost_categories"

    nome: Mapped[str] = mapped_column(String(60), nullable=False)
    posizione: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    code: Mapped[str | None] = mapped_column(String(30), default=None)
    archiviata: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("uq_cost_categories_code", "code", unique=True),
        # Case-insensitive identity needs a functional index, not a convention: a
        # plain unique=True on a text column is case-sensitive, and normalising in the
        # service protects only the paths that go through it.
        Index("uq_cost_categories_nome", func.lower(nome), unique=True),
    )


class TimeEntry(Base, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """One hour-logging entry, carrying its own rate.

    `deal_id` is **required**. The budget lives on the deal (`ore_preventivate`,
    `valore_preventivato`), the revenue arrives from invoices that carry a `deal_id`,
    and the P&L is per deal. An hour attached only to a customer would have no
    estimate to compare against and would escape every line of this slice; an hour
    attached to nothing is the ghost row the previous system produces and no export shows
    (`filterTimeEntriesForExport` requires a match, while `timeTrackingSummaryRows`
    falls back to `entry.id` as a group key -- so those hours count on screen and
    appear in no document). Internal work not attributable to a client is out of
    scope: keeping it in would mean payroll.

    `user_id` is **required** too. Without it there is no labour cost and half the
    margin is undefined. Not nullable even for "logged through MCP": an `Actor` of
    type `mcp` carries the id of the PAT's owning user (slice 1 §9), so there is
    always a person to attribute it to.

    `data` is a `Date`, not a timestamp -- §6.3. It is the calendar day the work
    belongs to, and that day decides which month, which report and which period it
    lands in. The previous system's `formatIsoDate` used `toISOString()`, so an hour logged at 23:30
    CEST on 31 March was stored as 1 April and went into the wrong monthly export --
    the file attached to an invoice.

    `fatturabile` and `invoice_line_id` are two columns because they answer two
    questions. `fatturabile` is a property of the **work** (an internal alignment
    meeting is never billed, and that is decided when it is logged);
    `invoice_line_id` is a fact about a **document** (whether a line covering it
    exists). A billable, not-yet-billed hour is the normal state of everything done
    this month. Merging them would make the weekly question -- *how much do I have to
    invoice?* -- unanswerable, because "not yet" and "never" would be indistinguishable.

    `invoice_line_id` had **no foreign key** in slice 4A: `invoice_lines` is a slice 3
    table and 4A deliberately did not depend on slice 3. Migration 0012 adds the real
    constraint, `ON DELETE SET NULL` -- which is what lets slice 3 replace a draft's
    lines wholesale (slice 3 §11) without leaving orphans and without slice 4 having to
    join the locked transaction of slice 3 §3, the part of the system that least wants
    new participants.

    `tariffa_origine`/`costo_origine` are `String(10)` plus a Pydantic `Literal`, never
    a Postgres ENUM: every closed set in this schema is spelled that way
    (`pipeline_stages.tipo`, `documents.tipo`), and a new value then costs a constant
    instead of an `ALTER TYPE` migration. `costo_origine` never takes the value
    `deal`: an hour's cost is a property of who works it, not of the client they work
    it for (§5.1).
    """

    __tablename__ = "time_entries"
    __table_args__ = (
        # Not a productivity limit -- a shape check. A value above 24 is almost always
        # the comma slip that writes 80 for 8,0, which would enter the margin as ten
        # thousand euros of work never done. The *daily* total is deliberately
        # unbounded: two 14-hour entries on one day are a likely error but not an
        # impossible one, and refusing the second would refuse a correction in
        # progress.
        CheckConstraint("ore > 0 AND ore <= 24", name="ck_time_entries_ore_range"),
        # §4.3, and the one rule no write path can go around. Deliberately wider than
        # the service rule -- it forbids deleting any entry bound to a line, draft
        # included -- because a constraint that distinguished invoice state would have
        # to read another table, i.e. be a trigger, and this project keeps that kind
        # of invisible logic out of the database. Reaching it costs nothing: to delete
        # an entry still on a draft, take it off the draft first and
        # `invoice_line_id` returns to NULL.
        CheckConstraint(
            "deleted_at IS NULL OR invoice_line_id IS NULL",
            name="ck_time_entries_billed_not_deleted",
        ),
        CheckConstraint(
            "tariffa_origine IN ('manuale', 'deal', 'utente', 'assente')",
            name="ck_time_entries_tariffa_origine",
        ),
        CheckConstraint(
            "costo_origine IN ('manuale', 'utente', 'assente')",
            name="ck_time_entries_costo_origine",
        ),
        # The shape every query in this slice has, partial on the filter every one of
        # them applies. Residual R7 asks for exactly this in general; the two new
        # tables are simply born with it.
        Index(
            "ix_time_entries_deal_data",
            "deal_id",
            "data",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_time_entries_user_data",
            "user_id",
            "data",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_time_entries_custom_fields", "custom_fields", postgresql_using="gin"),
    )

    deal_id: Mapped[UUID] = mapped_column(ForeignKey("deals.id"), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    data: Mapped[date] = mapped_column(Date, nullable=False)
    ore: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    descrizione: Mapped[str] = mapped_column(Text, nullable=False)
    fatturabile: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    tariffa_applicata: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)
    costo_applicato: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)
    tariffa_origine: Mapped[str] = mapped_column(String(10), nullable=False, default="assente")
    costo_origine: Mapped[str] = mapped_column(String(10), nullable=False, default="assente")
    invoice_line_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("invoice_lines.id", ondelete="SET NULL"), default=None, index=True
    )
    note_interne: Mapped[str | None] = mapped_column(Text, default=None)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class TimeTimer(Base, PrimaryKeyMixin, TimestampMixin):
    """The one clock a person may have running: what they are working on right now.

    One row per user, enforced by the unique index, because two simultaneous timers are
    the error Toggl and Clockify both refuse -- a person is doing one thing. It is a live
    session and not a record: stopping it *creates* a `TimeEntry` through the same
    `TimeEntryService.create` every other write goes through (rates frozen, period locks
    checked, activity recorded) and then the row is gone. Discarding it deletes the row
    and writes nothing. So there is no soft delete and no history here -- the history is
    the entry the stop produced.

    `deal_id` is nullable, unlike on `TimeEntry`: Toggl lets a timer start before the
    project is chosen, and a person who starts working on "una chiamata" and picks the
    deal at stop time is not lying. The deal becomes mandatory the moment the timer
    becomes an entry (`TimerService.stop`).

    Slice 4 §13 argued *against* a timer. Ivan asked for one on 2026-09-09, with Toggl
    and Clockify as the reference; the grid stays, this sits beside it.
    """

    __tablename__ = "time_timers"
    __table_args__ = (Index("ux_time_timers_user", "user_id", unique=True),)

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    deal_id: Mapped[UUID | None] = mapped_column(ForeignKey("deals.id"), default=None)
    descrizione: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fatturabile: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Cost(Base, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """Money that actually left, towards somebody else, with a receipt to prove it.

    A cost is not a time entry even though both reduce the margin, and the three
    differences are the ones that matter (§4.2): a cost is real money out with a
    document behind it, while an hour's cost is an internal, notional figure derived
    from a rate you chose yourself; an hour is also *potential revenue* and a cost
    never is; and an hour has a quantity comparable with an estimate
    (`ore_preventivate`) while a cost has only money. Merging them would leave half
    the columns null on every row and turn "hours recorded" -- the central quantity of
    this slice -- into a filtered aggregate over a table where most rows are not hours.

    `deal_id IS NULL` means a general expense: it enters the period P&L in a row of
    its own and is **never apportioned** onto any deal (§7.4). Any apportionment key
    -- on revenue, on hours -- has one precise and unacceptable consequence: a deal's
    margin would move when a *different* deal was invoiced.

    `importo` is the **total paid**, VAT included. Under the flat-rate regime input VAT
    is not deductible, so it is cost in every sense and recording the net would
    understate the cost by 22%. Under an ordinary regime the right choice is the
    opposite one, and the extension path is a second `importo_iva` column read from
    `fiscal_profile.codice_regime` -- named here as a boundary, not designed (§13).

    `document_id` is the receipt, held in slice 2's document store with its pluggable
    storage, versioning and hash. The previous system kept the attachment as base64 inside the costs
    JSON (`parseBase64Payload`); the document store already exists and is not
    reinvented.
    """

    __tablename__ = "costs"
    __table_args__ = (
        # Zero is refused because it is neither a cost nor a correction; negative is
        # allowed because it is a refund or a credit note received (§4.4).
        CheckConstraint("importo <> 0", name="ck_costs_importo_non_zero"),
        Index("ix_costs_deal_data", "deal_id", "data", postgresql_where=text("deleted_at IS NULL")),
        # The period P&L reads costs by date across every deal *and* the general
        # expenses, where `deal_id` is NULL and the two-column index above cannot help.
        Index("ix_costs_data", "data", postgresql_where=text("deleted_at IS NULL")),
        Index("ix_costs_custom_fields", "custom_fields", postgresql_using="gin"),
    )

    deal_id: Mapped[UUID | None] = mapped_column(ForeignKey("deals.id"), default=None, index=True)
    # The deal's azienda when the cost has a deal, `NULL` when it has none: a shared
    # cost («condivisa», REB-623, spec §1.7), which only the view over every azienda
    # shows. No column default: `NULL` is a meaning here, not an omission.
    azienda_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("emitter_profile.id"), default=None, index=True
    )
    category_id: Mapped[UUID] = mapped_column(
        ForeignKey("cost_categories.id"), nullable=False, index=True
    )
    data: Mapped[date] = mapped_column(Date, nullable=False)
    importo: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    descrizione: Mapped[str] = mapped_column(Text, nullable=False)
    fornitore: Mapped[str | None] = mapped_column(String(200), default=None)
    document_id: Mapped[UUID | None] = mapped_column(ForeignKey("documents.id"), default=None)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class PeriodLock(Base):
    """A reported month, closed against further writes.

    No `PrimaryKeyMixin` and no `TimestampMixin`: `(anno, mese)` *is* the identity --
    a month is closed or it is not, and a surrogate id would allow two rows saying so
    -- and `chiuso_il` *is* the timestamp. This is the only table in the schema without
    a UUID key, and it is because the natural key is total.

    Freezing rates (§5) stops a rate change from rewriting a margin somebody has
    already read. This stops the other way of moving the same number: logging today an
    hour dated last March. That is not an abuse, it is the back-dating §6.3 explicitly
    allows, and it is legitimate exactly as long as the period is open.

    Closing is not mandatory: somebody who closes nothing gets the previous behaviour,
    and no screen demands a ritual before it works. And closing does **not** freeze
    invoices, which already have their own rules (slice 3 §4) and do not want a second
    set: this table governs only `time_entries` and `costs`.
    """

    __tablename__ = "period_locks"

    anno: Mapped[int] = mapped_column(Integer, primary_key=True)
    mese: Mapped[int] = mapped_column(Integer, primary_key=True)
    chiuso_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    chiuso_da: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)

    __table_args__ = (CheckConstraint("mese >= 1 AND mese <= 12", name="ck_period_locks_mese"),)
