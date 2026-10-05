"""The «tutte» fast path first in every through-the-parent policy (REB-656).

Revision ID: 0049
Revises: 0048

Measured on REB-633's corpus (200,000 timeline rows, read as `pigrocrm_app`): the
dashboard's recent 50 activities cost 15.8 ms as «tutte» against 0.04 ms with no policy,
about 0.3 ms per row walked. The cost is the shape of 0048's predicates that reach an
azienda through a parent: `customer_visibile(customer_id)` and its kin hold an `EXISTS`,
which keeps Postgres from inlining them, so each row costs a SQL function call (and, in
`entita_visibile`, a second one inside the first) before the `*` inside the body answers.
An unscoped admin, who is every admin today, paid it on people, time entries, invoice
lines, versions, proposals, approvals, the Gmail tables and the timeline.

Here every policy whose `USING` reaches a parent becomes `scope_tutte() OR (<the
predicate as 0048 wrote it>)`. `scope_tutte()` has no `FROM` and no sublink, so the
planner inlines it into the policy's own filter, and a session bound to `*` evaluates one
`current_setting` per row and calls no function at all. The executor evaluates the arms
of an `OR` in the order written and stops at the first true one (it reorders the top-level
conjuncts of a `WHERE`, never the arms of one `OR`); the documentation promises no order,
as 0048's docstring says, and nothing here depends on it for correctness, only for the
cost: the right arm is 0048's whole predicate, right on its own. That predicate is read
from 0048's own `POLICIES`, so the scoped half of each rule is the one 0048 states, by
construction; `WITH CHECK` takes the same expression where 0048 made it equal to `USING`,
and `gmail_message_links` keeps its `true`. Under `*` every one of these predicates already
answered true; under a list or the empty string `false OR x` is `x`; with nothing bound
`NULL OR x` is true exactly when `x` is and NULL otherwise, which a policy refuses as it
refuses false. So nothing a scoped or unbound session sees or may write changes.

The eight through-the-parent functions are recreated the same way, with the
`current_setting` test spelled out first instead of a call to `scope_tutte()`: nothing a
policy above still reaches under `*`, but a caller outside these policies (a later
policy, a definer function, a query by hand) gets the same answer at the same price. In
`entita_visibile` the test moves in front of the whole `CASE`, which under `*` answers
true for every entity type, including the unknown ones the third argument decides: every
caller passes `true` or `scope_tutte()` there, both true under `*`.

Not here: `azienda_visibile`, which is a `CASE` on the setting already and inlines;
the tables whose policy calls only `scope_tutte` and `azienda_visibile`, which inline for
the same reason; and an index on `activities.kind`, a cost of its own the policy only
multiplies. The downgrade puts 0048's predicates and function bodies back as they were.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table whose 0048 policy reaches an azienda through a parent row.
FAST_PATH_TABLES: tuple[str, ...] = (
    "people",
    "time_entries",
    "time_timers",
    "invoice_lines",
    "payment_reminders",
    "document_versions",
    "proposals",
    "rate_cards",
    "renewal_assumptions",
    "contract_expenses",
    "work_units",
    "approvals",
    "work_unit_transitions",
    "attivita",
    "gmail_message_links",
    "email_drafts",
    "gmail_messages",
    "activities",
)

TUTTE = "current_setting('pigrocrm.aziende', true) = '*'"

FUNCTIONS = [
    f"""
    CREATE OR REPLACE FUNCTION customer_visibile(uuid) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT {TUTTE} OR EXISTS (
        SELECT 1 FROM customers c WHERE c.id = $1 AND azienda_visibile(c.azienda_id))
    $$
    """,
    f"""
    CREATE OR REPLACE FUNCTION person_visibile(uuid) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT {TUTTE} OR EXISTS (
        SELECT 1 FROM people p JOIN customers c ON c.id = p.customer_id
        WHERE p.id = $1 AND azienda_visibile(c.azienda_id))
    $$
    """,
    f"""
    CREATE OR REPLACE FUNCTION deal_visibile(uuid) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT {TUTTE} OR EXISTS (
        SELECT 1 FROM deals d WHERE d.id = $1 AND azienda_visibile(d.azienda_id))
    $$
    """,
    f"""
    CREATE OR REPLACE FUNCTION invoice_visibile(uuid) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT {TUTTE} OR EXISTS (
        SELECT 1 FROM invoices i WHERE i.id = $1 AND azienda_visibile(i.azienda_id))
    $$
    """,
    f"""
    CREATE OR REPLACE FUNCTION contract_visibile(uuid) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT {TUTTE} OR EXISTS (
        SELECT 1 FROM contracts k WHERE k.id = $1 AND azienda_visibile(k.azienda_id))
    $$
    """,
    f"""
    CREATE OR REPLACE FUNCTION document_visibile(uuid) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT {TUTTE} OR EXISTS (
        SELECT 1 FROM documents d WHERE d.id = $1 AND azienda_visibile(d.azienda_id))
    $$
    """,
    f"""
    CREATE OR REPLACE FUNCTION work_unit_visibile(uuid) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT {TUTTE} OR EXISTS (
        SELECT 1 FROM work_units w JOIN contracts k ON k.id = w.contract_id
        WHERE w.id = $1 AND azienda_visibile(k.azienda_id))
    $$
    """,
    # The polymorphic tables: `entity_type` names the table `entity_id` points at. The
    # third argument is the answer for a type with no azienda, true for the timeline
    # (a user's or a template's row is everyone's) and «tutte» only for a link or a
    # draft, which only ever name a customer, a person or a deal. The branches no longer
    # repeat the «tutte» test: the one in front answers for all of them.
    f"""
    CREATE OR REPLACE FUNCTION entita_visibile(text, uuid, boolean) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT {TUTTE} OR CASE $1
        WHEN 'customer' THEN customer_visibile($2)
        WHEN 'person' THEN person_visibile($2)
        WHEN 'deal' THEN deal_visibile($2)
        WHEN 'contract' THEN contract_visibile($2)
        WHEN 'document' THEN document_visibile($2)
        WHEN 'invoice' THEN invoice_visibile($2)
        WHEN 'work_unit' THEN work_unit_visibile($2)
        WHEN 'emitter_profile' THEN azienda_visibile($2)
        WHEN 'fiscal_profile' THEN EXISTS (
          SELECT 1 FROM fiscal_profile f WHERE f.id = $2 AND azienda_visibile(f.azienda_id))
        WHEN 'payment_reminder' THEN EXISTS (
          SELECT 1 FROM payment_reminders r WHERE r.id = $2 AND invoice_visibile(r.invoice_id))
        WHEN 'email_draft' THEN EXISTS (
          SELECT 1 FROM email_drafts e WHERE e.id = $2
          AND entita_visibile(e.entity_type, e.entity_id, scope_tutte()))
        WHEN 'proposal' THEN EXISTS (
          SELECT 1 FROM proposals p WHERE p.id = $2 AND document_visibile(p.document_id)
          AND (p.contract_id IS NULL OR contract_visibile(p.contract_id)))
        WHEN 'contract_expense' THEN EXISTS (
          SELECT 1 FROM contract_expenses x WHERE x.id = $2 AND contract_visibile(x.contract_id))
        WHEN 'approval' THEN EXISTS (
          SELECT 1 FROM approvals a WHERE a.id = $2 AND contract_visibile(a.contract_id))
        WHEN 'time_entry' THEN EXISTS (
          SELECT 1 FROM time_entries t WHERE t.id = $2 AND deal_visibile(t.deal_id))
        WHEN 'timer' THEN EXISTS (
          SELECT 1 FROM time_timers t WHERE t.id = $2
          AND (t.deal_id IS NULL OR deal_visibile(t.deal_id)))
        WHEN 'cost' THEN EXISTS (
          SELECT 1 FROM costs c WHERE c.id = $2
          AND (c.azienda_id IS NULL OR azienda_visibile(c.azienda_id)))
        WHEN 'attivita' THEN EXISTS (
          SELECT 1 FROM attivita a WHERE a.id = $2 AND (
            (a.customer_id IS NULL OR customer_visibile(a.customer_id))
            AND (a.person_id IS NULL OR person_visibile(a.person_id))
            AND (a.deal_id IS NULL OR deal_visibile(a.deal_id))
            AND (a.invoice_id IS NULL OR invoice_visibile(a.invoice_id))))
        ELSE $3
      END
    $$
    """,
]

FUNCTION_NAMES = (
    "customer_visibile",
    "person_visibile",
    "deal_visibile",
    "invoice_visibile",
    "contract_visibile",
    "document_visibile",
    "work_unit_visibile",
    "entita_visibile",
)


def _revision_0048() -> ModuleType:
    """Migration 0048, loaded from the file beside this one: a revision is a script in
    `versions/`, not a module on the path, and its `POLICIES` and `FUNCTIONS` are the
    record this revision rewrites and the downgrade restores."""
    path = Path(__file__).resolve().with_name("0048_azienda_scope.py")
    spec = importlib.util.spec_from_file_location("pigrocrm_migration_0048", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alter_policy(table: str, using: str, check: str) -> str:
    return f"ALTER POLICY ambito_azienda ON {table} USING ({using}) WITH CHECK ({check})"


def policies() -> list[tuple[str, str, str]]:
    """Per table: the `USING` and `WITH CHECK` this revision installs, derived from
    0048's own predicate."""
    base = _revision_0048().POLICIES
    out = []
    for table in FAST_PATH_TABLES:
        using, check = base[table]
        fast = f"scope_tutte() OR ({using})"
        out.append((table, fast, check or fast))
    return out


def statements() -> list[str]:
    """Every statement `upgrade` runs, in order: the functions, then one `ALTER POLICY`
    per table. Also what the test suite applies to its template database after 0048's
    (`projects/pigrocrm/conftest.py`), so a test root's clone carries the same
    predicates production has."""
    out = list(FUNCTIONS)
    out.extend(_alter_policy(table, using, check) for table, using, check in policies())
    return out


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    base = _revision_0048()
    for table in FAST_PATH_TABLES:
        using, check = base.POLICIES[table]
        op.execute(_alter_policy(table, using, check or using))
    restored = [
        statement
        for statement in base.FUNCTIONS
        if any(f"FUNCTION {name}(" in statement for name in FUNCTION_NAMES)
    ]
    assert len(restored) == len(FUNCTION_NAMES), "0048 no longer states all eight bodies"
    for statement in restored:
        op.execute(statement)
