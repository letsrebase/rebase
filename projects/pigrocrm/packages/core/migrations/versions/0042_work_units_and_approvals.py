"""work_units, work_unit_transitions and approvals, database-trigger enforced

Revision ID: 0042
Revises: 0041

REB-359, the day-lifecycle issue of "Bring mastro's ledger, invoice import and
forecasting into PigroCRM" (REB-344's signed-off mapping,
`docs/superpowers/specs/2026-09-23-mastro-ledger-onto-pigrocrm-design.md` §§5-6, 12).

**Why this is new infrastructure, not an extension of an existing pattern.** Zero
`CREATE TRIGGER`/`CREATE FUNCTION` statements exist anywhere in this migration
directory before this one (§12's own check). Every invariant this schema currently
enforces at write time is a plain `CHECK`/`UniqueConstraint`/partial `Index`, all
expressible without procedural SQL. A day's state machine cannot be, because a
transition's legality depends on the owning contract's own
`requires_prior_approval`, which a `CHECK` cannot subquery.

**The three trigger functions** (frozen below as `_WORK_UNIT_TRIGGER_SQL`, the text
`pigrocrm.core.work_units.triggers.WORK_UNIT_TRIGGER_SQL` rendered when this revision was
written: a migration must give the same schema whatever the application constants become,
and a later change to a trigger is a revision of its own):
`work_unit_enforce_state_machine` (`BEFORE INSERT OR UPDATE` on `work_units`,
rejecting any edge outside the graph in `work_units.models.WORK_UNIT_TRANSITIONS`,
auto-redirecting an unapproved `'lavorato'` write on a contract that requires prior
approval into `'lavorato_senza_approvazione'`, and recovering it the moment an
approval is linked), `work_unit_log_transition` (`AFTER INSERT OR UPDATE`, one
`work_unit_transitions` row per real state change), and `raise_immutable_violation`
(the generic guard `approvals` and `work_unit_transitions` both attach to).

**The partial unique index**, `uq_work_units_contract_data_live` on
`(contract_id, data) WHERE stato NOT IN ('rifiutato', 'revocato')`, keeps at most one
live day per contract per date -- mirroring mastro's own
`0012_work_unit_state_machine.sql:40-42`.

Table creation order is FK-driven: `approvals` before `work_units` (which references
it), `work_units` before `work_unit_transitions` (which references that).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042"
down_revision: str | Sequence[str] | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (PCORE-A-19): the SQL this revision created, not an import of what the
# application says today.
_WORK_UNIT_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION raise_immutable_violation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION '% on % is not allowed: rows in this table are immutable once written (id=%)',
        TG_OP, TG_TABLE_NAME, COALESCE(NEW.id, OLD.id)
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION work_unit_enforce_state_machine() RETURNS TRIGGER AS $$
DECLARE
    v_requires_approval boolean;
    -- What the caller actually asked for, captured before anything below rewrites
    -- `NEW.stato` -- the edge check runs against this, not against the possibly
    -- redirected value, so a state that was never allowed to reach 'lavorato' at
    -- all cannot sneak into 'lavorato_senza_approvazione' by asking for the
    -- unapproved write instead.
    v_requested text := NEW.stato;
BEGIN
    SELECT requires_prior_approval INTO v_requires_approval
    FROM contracts WHERE id = NEW.contract_id;

    IF v_requires_approval IS NULL THEN
        RAISE EXCEPTION 'work_units.contract_id % does not reference a contract', NEW.contract_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF v_requested NOT IN ('proposto', 'approvato', 'lavorato') THEN
            RAISE EXCEPTION
                'a work_unit cannot be created directly in state %; '
                'the legal entry states are proposto, approvato, lavorato',
                v_requested
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF v_requested <> OLD.stato
        AND NOT ((OLD.stato || '->' || v_requested) = ANY (ARRAY[
            'approvato->lavorato',
            'approvato->revocato',
            'contestato->lavorato',
            'contestato->non_fatturabile',
            'contestato->revocato',
            'fatturato->contestato',
            'fatturato->pagato',
            'lavorato->contestato',
            'lavorato->fatturato',
            'lavorato->non_fatturabile',
            'lavorato->revocato',
            'lavorato_senza_approvazione->contestato',
            'lavorato_senza_approvazione->fatturato',
            'lavorato_senza_approvazione->lavorato',
            'lavorato_senza_approvazione->non_fatturabile',
            'lavorato_senza_approvazione->revocato',
            'proposto->approvato',
            'proposto->lavorato',
            'proposto->revocato',
            'proposto->rifiutato'
        ]))
    THEN
        RAISE EXCEPTION 'work_unit % cannot move from % to %', NEW.id, OLD.stato, v_requested
            USING ERRCODE = 'check_violation';
    END IF;

    -- The automatic redirect (0013): a 'lavorato' write with no approval on a
    -- contract that requires one is never rejected outright -- it is recorded,
    -- flagged, and recovered the moment an approval is linked (spec §5).
    IF NEW.stato = 'lavorato' AND v_requires_approval AND NEW.approval_id IS NULL THEN
        NEW.stato := 'lavorato_senza_approvazione';
    END IF;

    -- The recovery half: the flagged state returns to plain 'lavorato' the instant
    -- an approval is linked, whether the caller asked for 'lavorato' explicitly
    -- (already validated as a legal edge above) or only set approval_id while
    -- leaving stato untouched (v_requested = OLD.stato, so the edge check above
    -- was a same-state no-op and this is the only thing that moves it).
    IF NEW.stato = 'lavorato_senza_approvazione' AND NEW.approval_id IS NOT NULL THEN
        NEW.stato := 'lavorato';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION work_unit_log_transition() RETURNS TRIGGER AS $$
DECLARE
    v_actor jsonb;
    v_motivo text;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.stato = OLD.stato THEN
        RETURN NEW;
    END IF;

    -- Session-local settings the repository layer sets immediately before the
    -- write (`set_config(..., true)`, scoped to the transaction), falling back to
    -- a system actor and a generic reason when unset rather than failing -- the log
    -- must never go silently incomplete (spec §5, `0012...sql:97-105`).
    v_actor := COALESCE(
        NULLIF(current_setting('pigrocrm.actor', true), '')::jsonb,
        '{"kind": "system"}'::jsonb
    );
    v_motivo := COALESCE(
        NULLIF(current_setting('pigrocrm.motivo', true), ''),
        'nessun motivo fornito'
    );

    INSERT INTO work_unit_transitions
        (id, work_unit_id, stato_precedente, stato_nuovo, attore, motivo)
    VALUES (
        gen_random_uuid(),
        NEW.id,
        CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.stato END,
        NEW.stato,
        v_actor,
        v_motivo
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_approvals_immutable ON approvals;
CREATE TRIGGER trg_approvals_immutable
    BEFORE UPDATE ON approvals
    FOR EACH ROW EXECUTE FUNCTION raise_immutable_violation();

DROP TRIGGER IF EXISTS trg_work_unit_transitions_immutable ON work_unit_transitions;
CREATE TRIGGER trg_work_unit_transitions_immutable
    BEFORE UPDATE OR DELETE ON work_unit_transitions
    FOR EACH ROW EXECUTE FUNCTION raise_immutable_violation();

DROP TRIGGER IF EXISTS trg_work_units_enforce_state_machine ON work_units;
CREATE TRIGGER trg_work_units_enforce_state_machine
    BEFORE INSERT OR UPDATE ON work_units
    FOR EACH ROW EXECUTE FUNCTION work_unit_enforce_state_machine();

DROP TRIGGER IF EXISTS trg_work_units_log_transition ON work_units;
CREATE TRIGGER trg_work_units_log_transition
    AFTER INSERT OR UPDATE ON work_units
    FOR EACH ROW EXECUTE FUNCTION work_unit_log_transition();
"""

