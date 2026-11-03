"""indexes for the lookups that scanned: addresses by lower(email), invoice dates, links

Revision ID: 0067
Revises: 0066

PCORE-B-18, PCORE-B-23 and PCORE-A-11.4.

- `people` and `customers` by `lower(email)`, partial on live rows: the Gmail roster
  resolves an address with `lower(email) = :needle`, which the plain `ix_*_email` btree
  cannot serve, so every address of every message was a sequential scan.
- `invoices.data_emissione`, `data_scadenza`, `stato_pagamento`, `pdf_document_id` and
  `xml_document_id`: the dashboards, the year CSV and the receivables filter on the dates
  and the payment state; `tipo_fattura_del_pdf(document_id)` runs on every non-PDF upload
  and filters on the PDF column.
- Foreign keys that queries filter on: `attivita.person_id` and `attivita.invoice_id` (the
  list's filters and the reminder engine), `deals.owner_id` (the automations and the
  reminder detectors) and `payment_reminders.email_draft_id` (the draft's reminder).
  Foreign keys nothing filters on (`time_timers.deal_id`, `notes.autore_id`,
  `note_revisions.edited_by_id`, `user_aziende.azienda_id`) get none: only an `ON DELETE`
  check would use them, and those rows are soft-deleted or never deleted.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0067"
down_revision: str | Sequence[str] | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOWER_EMAIL = (
    ("ix_people_email_lower", "people"),
    ("ix_customers_email_lower", "customers"),
)
PLAIN = (
    ("ix_invoices_data_emissione", "invoices", "data_emissione"),
    ("ix_invoices_data_scadenza", "invoices", "data_scadenza"),
    ("ix_invoices_stato_pagamento", "invoices", "stato_pagamento"),
    ("ix_invoices_pdf_document_id", "invoices", "pdf_document_id"),
    ("ix_invoices_xml_document_id", "invoices", "xml_document_id"),
    ("ix_attivita_person_id", "attivita", "person_id"),
    ("ix_attivita_invoice_id", "attivita", "invoice_id"),
    ("ix_deals_owner_id", "deals", "owner_id"),
    ("ix_payment_reminders_email_draft_id", "payment_reminders", "email_draft_id"),
)


def upgrade() -> None:
    for name, table in LOWER_EMAIL:
        op.execute(f"CREATE INDEX {name} ON {table} (lower(email)) WHERE deleted_at IS NULL")
    for name, table, column in PLAIN:
        op.create_index(name, table, [column])


def downgrade() -> None:
    for name, table, _ in reversed(PLAIN):
        op.drop_index(name, table_name=table)
    for name, _ in reversed(LOWER_EMAIL):
        op.execute(f"DROP INDEX {name}")
