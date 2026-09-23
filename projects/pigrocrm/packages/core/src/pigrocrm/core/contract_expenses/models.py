"""`contract_expenses`, per the signed-off mapping in
`docs/superpowers/specs/2026-09-23-mastro-ledger-onto-pigrocrm-design.md` §7.

`Cost` (`timetracking/models.py`) is structurally the opposite concept, by its own
docstring: "money that actually left, towards somebody else, with a receipt to
prove it" is the freelancer's own P&L outflow, and it has no `invoice_line_id` at
all -- there is no path from a `Cost` row onto an invoice, because none was ever
meant to exist. `CostCategory` is genuinely reusable for a rebillable expense's own
category (categorising an outflow is the same problem either way), so
`contract_expenses` reuses it directly rather than inventing a parallel taxonomy.

**`politica_spese`'s shape**, decided here -- spec §7's own permission ("not deeply
validated [on `contracts`] -- the rebillable-expenses issue is what reads and
enforces its shape"): a tagged JSONB union keyed by `kind`, English, matching the
convention this schema already uses for a JSONB *discriminator* key
(`work_unit_transitions.attore`: `{"kind": "human", ...}`; `approvals.origine`:
`{"kind": "manuale"}`) rather than the Italian `tipo` a plain top-level *column*
would carry -- with Italian *values*, since those name a business kind a person
reads, not a protocol tag:

    {"kind": "non_rimborsabile"}
    {"kind": "rimborsabile", "richiede_preautorizzazione": true}
    {"kind": "rimborsabile_con_tetto", "importo_tetto": "500.00",
     "richiede_preautorizzazione": false}

mastro's own three-variant `ExpensePolicy` (`contract.ts:96-99`) is
`not_reimbursed` / `reimbursed_at_cost` / `reimbursed_with_cap`, translated one for
one. mastro's own `requiresExpensePreAuthorisation` is a *separate* boolean column
on its `contract` (`contract.ts:225-227`) -- PigroCRM's own `contracts` table (spec
§3) has no second column for it, so it is folded into this same JSONB as
`richiede_preautorizzazione` instead: the two facts mastro's own trigger reads
(`0025_expense_and_clause_note_constraints.sql::expense_set_reimbursable`) both
live in `politica_spese` here, never split across a column the signed-off
`contracts` table does not have. Absent, it defaults to `false` (mastro's own
column default) -- `triggers.py` reads it with `COALESCE`; `"non_rimborsabile"`
needs it not at all, since nothing is ever reimbursed regardless of
pre-authorisation.

`importo_tetto` is stored but not read by the reimbursable trigger, mirroring
mastro's own `expense_set_reimbursable`, which likewise reads only `policy_kind` --
the cap constrains how much of an expense a human rebills at invoicing time, not
whether it counts as reimbursable at all. Enforcing the cap is not part of this
issue's Done-when and is left for whoever builds the review screen that reads it.

Two existing fixtures elsewhere in this codebase (REB-358's own tests) already
write `politica_spese = {"tipo": "non_rimborsabile"}` -- written before this field
was interpreted by anything, per the spec's own "not deeply validated" note. They
are untouched here: none of them ever creates a `contract_expenses` row, so the key
mismatch is silent exactly where it is harmless, and rewriting unrelated tests is
outside this issue's scope.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Numeric, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin


class ContractExpense(Base, PrimaryKeyMixin, TimestampMixin):
    """One client-rebillable expense against a contract -- mastro's `expense`
    (`expense.ts:34-49`), spec §7.

    No `SoftDeleteMixin`: the spec's own column table carries no `deleted_at`, and
    mastro's own schema has none either -- a wrong entry is corrected in place
    (`update`), not withdrawn the way a `work_unit` is revoked.
    """

    __tablename__ = "contract_expenses"

    contract_id: Mapped[UUID] = mapped_column(
        ForeignKey("contracts.id"), nullable=False, index=True
    )
    # Reuses `CostCategory` directly (spec §7): same table, same admin UI, same code
    # list a freelancer already maintains for `Cost`.
    category_id: Mapped[UUID] = mapped_column(
        ForeignKey("cost_categories.id"), nullable=False, index=True
    )
    data: Mapped[date] = mapped_column(Date, nullable=False)
    importo: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    descrizione: Mapped[str] = mapped_column(Text, nullable=False)
    pre_autorizzata: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # mastro's `authorisationReference` -- freeform evidence, not a foreign key, the
    # same lighter-weight treatment as any other written proof this schema does not
    # elevate to its own table (spec §7's own note on `approvals.document_id` being
    # stricter because a day's approval is a heavier fact).
    riferimento_autorizzazione: Mapped[str | None] = mapped_column(Text, default=None)
    # Never set by application code -- `contract_expense_set_rimborsabile`
    # (triggers.py) computes it from `pre_autorizzata` against the owning
    # contract's `politica_spese` on every insert or update (spec §7): an expense
    # that fails the check is still recorded, flagged, never silently accepted or
    # rejected outright, the same non-blocking philosophy as
    # `lavorato_senza_approvazione` (spec §5).
    rimborsabile: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # `SET NULL`, the same divergence from mastro's own `RESTRICT`, and the same
    # reason, as `work_units.invoice_line_id` (spec §5, §7): so
    # `InvoiceService.replace_lines` can rebuild a draft's line list wholesale
    # without leaving orphaned rows.
    invoice_line_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("invoice_lines.id", ondelete="SET NULL"), default=None, index=True
    )
    # The receipt -- reuses PigroCRM's own document store exactly as
    # `Cost.document_id` already does, no explicit `ondelete` (RESTRICT, the
    # schema-wide default), matching `Cost.document_id` exactly.
    document_id: Mapped[UUID | None] = mapped_column(ForeignKey("documents.id"), default=None)

    __table_args__ = (
        # mastro's own `expense_amount_positive`.
        CheckConstraint("importo > 0", name="ck_contract_expenses_importo_positive"),
        # mastro's own `expense_authorisation_reference_matches_pre_authorised`
        # (`0025_expense_and_clause_note_constraints.sql`): the written proof is
        # required exactly when the box is checked, and forbidden -- never a stale
        # leftover from before it was unchecked -- otherwise.
        CheckConstraint(
            "(pre_autorizzata AND riferimento_autorizzazione IS NOT NULL) "
            "OR (NOT pre_autorizzata AND riferimento_autorizzazione IS NULL)",
            name="ck_contract_expenses_riferimento_matches_pre_autorizzata",
        ),
    )
