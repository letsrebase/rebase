from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    column,
    desc,
    nullslast,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, SoftDeleteMixin, TimestampMixin


class Attivita(Base, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """One commitment with a state: the table slice 7 specified and slice 10 built.

    Not a `kind` of `activities` and not a generalisation of `payment_reminders`, and
    the spec of 2026-09-03 §2 argues both at length. In short: `activities` is
    append-only, so giving it a state would mean either a second timeline row per
    rewrite or a timeline that is no longer append-only; `payment_reminders` knows only
    about invoices, and its `livello` and its per-invoice ceiling are rules of a
    reminder rather than of a commitment.

    **`scadenza` is nullable on purpose** (spec §3.2). The commonest to-do has no date
    -- «ask Rossi for the codice SDI» is a real commitment that does not fall due on
    Thursday -- and a required date produces one of two bad outcomes: an invented date,
    which poisons every «what is due» list from then on, or the commitment not being
    recorded at all. The consequence has to be respected everywhere: an activity with
    no date is not late, is not for today, and appears in no count ordered by date. It
    is the same lesson `documents.stato_dal` taught in slice 6C -- **a NULL is not zero
    days.**

    **Three states, not two.** `annullata` exists because the thing worth knowing six
    months later is not «this disappeared» but *why*: «chase Rossi» cancelled because
    Rossi paid is information, and the same row deleted is a hole. The soft delete stays
    for the typo -- I created the wrong activity -- and not for the change of plan.

    **At most one reference, and zero is legitimate.** «Do March's e-invoicing» belongs
    to no customer. Two would be an activity that appears on two records and gets closed
    on one of them. This is deliberately *different* from `documents`, where slice 2
    requires exactly one of customer and deal: a document is a fact about somebody, a
    commitment can be about nobody but you.
    """

    __tablename__ = "attivita"
    __table_args__ = (
        # `completata_il` is set when, and only when, the state says so -- the spec's
        # own wording. Written as an equivalence rather than two half-checks so that
        # neither direction can be forgotten: a completed activity with no date and a
        # date on an open one are the same defect seen from two sides.
        CheckConstraint(
            "(stato = 'completata') = (completata_il IS NOT NULL)",
            name="ck_attivita_completata_il",
        ),
        CheckConstraint("stato IN ('aperta', 'completata', 'annullata')", name="ck_attivita_stato"),
        CheckConstraint(
            "origine IN ('manuale', 'automazione', 'sollecito')", name="ck_attivita_origine"
        ),
        # A rule only an automation may carry. Without this, `regola` on a manual
        # activity would read as «an automation made this», which is exactly the
        # question `origine` exists to answer.
        CheckConstraint(
            "regola IS NULL OR origine = 'automazione'", name="ck_attivita_regola_origine"
        ),
        CheckConstraint(
            "(customer_id IS NOT NULL)::int + (person_id IS NOT NULL)::int "
            "+ (deal_id IS NOT NULL)::int + (invoice_id IS NOT NULL)::int <= 1",
            name="ck_attivita_un_solo_riferimento",
        ),
        # The three indexes of spec §3.3, each answering a question the interface really
        # asks. All partial on the soft-delete predicate, like every other index in this
        # project that serves a list: an archived row is not in any of these answers.
        Index(
            "ix_attivita_scadenza",
            "stato",
            "scadenza",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_attivita_entity",
            "customer_id",
            "deal_id",
            "person_id",
            "invoice_id",
            "scadenza",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_attivita_assegnata",
            "assegnata_a",
            "stato",
            "scadenza",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_attivita_custom_fields", "custom_fields", postgresql_using="gin"),
        # Residuo R9: one `(column, id)` B-tree per admitted sort key -- see
        # `customers/models.py` for why each one is needed and why none is partial.
        Index("ix_attivita_created_at_id", "created_at", "id"),
        Index("ix_attivita_scadenza_id", "scadenza", "id"),
        # And the second index a nullable sort column costs, for the descending
        # direction: a backward scan of the ascending index yields NULLS FIRST, so it
        # cannot serve `scadenza` `dir=desc`, and without this Postgres sorts the table.
        # `column("scadenza")` rather than the mapped attribute because `id` comes from
        # `PrimaryKeyMixin` and is not bound in this class body -- the same spelling
        # `people` uses for `cognome`.
        Index(
            "ix_attivita_scadenza_desc_id",
            nullslast(desc(column("scadenza"))),
            desc(column("id")),
        ),
        # The global search's sixth branch (REB-723, migration 0052): `titolo` and `note`
        # by `ILIKE '%...%'`, which only a trigram index serves. Declared here as well as
        # in the migration because `Base.metadata.create_all` builds the test schema.
        Index(
            "ix_attivita_titolo_trgm",
            "titolo",
            postgresql_using="gin",
            postgresql_ops={"titolo": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_attivita_note_trgm",
            "note",
            postgresql_using="gin",
            postgresql_ops={"note": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    titolo: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    # Nullable: see the class docstring.
    scadenza: Mapped[date | None] = mapped_column(Date, default=None)
    stato: Mapped[str] = mapped_column(String(20), nullable=False, default="aperta")
    # A `Date` and not a timestamp, because the question is «did I do it» and not «at
    # what time». Written with `today_local()`, never `date.today()`: slice 6B reduced
    # this project to one clock and an AST scan fails the build on a second one.
    completata_il: Mapped[date | None] = mapped_column(Date, default=None)
    # Null means «whoever opens the list», not «nobody» -- the spec's own words.
    assegnata_a: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    customer_id: Mapped[UUID | None] = mapped_column(ForeignKey("customers.id"), default=None)
    person_id: Mapped[UUID | None] = mapped_column(ForeignKey("people.id"), default=None)
    deal_id: Mapped[UUID | None] = mapped_column(ForeignKey("deals.id"), default=None)
    invoice_id: Mapped[UUID | None] = mapped_column(ForeignKey("invoices.id"), default=None)
    origine: Mapped[str] = mapped_column(String(20), nullable=False, default="manuale")
    regola: Mapped[str | None] = mapped_column(String(50), default=None)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
