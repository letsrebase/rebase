"""The three trigger functions spec §12 ports, verified against mastro's own
migrations (`0012_work_unit_state_machine.sql`, widened by
`0013_worked_without_approval.sql`, and `0011_approval_constraints.sql`): the
state-machine enforcement (with the automatic `lavorato_senza_approvazione` redirect
and recovery), the append-only transition log, and the generic immutability guard
`approvals` and `work_unit_transitions` both attach to.

**Why this SQL lives in one importable constant instead of being typed out twice.**
`Base.metadata.create_all()` -- what `tests/conftest.py` builds the test schema with
-- understands ordinary SQLAlchemy metadata (tables, indexes, `CheckConstraint`,
`ExcludeConstraint`), but a trigger function's body is executable procedural code
`create_all` has no representation for at all. §12 leaves the fixture question open:
an `alembic upgrade head` test fixture, or the same "run the DDL a second time" shape
this schema already uses for `CREATE EXTENSION IF NOT EXISTS pg_trgm`/`btree_gist`
(hand-duplicated between the migration and `conftest.py`'s `db_engine`). This module
picks neither extreme: the tables stay ordinary `Base.metadata` objects (`models.py`,
built by `create_all` exactly like every other table), and only the trigger DDL --
the one piece `create_all` cannot express -- gets the "run twice" treatment, via one
shared constant rather than two hand-copies of ~40 lines of PL/pgSQL. That is the
same reasoning `PROFORMA_SEQUENCE` (`invoices/models.py`) already gives for why it is
"attached to the metadata, not only created by the migration": a fact two paths must
agree on is declared once. The migration's own `upgrade()` still reads as a normal,
self-contained Alembic revision -- it just calls `op.execute(WORK_UNIT_TRIGGER_SQL)`
the same way `0005_invoices.py` calls `op.execute("CREATE SEQUENCE ...")` -- and
`tests/conftest.py` runs the identical text a second time, after `create_all`, so a
trigger a test exercises is provably the same trigger production runs.

The edge list is rendered from `WORK_UNIT_TRANSITIONS` (`models.py`) rather than
copied into the SQL text by hand, so the documented graph and the enforced one cannot
silently disagree.
"""

from pigrocrm.core.work_units.models import WORK_UNIT_TRANSITIONS


def _edges_array_sql() -> str:
    """`ARRAY['proposto->approvato', ...]`, one entry per legal `(from, to)` pair."""
    edges = sorted(
        f"{origin}->{target}"
        for origin, targets in WORK_UNIT_TRANSITIONS.items()
        for target in targets
    )
    body = ",\n            ".join(f"'{edge}'" for edge in edges)
    return f"ARRAY[\n            {body}\n        ]"


_TRIGGER_SQL_TEMPLATE = """
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
        AND NOT ((OLD.stato || '->' || v_requested) = ANY (__EDGES__))
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

WORK_UNIT_TRIGGER_SQL: str = _TRIGGER_SQL_TEMPLATE.replace("__EDGES__", _edges_array_sql())

# What `downgrade()` and the "second connection.execute() block" both need to undo --
# order matters (triggers before the functions they call, `work_units`'s two triggers
# before `approvals`'s, no dependency between them otherwise).
DROP_WORK_UNIT_TRIGGER_SQL: str = """
DROP TRIGGER IF EXISTS trg_work_units_log_transition ON work_units;
DROP TRIGGER IF EXISTS trg_work_units_enforce_state_machine ON work_units;
DROP TRIGGER IF EXISTS trg_work_unit_transitions_immutable ON work_unit_transitions;
DROP TRIGGER IF EXISTS trg_approvals_immutable ON approvals;
DROP FUNCTION IF EXISTS work_unit_log_transition();
DROP FUNCTION IF EXISTS work_unit_enforce_state_machine();
DROP FUNCTION IF EXISTS raise_immutable_violation();
"""

__all__ = ["WORK_UNIT_TRIGGER_SQL", "DROP_WORK_UNIT_TRIGGER_SQL"]