_DROP_WORK_UNIT_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS trg_work_units_log_transition ON work_units;
DROP TRIGGER IF EXISTS trg_work_units_enforce_state_machine ON work_units;
DROP TRIGGER IF EXISTS trg_work_unit_transitions_immutable ON work_unit_transitions;
DROP TRIGGER IF EXISTS trg_approvals_immutable ON approvals;
DROP FUNCTION IF EXISTS work_unit_log_transition();
DROP FUNCTION IF EXISTS work_unit_enforce_state_machine();
DROP FUNCTION IF EXISTS raise_immutable_violation();
"""

_TIMESTAMPS = (
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("canale", sa.String(length=20), nullable=False),
        sa.Column("mittente", sa.Text(), nullable=False),
        sa.Column("ricevuto_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("estratto", sa.Text(), nullable=False),
        sa.Column("origine", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_TIMESTAMPS,
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.CheckConstraint(
            "canale IN ('email', 'posta_certificata', 'raccomandata', 'corriere', 'altro')",
            name="ck_approvals_canale",
        ),
    )
    op.create_index("ix_approvals_contract_id", "approvals", ["contract_id"])
    op.create_index("ix_approvals_document_id", "approvals", ["document_id"])

    op.create_table(
        "work_units",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("quantita", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("descrizione", sa.Text(), nullable=False),
        sa.Column(
            "stato", sa.String(length=40), nullable=False, server_default=sa.text("'proposto'")
        ),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("invoice_line_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        *_TIMESTAMPS,
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
        sa.ForeignKeyConstraint(["approval_id"], ["approvals.id"]),
        sa.ForeignKeyConstraint(["invoice_line_id"], ["invoice_lines.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "stato IN ('proposto', 'approvato', 'lavorato', 'lavorato_senza_approvazione', "
            "'fatturato', 'pagato', 'contestato', 'revocato', 'rifiutato', 'non_fatturabile')",
            name="ck_work_units_stato",
        ),
    )
    op.create_index("ix_work_units_contract_id", "work_units", ["contract_id"])
    op.create_index("ix_work_units_approval_id", "work_units", ["approval_id"])
    op.create_index("ix_work_units_invoice_line_id", "work_units", ["invoice_line_id"])
    # At most one live day per contract per date -- `rifiutato`/`revocato` are
    # excluded because those are exactly the two outcomes that free a date for a
    # fresh proposal; `non_fatturabile` stays counted (still real, recorded work).
    op.create_index(
        "uq_work_units_contract_data_live",
        "work_units",
        ["contract_id", "data"],
        unique=True,
        postgresql_where=sa.text("stato NOT IN ('rifiutato', 'revocato')"),
    )

    op.create_table(
        "work_unit_transitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("work_unit_id", sa.Uuid(), nullable=False),
        sa.Column("stato_precedente", sa.String(length=40), nullable=True),
        sa.Column("stato_nuovo", sa.String(length=40), nullable=False),
        sa.Column("attore", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["work_unit_id"], ["work_units.id"]),
    )
    op.create_index(
        "ix_work_unit_transitions_work_unit_id_seq",
        "work_unit_transitions",
        ["work_unit_id", "seq"],
    )

    # The three trigger functions and their CREATE TRIGGER calls (§12).
    op.execute(_WORK_UNIT_TRIGGER_SQL)


def downgrade() -> None:
    op.execute(_DROP_WORK_UNIT_TRIGGER_SQL)

    op.drop_index("ix_work_unit_transitions_work_unit_id_seq", "work_unit_transitions")
    op.drop_table("work_unit_transitions")

    op.drop_index("uq_work_units_contract_data_live", "work_units")
    op.drop_index("ix_work_units_invoice_line_id", "work_units")
    op.drop_index("ix_work_units_approval_id", "work_units")
    op.drop_index("ix_work_units_contract_id", "work_units")
    op.drop_table("work_units")

    op.drop_index("ix_approvals_document_id", "approvals")
    op.drop_index("ix_approvals_contract_id", "approvals")
    op.drop_table("approvals")
