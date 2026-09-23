"""`work_units`, `work_unit_transitions` and `approvals` -- the day lifecycle, per the
signed-off mapping in
`docs/superpowers/specs/2026-09-23-mastro-ledger-onto-pigrocrm-design.md` §§5-6, 12.

`TimeEntry` (`timetracking/models.py`) is the closest existing entity and is still the
wrong shape: no state machine, no `approval_id`, priced per entry rather than resolved
against a contract's own rate card. A day's state machine cannot be a plain `CHECK`
either, because a transition's legality depends on the owning contract's own
`requires_prior_approval` -- a `CHECK` cannot subquery another table. That is why
`work_unit_enforce_state_machine` (`triggers.py`) is a `BEFORE INSERT OR UPDATE`
trigger and not a constraint: this is genuinely new infrastructure for this codebase
(§12's own check -- zero `CREATE TRIGGER` statements exist in any prior migration).

`approvals` is declared in this module rather than a package of its own because its
only consumer today is `work_units.approval_id`; nothing else in this schema
references it. A future issue that needs `approvals` from elsewhere (contract-level
evidence, a `proposals` accept path) can move it without this issue re-litigating the
lifecycle it was built to serve.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin

# Italian values, translating mastro's ten states (`work-unit.ts:39-50`) one for one,
# `String(40)` + a `CHECK` -- never a Postgres `ENUM` -- matching every other closed
# set already on this schema (`invoices.stato`, `documents.tipo`). Wider than the
# longest legal value (`lavorato_senza_approvazione`, 27 characters) on purpose: a
# column sized exactly to its legal set answers a wrong value with Postgres's own
# raw `StringDataRightTruncation`, not the `ck_work_units_stato` violation beside it
# (`automations/schemas.py`'s own comment on the identical defect, already fixed
# once on `invoices.tipo`).
WORK_UNIT_STATI: tuple[str, ...] = (
    "proposto",
    "approvato",
    "lavorato",
    "lavorato_senza_approvazione",
    "fatturato",
    "pagato",
    "contestato",
    "revocato",
    "rifiutato",
    "non_fatturabile",
)

# The three states an INSERT may name directly (§12's own citation of
# `0012_work_unit_state_machine.sql`: "rejecting an INSERT starting anywhere but the
# three legal entry states"). `lavorato_senza_approvazione` is reachable only through
# the redirect below -- a caller can never name it on creation.
WORK_UNIT_ENTRY_STATI: frozenset[str] = frozenset({"proposto", "approvato", "lavorato"})

# REB-372: the states `AnalyticsRepository.unbilled_backlog` treats as "approved to
# bill", once work_units exist to make the distinction at all -- mastro's own three
# qualifying states (`certainty.ts:116-120`: "approved/worked/disputed"), translated,
# and nothing wider.
#
# `fatturato`/`pagato` are deliberately NOT in this set, even though they sit later in
# the graph: `bind_work_units` (`invoicing.py`) sets only `invoice_line_id`, never
# `stato`, so the normal invoicing path never actually produces `fatturato` on its
# own -- and `WorkUnitService.transition` is a generic, already-public method with no
# coupling to `invoice_line_id` at all, so a work_unit can reach `fatturato`/`pagato`
# (both legal edges from `lavorato`/`contestato`/`fatturato`) while `invoice_line_id`
# is still NULL. Relying on the backlog query's own `invoice_line_id IS NULL` filter
# to keep such a row out would be relying on an invariant this codebase does not
# enforce; the state itself is what says "already billed" or "already paid", and
# that is reason enough on its own to leave the backlog, independent of the link.
#
# Also deliberately excluded: `proposto` (merely logged, the one distinction this
# issue exists to draw), `lavorato_senza_approvazione` (worked *without* the approval
# its own contract requires -- the flagged, uncertain branch REB-359's invoicing step
# already refuses to gather automatically, `work_units/repository.py`'s own
# `unbilled_for_contract` default), `rifiutato`/`revocato` (never happened, or no
# longer counts), and `non_fatturabile` (recorded, real, but declared unbillable --
# the opposite of committed revenue).
WORK_UNIT_COMMITTED_STATI: frozenset[str] = frozenset({"approvato", "lavorato", "contestato"})

# The state graph `work_unit_enforce_state_machine` enforces (`triggers.py` renders
# this into the migration's own `op.execute(...)` text and into
# `tests/conftest.py`'s second bootstrap block, so the documented graph and the
# enforced one cannot drift apart -- the same "declared once, read twice" shape
# `PROFORMA_SEQUENCE` already uses in `invoices/models.py`).
#
# Mastro's own exhaustive table (`worked-without-approval.test.ts`) is cited by the
# spec but not reproduced in it; this is the coherent graph implied by the state
# names and by the spec's own description of each one (§5, §12), reasoned out here
# because the implementing issue is explicitly told the mastro repository may be
# unreachable:
#
# - A day starts proposed, pre-approved, or already worked (the three entry states).
# - `proposto` moves to approval, straight to work (no prior approval required by
#   the contract), or off the ledger (rejected by the client, or withdrawn).
# - `approvato` moves to work, or is withdrawn before the work happens.
# - `lavorato` and `lavorato_senza_approvazione` share their forward edges: the work
#   already happened either way, so both can be billed, disputed, written off as
#   unbillable, or withdrawn. The flagged state's one extra edge, back to plain
#   `lavorato`, is the recovery -- also reachable automatically, with no explicit
#   caller transition, the instant `approval_id` is linked (`triggers.py`).
# - The trigger checks this graph against what the caller *requested*, before the
#   redirect ever rewrites anything (`triggers.py`'s own `v_requested`) -- so
#   `proposto`/`approvato` need no separate edge into `lavorato_senza_approvazione`:
#   a caller only ever asks for `'lavorato'`, and the redirect turning that into the
#   flagged state afterward is a rewrite of an already-legal write, not a second
#   edge of its own. It also closes the loophole a post-redirect check would leave
#   open: without this ordering, any state able to reach `'lavorato_senza_approvazione'`
#   at all (say, from a state that was never allowed to reach `'lavorato'`) could
#   sneak there by asking for the unapproved write.
# - `fatturato` is paid, or disputed before payment lands.
# - `contestato` resolves back to `lavorato`, is written off, or is withdrawn.
# - `pagato`, `rifiutato`, `revocato` and `non_fatturabile` are terminal: once a day
#   is paid, refused, withdrawn or written off, nothing here moves it again.
WORK_UNIT_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposto": frozenset({"approvato", "lavorato", "rifiutato", "revocato"}),
    "approvato": frozenset({"lavorato", "revocato"}),
    "lavorato": frozenset({"fatturato", "contestato", "non_fatturabile", "revocato"}),
    "lavorato_senza_approvazione": frozenset(
        {"lavorato", "fatturato", "contestato", "non_fatturabile", "revocato"}
    ),
    "fatturato": frozenset({"pagato", "contestato"}),
    "contestato": frozenset({"lavorato", "revocato", "non_fatturabile"}),
    "pagato": frozenset(),
    "rifiutato": frozenset(),
    "revocato": frozenset(),
    "non_fatturabile": frozenset(),
}

# mastro's `noticeChannel` (`client.ts:11-17`), reused verbatim for an approval's own
# evidence channel (spec §6).
APPROVAL_CANALI: tuple[str, ...] = (
    "email",
    "posta_certificata",
    "raccomandata",
    "corriere",
    "altro",
)


class WorkUnit(Base, PrimaryKeyMixin, TimestampMixin):
    """One day (or fraction of a day, or hourly figure) of a contract's ledger --
    mastro's `work_unit` (`work-unit.ts`), spec §5.

    No `SoftDeleteMixin`: nothing here is ever hard-removed. `revocato` is the
    withdrawal state -- a day that should no longer count is moved there, not
    deleted, which is also what frees its `(contract_id, data)` slot for a fresh
    proposal (the partial unique index below).

    `quantita` is not validated against the rate card's own unit here -- "which one
    applies is resolved against the contract's rate card at pricing time, not
    validated here" (`work-unit.ts:54-56`, spec §5), i.e. by `invoicing.py`.
    """

    __tablename__ = "work_units"

    contract_id: Mapped[UUID] = mapped_column(
        ForeignKey("contracts.id"), nullable=False, index=True
    )
    data: Mapped[date] = mapped_column(Date, nullable=False)
    quantita: Mapped[Any] = mapped_column(Numeric(6, 2), nullable=False)
    descrizione: Mapped[str] = mapped_column(Text, nullable=False)
    stato: Mapped[str] = mapped_column(
        String(40), nullable=False, default="proposto", server_default=text("'proposto'")
    )
    # No explicit `ondelete`: RESTRICT is the schema-wide default, and mastro's own
    # reasoning applies unchanged -- a day already relying on an approval cannot have
    # it pulled out from under it (`work-unit.ts:59-61`).
    approval_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("approvals.id"), default=None, index=True
    )
    # `SET NULL`, diverging from mastro's own `RESTRICT` on purpose -- see the class
    # docstring in the design spec §5: `TimeEntry.invoice_line_id` already uses `SET
    # NULL` for the identical relationship so `InvoiceService.replace_lines` can
    # rebuild a draft's line list wholesale without leaving orphans, and a
    # `work_unit` follows its sibling's own proven mechanism.
    invoice_line_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("invoice_lines.id", ondelete="SET NULL"), default=None, index=True
    )
    note: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (
        CheckConstraint(f"stato IN {WORK_UNIT_STATI!r}", name="ck_work_units_stato"),
        # At most one *live* day per contract per date -- mirroring
        # `0012_work_unit_state_machine.sql:40-42`. `rifiutato`/`revocato` are
        # excluded because those are exactly the two outcomes that leave a date free
        # for a fresh proposal; `non_fatturabile` stays counted because it is still
        # real, recorded work, just not billable.
        Index(
            "uq_work_units_contract_data_live",
            "contract_id",
            "data",
            unique=True,
            postgresql_where=text("stato NOT IN ('rifiutato', 'revocato')"),
        ),
    )


class WorkUnitTransition(Base, PrimaryKeyMixin):
    """One row per real state change of a `WorkUnit`, written only by
    `work_unit_log_transition` -- append-only, immutable (`triggers.py`), spec §5.

    No `TimestampMixin`: `created_at` here is `clock_timestamp()`, not the frozen
    per-transaction `now()` every other table uses, for the reason mastro states --
    this is a log, and every row `now()` would give the same insertion order could
    still display backwards (`work-unit.ts:109-118`). `seq`, not `created_at`, is the
    real ordering column: `clock_timestamp()` can still tie within one statement,
    `nextval()` cannot (`work-unit.ts:122-131`).
    """

    __tablename__ = "work_unit_transitions"

    work_unit_id: Mapped[UUID] = mapped_column(ForeignKey("work_units.id"), nullable=False)
    stato_precedente: Mapped[str | None] = mapped_column(String(40), default=None)
    stato_nuovo: Mapped[str] = mapped_column(String(40), nullable=False)
    # Mirrors mastro's `TransitionActor` (`work-unit.ts:84-87`), derived from
    # PigroCRM's own `Actor` (`actor.py:135`) by `service.py::actor_to_transition_json`
    # rather than invented as a new authentication concept.
    attore: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=False), nullable=False)

    __table_args__ = (Index("ix_work_unit_transitions_work_unit_id_seq", "work_unit_id", "seq"),)


class Approval(Base, PrimaryKeyMixin, TimestampMixin):
    """A human's written approval of one or more days -- mastro's `approval`
    (`approval.ts`), spec §6. Immutable by `raise_immutable_violation`
    (`triggers.py`): a correction is always a new row, never an edit of an existing
    one.

    No `SoftDeleteMixin`: deletion is left to the ordinary FK --
    `work_units.approval_id` already references this table with the default
    `RESTRICT`, so an approval something relies on cannot be deleted either, without
    a second, redundant trigger.
    """

    __tablename__ = "approvals"

    contract_id: Mapped[UUID] = mapped_column(
        ForeignKey("contracts.id"), nullable=False, index=True
    )
    canale: Mapped[str] = mapped_column(String(20), nullable=False)
    # Free text, an email address or a name, never validated against
    # `Person`/`client_contact` (spec §2, §6) -- `canApprove` is deliberately not
    # ported.
    mittente: Mapped[str] = mapped_column(Text, nullable=False)
    ricevuto_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    message_id: Mapped[str | None] = mapped_column(Text, default=None)
    # No explicit `ondelete`: RESTRICT is the schema-wide default -- the archived
    # original this approval interprets cannot be deleted out from under it.
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id"), nullable=False, index=True
    )
    estratto: Mapped[str] = mapped_column(Text, nullable=False)
    origine: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint(f"canale IN {APPROVAL_CANALI!r}", name="ck_approvals_canale"),
    )
