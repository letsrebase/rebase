"""Row-level security on every azienda-scoped table (REB-633).

Revision ID: 0048
Revises: 0047

Spec 2026-10-03 §4, §2 step 8, milestone 6 of §9. A scoped member sees and writes
only the rows of their aziende, and the boundary is Postgres, never a `WHERE` a
repository remembered to add: one `FOR ALL` policy per table, `USING` and `WITH CHECK`
on the same expression except where the table says otherwise, `FORCE ROW LEVEL
SECURITY` so the owner is subject to it too. A superuser never is, which is why the
test suite's user sees everything and `test_azienda_scope.py` connects as a role of
its own.

The predicate reads two transaction-local settings `db/scope.py` binds:
`pigrocrm.aziende` (`*`, or comma-joined ids, or the empty string) and
`pigrocrm.user_id`. A connection with nothing bound reads `NULL`, a pooled one with an
expired value the empty string, and both are closed. The `*` branch of
`azienda_visibile` is a `CASE`, never an `OR`: Postgres does not promise to evaluate the
left operand of an `OR` first, and casting `*` to `uuid[]` is an error the moment it is
tried. The helpers below are plain SQL, `STABLE`, and spell the parent's own visibility
out instead of relying on the parent table's policy applying inside a subquery, so a
reader of one function sees the whole rule.

Space-level tables carry no policy (`users`, `invitations`, `user_aziende`, the tokens,
`pipeline_stages`, `templates`, `cost_categories`, `field_definitions`, `period_locks`,
`space_settings`, `automation_config`, `digests`, `google_accounts`,
`google_drive_accounts`, `google_oauth_states`, `gmail_known_addresses`): reading them
is harmless and writing them is `require_unscoped_admin`. The downgrade drops every
policy, disables row-level security and drops the functions: nothing it leaves behind.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table that gets a policy, with its `USING` expression and, where it differs,
# its `WITH CHECK`. `_parent_visible(...)` helpers are the functions created below.
_T = "true"
POLICIES: dict[str, tuple[str, str | None]] = {
    "emitter_profile": ("azienda_visibile(id)", None),
    "fiscal_profile": ("azienda_visibile(azienda_id)", None),
    "invoice_counters": ("azienda_visibile(azienda_id)", None),
    "invoice_register_gaps": ("azienda_visibile(azienda_id)", None),
    "customers": ("azienda_visibile(azienda_id)", None),
    "deals": ("azienda_visibile(azienda_id)", None),
    "contracts": ("azienda_visibile(azienda_id)", None),
    "documents": ("azienda_visibile(azienda_id)", None),
    "invoices": ("azienda_visibile(azienda_id)", None),
    # A shared cost (no azienda) is «tutte»'s alone, for reading and for writing.
    "costs": ("(azienda_id IS NULL AND scope_tutte()) OR azienda_visibile(azienda_id)", None),
    "people": ("(customer_id IS NULL AND scope_tutte()) OR customer_visibile(customer_id)", None),
    "time_entries": ("deal_visibile(deal_id)", None),
    "time_timers": ("(deal_id IS NULL AND scope_tutte()) OR deal_visibile(deal_id)", None),
    "invoice_lines": ("invoice_visibile(invoice_id)", None),
    "payment_reminders": ("invoice_visibile(invoice_id)", None),
    "document_versions": ("document_visibile(document_id)", None),
    "rate_cards": ("contract_visibile(contract_id)", None),
    "renewal_assumptions": ("contract_visibile(contract_id)", None),
    "contract_expenses": ("contract_visibile(contract_id)", None),
    "work_units": ("contract_visibile(contract_id)", None),
    "approvals": ("contract_visibile(contract_id)", None),
    "work_unit_transitions": ("work_unit_visibile(work_unit_id)", None),
    # A proposal follows its evidence document and, when it names one, the contract it
    # proposes for: the document may sit with a customer that moved, the contract did
    # not (CodeRabbit's sixth adversarial pass on PR #513).
    "proposals": (
        "document_visibile(document_id) "
        "AND (contract_id IS NULL OR contract_visibile(contract_id))",
        None,
    ),
    "attivita": (
        "CASE WHEN customer_id IS NULL AND person_id IS NULL AND deal_id IS NULL "
        "AND invoice_id IS NULL THEN scope_tutte() ELSE "
        "(customer_id IS NULL OR customer_visibile(customer_id)) "
        "AND (person_id IS NULL OR person_visibile(person_id)) "
        "AND (deal_id IS NULL OR deal_visibile(deal_id)) "
        "AND (invoice_id IS NULL OR invoice_visibile(invoice_id)) END",
        None,
    ),
    # A link is written after the mail has left (`gmail/send.py`): recording a delivered
    # mail never fails on the scope. A scoped sender cannot reach an out-of-scope
    # contact anyway, since `people` hides it from the recipient resolution.
    "gmail_message_links": ("entita_visibile(entity_type, entity_id, scope_tutte())", _T),
    # A draft follows its entity, and a payment reminder's draft follows the invoice
    # as well: the body copies the invoice's number, dates, total and IBAN, and a
    # customer moved to another azienda keeps its invoices where they were (spec
    # §1.7), so the draft must stay with them (CodeRabbit's adversarial pass on PR
    # #513). «Tutte» reads everything, as for every other through-the-record rule.
    "email_drafts": (
        "entita_visibile(entity_type, entity_id, scope_tutte()) AND ("
        "payment_reminder_id IS NULL OR scope_tutte() OR EXISTS ("
        "SELECT 1 FROM payment_reminders r WHERE r.id = email_drafts.payment_reminder_id "
        "AND invoice_visibile(r.invoice_id)))",
        None,
    ),
    # Own mailbox, or a visible link, or no link at all and «tutte». The first branch
    # is what lets the sync and the send write a message before its links exist: the
    # flush is an `INSERT ... RETURNING`, and Postgres applies the `SELECT` policy to
    # the rows an `INSERT` returns, so `WITH CHECK (true)` alone would not do.
    # And a sent payment reminder follows its invoice, like its draft and its timeline
    # row (CodeRabbit's third adversarial pass on PR #513): the message copies the
    # draft's body, the draft remembers the Gmail id it left as, and
    # `sollecito_di_messaggio` reads that chain as the owner, past the policies that
    # would hide the draft from the very member this clause is for.
    "gmail_messages": (
        "(mailbox_mia(google_account_id) OR EXISTS (SELECT 1 FROM gmail_message_links l "
        "WHERE l.gmail_message_id = gmail_messages.id "
        "AND entita_visibile(l.entity_type, l.entity_id, scope_tutte())) "
        "OR (NOT EXISTS (SELECT 1 FROM gmail_message_links l "
        "WHERE l.gmail_message_id = gmail_messages.id) AND scope_tutte())) "
        "AND (scope_tutte() "
        "OR sollecito_di_messaggio(google_account_id, gmail_message_id) IS NULL "
        "OR invoice_visibile(sollecito_di_messaggio(google_account_id, gmail_message_id)))",
        None,
    ),
    # The timeline: a row about an azienda-bound record follows that record; a row
    # about a space-level one (a user, a template, a stage, ...) is everyone's. The
    # invoice register has no row of its own: its timeline entries carry a derived id
    # and name their azienda in the payload, which is what the policy reads for them
    # (CodeRabbit's adversarial pass on PR #513: a gap's number and reason are the
    # azienda's, not the space's).
    # A row whose payload names an invoice (a reminder prepared on a customer, hours
    # bound to an invoice on a deal) follows that invoice too, for the same reason as
    # the draft above: the record it hangs on may have moved, the invoice has not.
    "activities": (
        "(CASE WHEN entity_type = 'invoice_register' "
        "THEN azienda_visibile(NULLIF(payload->>'azienda_id', '')::uuid) "
        "ELSE entita_visibile(entity_type, entity_id, true) END) AND ("
        "payload->>'invoice_id' IS NULL OR scope_tutte() "
        "OR invoice_visibile(NULLIF(payload->>'invoice_id', '')::uuid))",
        None,
    ),
}

FUNCTIONS = [
    # The invoice behind a sent payment reminder, or NULL for any other message. Read as
    # the owner with «tutte» set for the call alone (`SET` on the function restores the
    # caller's value on return), because the draft and the reminder are themselves
    # policied and the member this answer is for cannot see them; the invoice's own
    # visibility is then judged outside, under the caller's real scope.
    """
    CREATE OR REPLACE FUNCTION sollecito_di_messaggio(uuid, text) RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = public
    SET pigrocrm.aziende = '*'
    AS $$
      SELECT r.invoice_id
      FROM email_drafts d
      JOIN payment_reminders r ON r.id = d.payment_reminder_id
      WHERE d.google_account_id = $1 AND d.sent_gmail_message_id = $2
      LIMIT 1
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION scope_tutte() RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT current_setting('pigrocrm.aziende', true) = '*'
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION azienda_visibile(uuid) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT CASE current_setting('pigrocrm.aziende', true)
        WHEN '*' THEN true
        ELSE $1 = ANY (string_to_array(
          coalesce(current_setting('pigrocrm.aziende', true), ''), ',')::uuid[])
      END
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION customer_visibile(uuid) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT scope_tutte() OR EXISTS (
        SELECT 1 FROM customers c WHERE c.id = $1 AND azienda_visibile(c.azienda_id))
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION person_visibile(uuid) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT scope_tutte() OR EXISTS (
        SELECT 1 FROM people p JOIN customers c ON c.id = p.customer_id
        WHERE p.id = $1 AND azienda_visibile(c.azienda_id))
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION deal_visibile(uuid) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT scope_tutte() OR EXISTS (
        SELECT 1 FROM deals d WHERE d.id = $1 AND azienda_visibile(d.azienda_id))
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION invoice_visibile(uuid) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT scope_tutte() OR EXISTS (
        SELECT 1 FROM invoices i WHERE i.id = $1 AND azienda_visibile(i.azienda_id))
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION contract_visibile(uuid) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT scope_tutte() OR EXISTS (
        SELECT 1 FROM contracts k WHERE k.id = $1 AND azienda_visibile(k.azienda_id))
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION document_visibile(uuid) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT scope_tutte() OR EXISTS (
        SELECT 1 FROM documents d WHERE d.id = $1 AND azienda_visibile(d.azienda_id))
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION work_unit_visibile(uuid) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT scope_tutte() OR EXISTS (
        SELECT 1 FROM work_units w JOIN contracts k ON k.id = w.contract_id
        WHERE w.id = $1 AND azienda_visibile(k.azienda_id))
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION mailbox_mia(uuid) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT EXISTS (
        SELECT 1 FROM google_accounts g WHERE g.id = $1
        AND g.user_id::text = coalesce(current_setting('pigrocrm.user_id', true), ''))
    $$
    """,
    # The polymorphic tables: `entity_type` names the table `entity_id` points at. The
    # third argument is the answer for a type with no azienda, true for the timeline
    # (a user's or a template's row is everyone's) and «tutte» only for a link or a
    # draft, which only ever name a customer, a person or a deal.
    """
    CREATE OR REPLACE FUNCTION entita_visibile(text, uuid, boolean) RETURNS boolean
    LANGUAGE sql STABLE AS $$
      SELECT CASE $1
        WHEN 'customer' THEN customer_visibile($2)
        WHEN 'person' THEN person_visibile($2)
        WHEN 'deal' THEN deal_visibile($2)
        WHEN 'contract' THEN contract_visibile($2)
        WHEN 'document' THEN document_visibile($2)
        WHEN 'invoice' THEN invoice_visibile($2)
        WHEN 'work_unit' THEN work_unit_visibile($2)
        WHEN 'emitter_profile' THEN azienda_visibile($2)
        WHEN 'fiscal_profile' THEN scope_tutte() OR EXISTS (
          SELECT 1 FROM fiscal_profile f WHERE f.id = $2 AND azienda_visibile(f.azienda_id))
        WHEN 'payment_reminder' THEN scope_tutte() OR EXISTS (
          SELECT 1 FROM payment_reminders r WHERE r.id = $2 AND invoice_visibile(r.invoice_id))
        WHEN 'email_draft' THEN scope_tutte() OR EXISTS (
          SELECT 1 FROM email_drafts e WHERE e.id = $2
          AND entita_visibile(e.entity_type, e.entity_id, scope_tutte()))
        WHEN 'proposal' THEN scope_tutte() OR EXISTS (
          SELECT 1 FROM proposals p WHERE p.id = $2 AND document_visibile(p.document_id)
          AND (p.contract_id IS NULL OR contract_visibile(p.contract_id)))
        WHEN 'contract_expense' THEN scope_tutte() OR EXISTS (
          SELECT 1 FROM contract_expenses x WHERE x.id = $2 AND contract_visibile(x.contract_id))
        WHEN 'approval' THEN scope_tutte() OR EXISTS (
          SELECT 1 FROM approvals a WHERE a.id = $2 AND contract_visibile(a.contract_id))
        WHEN 'time_entry' THEN scope_tutte() OR EXISTS (
          SELECT 1 FROM time_entries t WHERE t.id = $2 AND deal_visibile(t.deal_id))
        WHEN 'timer' THEN scope_tutte() OR EXISTS (
          SELECT 1 FROM time_timers t WHERE t.id = $2
          AND (t.deal_id IS NULL OR deal_visibile(t.deal_id)))
        WHEN 'cost' THEN scope_tutte() OR EXISTS (
          SELECT 1 FROM costs c WHERE c.id = $2
          AND (c.azienda_id IS NULL OR azienda_visibile(c.azienda_id)))
        WHEN 'attivita' THEN scope_tutte() OR EXISTS (
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

FUNCTION_NAMES = [
    "sollecito_di_messaggio(uuid, text)",
    "entita_visibile(text, uuid, boolean)",
    "mailbox_mia(uuid)",
    "work_unit_visibile(uuid)",
    "document_visibile(uuid)",
    "contract_visibile(uuid)",
    "invoice_visibile(uuid)",
    "deal_visibile(uuid)",
    "person_visibile(uuid)",
    "customer_visibile(uuid)",
    "azienda_visibile(uuid)",
    "scope_tutte()",
]


def statements() -> list[str]:
    """Every statement `upgrade` runs, in order: the functions, then per table the
    policy, `ENABLE` and `FORCE`. Also what the test suite applies to the template
    database it builds with `create_all` instead of Alembic (`projects/pigrocrm/
    conftest.py`), so a test root's clone carries the same policies production has."""
    out = list(FUNCTIONS)
    for table, (using, check) in POLICIES.items():
        out.append(
            f"CREATE POLICY ambito_azienda ON {table} FOR ALL "
            f"USING ({using}) WITH CHECK ({check or using})"
        )
        out.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        out.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    return out


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    for table in POLICIES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS ambito_azienda ON {table}")
    for name in FUNCTION_NAMES:
        op.execute(f"DROP FUNCTION IF EXISTS {name}")
