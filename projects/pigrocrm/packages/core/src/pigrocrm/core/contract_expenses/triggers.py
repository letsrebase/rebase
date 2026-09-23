"""The one trigger function spec §7 asks for, verified against mastro's own
migration (`0025_expense_and_clause_note_constraints.sql::expense_set_reimbursable`):
`rimborsabile` is never set by application code -- it is recomputed on every insert
or update, from whatever the row and its owning contract's `politica_spese` say
right now, and the write itself is never rejected either way (spec §7, §14's own
"Rebillable expenses" bullet).

**Why this SQL lives in one importable constant instead of being typed out twice.**
Identical reasoning to `work_units.triggers` (see that module's own docstring in
full): `Base.metadata.create_all()` -- what `tests/conftest.py` builds the test
schema with -- has no representation for a trigger function's body, so the
migration's `upgrade()` and the test fixture both run this exact text via
`op.execute(...)` / `connection.execute(text(...))`, never two hand-copies of the
same ~20 lines of PL/pgSQL.

Unlike `work_units`, there is exactly one trigger here and no state machine to
render from a table -- `contract_expenses` has no legal-edge graph, only a boolean
recomputed from two JSONB reads, so this module carries no `_edges_array_sql()`
equivalent.
"""

CONTRACT_EXPENSE_TRIGGER_SQL: str = """
CREATE OR REPLACE FUNCTION contract_expense_set_rimborsabile() RETURNS TRIGGER AS $$
DECLARE
    v_politica jsonb;
    v_kind text;
    v_richiede_preautorizzazione boolean;
BEGIN
    SELECT c.politica_spese INTO v_politica
    FROM contracts c WHERE c.id = NEW.contract_id;

    IF v_politica IS NULL THEN
        RAISE EXCEPTION 'contract_expenses.contract_id % does not reference a contract',
            NEW.contract_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    v_kind := v_politica ->> 'kind';
    IF v_kind IS NULL THEN
        RAISE EXCEPTION
            'contracts.politica_spese for contract % has no "kind" tag: %',
            NEW.contract_id, v_politica
            USING ERRCODE = 'check_violation';
    END IF;
    v_richiede_preautorizzazione := COALESCE(
        (v_politica ->> 'richiede_preautorizzazione')::boolean, false
    );

    -- mastro's own `expense_set_reimbursable`, translated one for one: never
    -- reimbursable at all under 'non_rimborsabile', regardless of
    -- pre-authorisation; otherwise reimbursable unless the policy requires
    -- pre-authorisation and this expense does not have it -- in which case it is
    -- still recorded, just flagged, never rejected outright.
    NEW.rimborsabile := (v_kind <> 'non_rimborsabile')
        AND (NOT v_richiede_preautorizzazione OR NEW.pre_autorizzata);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_contract_expenses_set_rimborsabile ON contract_expenses;
CREATE TRIGGER trg_contract_expenses_set_rimborsabile
    BEFORE INSERT OR UPDATE ON contract_expenses
    FOR EACH ROW EXECUTE FUNCTION contract_expense_set_rimborsabile();
"""

DROP_CONTRACT_EXPENSE_TRIGGER_SQL: str = """
DROP TRIGGER IF EXISTS trg_contract_expenses_set_rimborsabile ON contract_expenses;
DROP FUNCTION IF EXISTS contract_expense_set_rimborsabile();
"""

__all__ = ["CONTRACT_EXPENSE_TRIGGER_SQL", "DROP_CONTRACT_EXPENSE_TRIGGER_SQL"]
