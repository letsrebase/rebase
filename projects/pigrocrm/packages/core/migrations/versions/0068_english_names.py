# ruff: noqa: E501  (the generated CHECK and function texts are what Postgres prints)
"""the English rename: tables, columns, constraints, indexes, vocabulary, JSON and RLS names

Revision ID: 0068
Revises: 0067

REB-898. Every Italian identifier of the schema becomes English, in one transaction:

- the policies, the triggers and the 19 functions that name the old tables, columns and
  values are dropped first, and RLS is switched off for the rewrite (`FORCE` binds the owner,
  and with no policy a table answers no rows, so a data step would touch nothing);
- the 46 CHECK constraints and the two partial indexes whose text carries a renamed value are
  dropped, to be created again with the English text once the rows are rewritten;
- tables, columns, constraints, indexes and the proforma sequence are renamed
  (`emitter_profile` -> `legal_entities`, `user_aziende` -> `user_legal_entities`,
  `attivita` -> `tasks`);
- the persisted vocabulary is rewritten per column (`stato` words, types, channels, codes),
  and so is the JSON the application reads back with a closed schema (invoice snapshots,
  proposal fields, expense policies, approval origins, transition actors, declared
  variables, attachments) or by key (the timeline's `kind`, `entity_type` and `payload`);
- the literal server defaults, the CHECKs and the two indexes are rebuilt, and the functions,
  policies and triggers come back under their English names with the GUC
  `pigrocrm.legal_entities`; `EXECUTE` on the four definer functions goes to the
  application role again.

`downgrade` reverses each step: the Italian DDL of the revisions 0042, 0043, 0048, 0049,
0056, 0060, 0064 and 0066 is loaded from those files, which this revision never edits.
Rows whose JSON or value was written after the upgrade with a word the old code had no name
for are left as they are. `statements()` is what `projects/pigrocrm/conftest.py` applies to
the template database after `create_all`.
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

from pigrocrm.core.config import get_settings

revision: str = "0068"
down_revision: str | Sequence[str] | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_REVISIONS = [
    "0048_azienda_scope.py",
    "0049_azienda_scope_fast_path.py",
    "0056_contract_email_templates.py",
    "0060_clause_notes.py",
    "0064_notes_thread.py",
    "0066_definer_functions_search_path.py",
]

TABLE_RENAMES = [
    ("emitter_profile", "legal_entities"),
    ("user_aziende", "user_legal_entities"),
    ("attivita", "tasks"),
]

COLUMN_RENAMES = {
    "user_legal_entities": {"azienda_id": "legal_entity_id"},
    "proposals": {
        "campi_proposti": "proposed_fields",
        "estratto": "excerpt",
        "tipo_estratto": "excerpt_type",
        "confidenza": "confidence",
        "motivo_confidenza": "confidence_reason",
        "stato": "status",
        "campi_accettati": "accepted_fields",
        "id_risultato": "result_id",
        "deciso_da": "decided_by",
        "deciso_il": "decided_at",
    },
    "invoice_lines": {
        "numero_linea": "line_number",
        "descrizione": "description",
        "quantita": "quantity",
        "unita_misura": "unit_of_measure",
        "prezzo_unitario": "unit_price",
        "sconto_percentuale": "discount_percentage",
        "sconto_importo": "discount_amount",
        "prezzo_totale": "total_price",
        "aliquota_iva": "vat_rate",
        "natura": "vat_nature",
        "riferimento_normativo": "legal_reference",
    },
    "customers": {
        "ragione_sociale": "legal_name",
        "partita_iva": "vat_number",
        "codice_fiscale": "tax_code",
        "codice_sdi": "sdi_code",
        "pec": "certified_email",
        "indirizzo": "address",
        "comune": "city",
        "provincia": "province",
        "nazione": "country",
        "telefono": "phone",
        "sito_web": "website",
        "stato": "status",
        "giorni_pagamento": "payment_days",
        "pagamento_fine_mese": "payment_end_of_month",
        "azienda_id": "legal_entity_id",
    },
    "contract_email_templates": {
        "nome": "name",
        "oggetto": "subject",
        "corpo": "body",
        "allegati": "attachments",
        "attivo": "active",
    },
    "contracts": {
        "titolo": "title",
        "inizio": "start",
        "fine": "end",
        "tipo_rinnovo": "renewal_type",
        "preavviso_rinnovo_giorni": "renewal_notice_days",
        "preavviso_disdetta_giorni": "termination_notice_days",
        "giorni_pagamento": "payment_days",
        "pagamento_fine_mese": "payment_end_of_month",
        "cadenza_fatturazione": "billing_cadence",
        "divisa": "currency",
        "politica_spese": "expense_policy",
        "stato": "status",
        "contratto_precedente_id": "previous_contract_id",
        "azienda_id": "legal_entity_id",
        "tetto_quota_cliente": "customer_share_cap",
    },
    "rate_cards": {
        "valido_da": "valid_from",
        "valido_a": "valid_to",
        "tipo": "type",
        "importo": "amount",
        "unita": "unit",
        "frazioni_ammesse": "allowed_fractions",
        "ore_minime": "minimum_hours",
        "periodo_erogazione": "delivery_period",
    },
    "time_timers": {"descrizione": "description", "fatturabile": "billable"},
    "invoice_counters": {
        "anno": "year",
        "ultimo_numero": "last_number",
        "azienda_id": "legal_entity_id",
    },
    "gmail_messages": {"body_html_scartato": "body_html_discarded"},
    "time_entries": {
        "data": "date",
        "ore": "hours",
        "descrizione": "description",
        "fatturabile": "billable",
        "tariffa_applicata": "applied_rate",
        "costo_applicato": "applied_cost",
        "tariffa_origine": "rate_source",
        "costo_origine": "cost_source",
        "note_interne": "internal_notes",
    },
    "deals": {
        "nome": "name",
        "valore_previsto": "expected_value",
        "probabilita": "probability",
        "data_chiusura_prevista": "expected_close_date",
        "ore_preventivate": "budgeted_hours",
        "valore_preventivato": "budgeted_value",
        "tariffa_oraria": "hourly_rate",
        "chiuso_il": "closed_at",
        "azienda_id": "legal_entity_id",
    },
    "renewal_assumptions": {
        "probabilita": "probability",
        "volume_atteso": "expected_volume",
        "orizzonte_al": "horizon_until",
    },
    "cost_categories": {"nome": "name", "posizione": "position", "archiviata": "archived"},
    "documents": {
        "tipo": "type",
        "titolo": "title",
        "stato": "status",
        "versione_corrente": "current_version",
        "stato_dal": "status_since",
        "azienda_id": "legal_entity_id",
    },
    "notes": {
        "testo": "text",
        "autore_id": "author_id",
        "autore": "author",
        "fonte": "source",
        "modificata_at": "edited_at",
        "modificata_da_id": "edited_by_id",
    },
    "work_units": {
        "data": "date",
        "quantita": "quantity",
        "descrizione": "description",
        "stato": "status",
    },
    "period_locks": {
        "anno": "year",
        "mese": "month",
        "chiuso_il": "closed_at",
        "chiuso_da": "closed_by",
    },
    "contract_expenses": {
        "data": "date",
        "importo": "amount",
        "descrizione": "description",
        "pre_autorizzata": "pre_authorized",
        "riferimento_autorizzazione": "authorization_reference",
        "rimborsabile": "reimbursable",
    },
    "pipeline_stages": {
        "nome": "name",
        "posizione": "position",
        "probabilita_default": "default_probability",
        "tipo": "type",
    },
    "users": {
        "nome": "name",
        "ruolo": "role",
        "attivo": "active",
        "tariffa_oraria_default": "default_hourly_rate",
        "costo_orario_default": "default_hourly_cost",
        "email_verificata_il": "email_verified_at",
        "digest_settimanale": "weekly_digest",
        "ambito_limitato": "limited_scope",
    },
    "invitations": {"nome": "name", "ruolo": "role", "aziende": "legal_entities"},
    "clause_notes": {
        "riferimento_clausola": "clause_reference",
        "testo_letterale": "literal_text",
        "interpretazione_adottata": "adopted_interpretation",
    },
    "personal_access_tokens": {"nome": "name"},
    "templates": {
        "nome": "name",
        "tipo": "type",
        "corpo_markdown": "body_markdown",
        "variabili_dichiarate": "declared_variables",
        "attivo": "active",
    },
    "note_revisions": {"testo": "text", "fonte": "source"},
    "people": {"nome": "first_name", "cognome": "last_name", "telefono": "phone", "ruolo": "role"},
    "work_unit_transitions": {
        "stato_precedente": "previous_status",
        "stato_nuovo": "new_status",
        "attore": "actor",
        "motivo": "reason",
    },
    "invoice_register_gaps": {
        "anno": "year",
        "numero": "number",
        "motivo": "reason",
        "dichiarato_da": "declared_by",
        "dichiarato_il": "declared_at",
        "azienda_id": "legal_entity_id",
    },
    "automation_config": {
        "a1_offerta_accettata_vince_deal": "a1_wins_accepted_offer_deal",
        "a2_offerta_inviata_avanza_deal": "a2_advances_sent_offer_deal",
        "a3_offerta_inviata_richiama": "a3_sent_offer_follow_up",
        "a4_deal_fermo": "a4_stalled_deal",
        "a5_fattura_scaduta": "a5_overdue_invoice",
        "a4_deal_fermo_giorni": "a4_stalled_deal_days",
    },
    "document_versions": {
        "numero": "number",
        "sorgente_markdown": "markdown_source",
        "variabili": "variables",
        "dimensione": "size",
        "creato_da": "created_by",
    },
    "legal_entities": {
        "ragione_sociale": "legal_name",
        "partita_iva": "vat_number",
        "codice_fiscale": "tax_code",
        "indirizzo": "address",
        "comune": "city",
        "provincia": "province",
        "nazione": "country",
        "pec": "certified_email",
        "codice_sdi": "sdi_code",
        "telefono": "phone",
        "sito_web": "website",
        "firma_key": "signature_key",
        "regime_fiscale": "tax_regime",
        "firma_email": "email_signature",
        "nome": "name",
        "predefinita": "is_default",
        "attiva": "active",
    },
    "digests": {"settimana": "week", "inviato_a": "sent_to"},
    "costs": {
        "data": "date",
        "importo": "amount",
        "descrizione": "description",
        "fornitore": "supplier",
        "azienda_id": "legal_entity_id",
    },
    "approvals": {
        "canale": "channel",
        "mittente": "sender",
        "ricevuto_il": "received_at",
        "estratto": "excerpt",
        "origine": "origin",
    },
    "tasks": {
        "titolo": "title",
        "scadenza": "due_date",
        "stato": "status",
        "completata_il": "completed_at",
        "assegnata_a": "assigned_to",
        "origine": "origin",
        "regola": "rule",
    },
    "invoices": {
        "tipo": "type",
        "stato": "status",
        "anno": "year",
        "numero": "number",
        "riferimento": "reference",
        "data_emissione": "issue_date",
        "data_scadenza": "due_date",
        "tipo_documento": "sdi_document_type",
        "divisa": "currency",
        "imponibile": "taxable_amount",
        "imposta": "vat_amount",
        "bollo": "stamp_duty",
        "totale": "total",
        "causale": "memo",
        "snapshot_versione": "snapshot_version",
        "stato_pagamento": "payment_status",
        "data_incasso": "payment_date",
        "trasmessa_esternamente_il": "transmitted_externally_at",
        "origine_proforma_id": "origin_proforma_id",
        "annullata_il": "cancelled_at",
        "motivo_annullamento": "cancellation_reason",
        "importata_da": "imported_from",
        "competenza_da": "accrual_from",
        "competenza_a": "accrual_to",
        "azienda_id": "legal_entity_id",
        "ordine_numero": "purchase_order_number",
        "ordine_data": "purchase_order_date",
        "ordine_cig": "purchase_order_tender_code",
        "ordine_cup": "purchase_order_project_code",
        "note_interne": "internal_notes",
    },
    "fiscal_profile": {
        "codice_regime": "regime_code",
        "aliquota_iva_default": "default_vat_rate",
        "natura_default": "default_vat_nature",
        "riferimento_normativo": "legal_reference",
        "applica_bollo": "apply_stamp_duty",
        "soglia_bollo": "stamp_duty_threshold",
        "importo_bollo": "stamp_duty_amount",
        "condizioni_pagamento": "payment_terms",
        "modalita_pagamento": "payment_method",
        "giorni_scadenza": "due_days",
        "coefficiente_redditivita": "profitability_coefficient",
        "aliquota_imposta_sostitutiva": "substitute_tax_rate",
        "aliquota_inps": "social_security_rate",
        "azienda_id": "legal_entity_id",
    },
}

COLUMNS_BY_OLD_TABLE = {
    "user_aziende": {"azienda_id": "legal_entity_id"},
    "proposals": {
        "campi_proposti": "proposed_fields",
        "estratto": "excerpt",
        "tipo_estratto": "excerpt_type",
        "confidenza": "confidence",
        "motivo_confidenza": "confidence_reason",
        "stato": "status",
        "campi_accettati": "accepted_fields",
        "id_risultato": "result_id",
        "deciso_da": "decided_by",
        "deciso_il": "decided_at",
    },
    "invoice_lines": {
        "numero_linea": "line_number",
        "descrizione": "description",
        "quantita": "quantity",
        "unita_misura": "unit_of_measure",
        "prezzo_unitario": "unit_price",
        "sconto_percentuale": "discount_percentage",
        "sconto_importo": "discount_amount",
        "prezzo_totale": "total_price",
        "aliquota_iva": "vat_rate",
        "natura": "vat_nature",
        "riferimento_normativo": "legal_reference",
    },
    "customers": {
        "ragione_sociale": "legal_name",
        "partita_iva": "vat_number",
        "codice_fiscale": "tax_code",
        "codice_sdi": "sdi_code",
        "pec": "certified_email",
        "indirizzo": "address",
        "comune": "city",
        "provincia": "province",
        "nazione": "country",
        "telefono": "phone",
        "sito_web": "website",
        "stato": "status",
        "giorni_pagamento": "payment_days",
        "pagamento_fine_mese": "payment_end_of_month",
        "azienda_id": "legal_entity_id",
    },
    "contract_email_templates": {
        "nome": "name",
        "oggetto": "subject",
        "corpo": "body",
        "allegati": "attachments",
        "attivo": "active",
    },
    "contracts": {
        "titolo": "title",
        "inizio": "start",
        "fine": "end",
        "tipo_rinnovo": "renewal_type",
        "preavviso_rinnovo_giorni": "renewal_notice_days",
        "preavviso_disdetta_giorni": "termination_notice_days",
        "giorni_pagamento": "payment_days",
        "pagamento_fine_mese": "payment_end_of_month",
        "cadenza_fatturazione": "billing_cadence",
        "divisa": "currency",
        "politica_spese": "expense_policy",
        "stato": "status",
        "contratto_precedente_id": "previous_contract_id",
        "azienda_id": "legal_entity_id",
        "tetto_quota_cliente": "customer_share_cap",
    },
    "rate_cards": {
        "valido_da": "valid_from",
        "valido_a": "valid_to",
        "tipo": "type",
        "importo": "amount",
        "unita": "unit",
        "frazioni_ammesse": "allowed_fractions",
        "ore_minime": "minimum_hours",
        "periodo_erogazione": "delivery_period",
    },
    "time_timers": {"descrizione": "description", "fatturabile": "billable"},
    "invoice_counters": {
        "anno": "year",
        "ultimo_numero": "last_number",
        "azienda_id": "legal_entity_id",
    },
    "gmail_messages": {"body_html_scartato": "body_html_discarded"},
    "time_entries": {
        "data": "date",
        "ore": "hours",
        "descrizione": "description",
        "fatturabile": "billable",
        "tariffa_applicata": "applied_rate",
        "costo_applicato": "applied_cost",
        "tariffa_origine": "rate_source",
        "costo_origine": "cost_source",
        "note_interne": "internal_notes",
    },
    "deals": {
        "nome": "name",
        "valore_previsto": "expected_value",
        "probabilita": "probability",
        "data_chiusura_prevista": "expected_close_date",
        "ore_preventivate": "budgeted_hours",
        "valore_preventivato": "budgeted_value",
        "tariffa_oraria": "hourly_rate",
        "chiuso_il": "closed_at",
        "azienda_id": "legal_entity_id",
    },
    "renewal_assumptions": {
        "probabilita": "probability",
        "volume_atteso": "expected_volume",
        "orizzonte_al": "horizon_until",
    },
    "cost_categories": {"nome": "name", "posizione": "position", "archiviata": "archived"},
    "documents": {
        "tipo": "type",
        "titolo": "title",
        "stato": "status",
        "versione_corrente": "current_version",
        "stato_dal": "status_since",
        "azienda_id": "legal_entity_id",
    },
    "notes": {
        "testo": "text",
        "autore_id": "author_id",
        "autore": "author",
        "fonte": "source",
        "modificata_at": "edited_at",
        "modificata_da_id": "edited_by_id",
    },
    "work_units": {
        "data": "date",
        "quantita": "quantity",
        "descrizione": "description",
        "stato": "status",
    },
    "period_locks": {
        "anno": "year",
        "mese": "month",
        "chiuso_il": "closed_at",
        "chiuso_da": "closed_by",
    },
    "contract_expenses": {
        "data": "date",
        "importo": "amount",
        "descrizione": "description",
        "pre_autorizzata": "pre_authorized",
        "riferimento_autorizzazione": "authorization_reference",
        "rimborsabile": "reimbursable",
    },
    "pipeline_stages": {
        "nome": "name",
        "posizione": "position",
        "probabilita_default": "default_probability",
        "tipo": "type",
    },
    "users": {
        "nome": "name",
        "ruolo": "role",
        "attivo": "active",
        "tariffa_oraria_default": "default_hourly_rate",
        "costo_orario_default": "default_hourly_cost",
        "email_verificata_il": "email_verified_at",
        "digest_settimanale": "weekly_digest",
        "ambito_limitato": "limited_scope",
    },
    "invitations": {"nome": "name", "ruolo": "role", "aziende": "legal_entities"},
    "clause_notes": {
        "riferimento_clausola": "clause_reference",
        "testo_letterale": "literal_text",
        "interpretazione_adottata": "adopted_interpretation",
    },
    "personal_access_tokens": {"nome": "name"},
    "templates": {
        "nome": "name",
        "tipo": "type",
        "corpo_markdown": "body_markdown",
        "variabili_dichiarate": "declared_variables",
        "attivo": "active",
    },
    "note_revisions": {"testo": "text", "fonte": "source"},
    "people": {"nome": "first_name", "cognome": "last_name", "telefono": "phone", "ruolo": "role"},
    "work_unit_transitions": {
        "stato_precedente": "previous_status",
        "stato_nuovo": "new_status",
        "attore": "actor",
        "motivo": "reason",
    },
    "invoice_register_gaps": {
        "anno": "year",
        "numero": "number",
        "motivo": "reason",
        "dichiarato_da": "declared_by",
        "dichiarato_il": "declared_at",
        "azienda_id": "legal_entity_id",
    },
    "automation_config": {
        "a1_offerta_accettata_vince_deal": "a1_wins_accepted_offer_deal",
        "a2_offerta_inviata_avanza_deal": "a2_advances_sent_offer_deal",
        "a3_offerta_inviata_richiama": "a3_sent_offer_follow_up",
        "a4_deal_fermo": "a4_stalled_deal",
        "a5_fattura_scaduta": "a5_overdue_invoice",
        "a4_deal_fermo_giorni": "a4_stalled_deal_days",
    },
    "document_versions": {
        "numero": "number",
        "sorgente_markdown": "markdown_source",
        "variabili": "variables",
        "dimensione": "size",
        "creato_da": "created_by",
    },
    "emitter_profile": {
        "ragione_sociale": "legal_name",
        "partita_iva": "vat_number",
        "codice_fiscale": "tax_code",
        "indirizzo": "address",
        "comune": "city",
        "provincia": "province",
        "nazione": "country",
        "pec": "certified_email",
        "codice_sdi": "sdi_code",
        "telefono": "phone",
        "sito_web": "website",
        "firma_key": "signature_key",
        "regime_fiscale": "tax_regime",
        "firma_email": "email_signature",
        "nome": "name",
        "predefinita": "is_default",
        "attiva": "active",
    },
    "digests": {"settimana": "week", "inviato_a": "sent_to"},
    "costs": {
        "data": "date",
        "importo": "amount",
        "descrizione": "description",
        "fornitore": "supplier",
        "azienda_id": "legal_entity_id",
    },
    "approvals": {
        "canale": "channel",
        "mittente": "sender",
        "ricevuto_il": "received_at",
        "estratto": "excerpt",
        "origine": "origin",
    },
    "attivita": {
        "titolo": "title",
        "scadenza": "due_date",
        "stato": "status",
        "completata_il": "completed_at",
        "assegnata_a": "assigned_to",
        "origine": "origin",
        "regola": "rule",
    },
    "invoices": {
        "tipo": "type",
        "stato": "status",
        "anno": "year",
        "numero": "number",
        "riferimento": "reference",
        "data_emissione": "issue_date",
        "data_scadenza": "due_date",
        "tipo_documento": "sdi_document_type",
        "divisa": "currency",
        "imponibile": "taxable_amount",
        "imposta": "vat_amount",
        "bollo": "stamp_duty",
        "totale": "total",
        "causale": "memo",
        "snapshot_versione": "snapshot_version",
        "stato_pagamento": "payment_status",
        "data_incasso": "payment_date",
        "trasmessa_esternamente_il": "transmitted_externally_at",
        "origine_proforma_id": "origin_proforma_id",
        "annullata_il": "cancelled_at",
        "motivo_annullamento": "cancellation_reason",
        "importata_da": "imported_from",
        "competenza_da": "accrual_from",
        "competenza_a": "accrual_to",
        "azienda_id": "legal_entity_id",
        "ordine_numero": "purchase_order_number",
        "ordine_data": "purchase_order_date",
        "ordine_cig": "purchase_order_tender_code",
        "ordine_cup": "purchase_order_project_code",
        "note_interne": "internal_notes",
    },
    "fiscal_profile": {
        "codice_regime": "regime_code",
        "aliquota_iva_default": "default_vat_rate",
        "natura_default": "default_vat_nature",
        "riferimento_normativo": "legal_reference",
        "applica_bollo": "apply_stamp_duty",
        "soglia_bollo": "stamp_duty_threshold",
        "importo_bollo": "stamp_duty_amount",
        "condizioni_pagamento": "payment_terms",
        "modalita_pagamento": "payment_method",
        "giorni_scadenza": "due_days",
        "coefficiente_redditivita": "profitability_coefficient",
        "aliquota_imposta_sostitutiva": "substitute_tax_rate",
        "aliquota_inps": "social_security_rate",
        "azienda_id": "legal_entity_id",
    },
}

CONSTRAINT_RENAMES = [
    ("tasks", "attivita_assegnata_a_fkey", "tasks_assigned_to_fkey"),
    ("tasks", "attivita_customer_id_fkey", "tasks_customer_id_fkey"),
    ("tasks", "attivita_deal_id_fkey", "tasks_deal_id_fkey"),
    ("tasks", "attivita_invoice_id_fkey", "tasks_invoice_id_fkey"),
    ("tasks", "attivita_person_id_fkey", "tasks_person_id_fkey"),
    ("tasks", "attivita_pkey", "tasks_pkey"),
    ("contracts", "contracts_contratto_precedente_id_fkey", "contracts_previous_contract_id_fkey"),
    ("contracts", "contracts_contratto_precedente_id_key", "contracts_previous_contract_id_key"),
    ("contracts", "fk_contracts_azienda_id_emitter_profile", "contracts_legal_entity_id_fkey"),
    ("costs", "fk_costs_azienda_id_emitter_profile", "costs_legal_entity_id_fkey"),
    ("customers", "fk_customers_azienda_id_emitter_profile", "customers_legal_entity_id_fkey"),
    ("deals", "fk_deals_azienda_id_emitter_profile", "deals_legal_entity_id_fkey"),
    ("digests", "uq_digests_settimana", "digests_week_key"),
    ("document_versions", "document_versions_creato_da_fkey", "document_versions_created_by_fkey"),
    (
        "document_versions",
        "uq_document_versions_document_numero",
        "uq_document_versions_document_number",
    ),
    ("documents", "fk_documents_azienda_id_emitter_profile", "documents_legal_entity_id_fkey"),
    ("legal_entities", "emitter_profile_pkey", "legal_entities_pkey"),
    (
        "fiscal_profile",
        "fk_fiscal_profile_azienda_id_emitter_profile",
        "fiscal_profile_legal_entity_id_fkey",
    ),
    ("fiscal_profile", "uq_fiscal_profile_azienda_id", "fiscal_profile_legal_entity_id_key"),
    (
        "invoice_counters",
        "fk_invoice_counters_azienda_id_emitter_profile",
        "invoice_counters_legal_entity_id_fkey",
    ),
    ("invoice_lines", "uq_invoice_lines_invoice_numero", "uq_invoice_lines_invoice_number"),
    (
        "invoice_register_gaps",
        "fk_invoice_register_gaps_azienda_id_emitter_profile",
        "invoice_register_gaps_legal_entity_id_fkey",
    ),
    (
        "invoice_register_gaps",
        "invoice_register_gaps_dichiarato_da_fkey",
        "invoice_register_gaps_declared_by_fkey",
    ),
    (
        "invoice_register_gaps",
        "uq_invoice_register_gaps_azienda_anno_numero",
        "uq_invoice_register_gaps_legal_entity_year_number",
    ),
    ("invoices", "fk_invoices_azienda_id_emitter_profile", "invoices_legal_entity_id_fkey"),
    ("invoices", "invoices_origine_proforma_id_fkey", "invoices_origin_proforma_id_fkey"),
    ("notes", "notes_autore_id_fkey", "notes_author_id_fkey"),
    ("notes", "notes_modificata_da_id_fkey", "notes_edited_by_id_fkey"),
    ("period_locks", "period_locks_chiuso_da_fkey", "period_locks_closed_by_fkey"),
    (
        "user_legal_entities",
        "user_aziende_azienda_id_fkey",
        "user_legal_entities_legal_entity_id_fkey",
    ),
    ("user_legal_entities", "user_aziende_pkey", "user_legal_entities_pkey"),
    ("user_legal_entities", "user_aziende_user_id_fkey", "user_legal_entities_user_id_fkey"),
]

INDEX_RENAMES = [
    ("ix_attivita_assegnata", "ix_tasks_assigned"),
    ("ix_attivita_created_at_id", "ix_tasks_created_at_id"),
    ("ix_attivita_custom_fields", "ix_tasks_custom_fields"),
    ("ix_attivita_entity", "ix_tasks_entity"),
    ("ix_attivita_invoice_id", "ix_tasks_invoice_id"),
    ("ix_attivita_note_trgm", "ix_tasks_note_trgm"),
    ("ix_attivita_person_id", "ix_tasks_person_id"),
    ("ix_attivita_scadenza", "ix_tasks_due_date"),
    ("ix_attivita_scadenza_desc_id", "ix_tasks_due_date_desc_id"),
    ("ix_attivita_scadenza_id", "ix_tasks_due_date_id"),
    ("ix_attivita_titolo_trgm", "ix_tasks_title_trgm"),
    ("uq_contract_email_templates_nome", "uq_contract_email_templates_name"),
    ("ix_contracts_azienda_id", "ix_contracts_legal_entity_id"),
    ("ix_contracts_titolo_id", "ix_contracts_title_id"),
    ("uq_cost_categories_nome", "uq_cost_categories_name"),
    ("ix_costs_azienda_id", "ix_costs_legal_entity_id"),
    ("ix_costs_data", "ix_costs_date"),
    ("ix_costs_deal_data", "ix_costs_deal_date"),
    ("ix_customers_azienda_id", "ix_customers_legal_entity_id"),
    ("ix_customers_codice_fiscale_trgm", "ix_customers_tax_code_trgm"),
    ("ix_customers_partita_iva", "ix_customers_vat_number"),
    ("ix_customers_partita_iva_trgm", "ix_customers_vat_number_trgm"),
    ("ix_customers_ragione_sociale", "ix_customers_legal_name"),
    ("ix_customers_ragione_sociale_id", "ix_customers_legal_name_id"),
    ("ix_customers_ragione_sociale_trgm", "ix_customers_legal_name_trgm"),
    ("ix_deals_azienda_id", "ix_deals_legal_entity_id"),
    ("ix_deals_chiuso_il", "ix_deals_closed_at"),
    ("ix_deals_nome_id", "ix_deals_name_id"),
    ("ix_deals_nome_trgm", "ix_deals_name_trgm"),
    ("ix_documents_azienda_id", "ix_documents_legal_entity_id"),
    ("ix_documents_stato_dal", "ix_documents_status_since"),
    ("ix_documents_titolo_id", "ix_documents_title_id"),
    ("ix_documents_titolo_trgm", "ix_documents_title_trgm"),
    ("uq_emitter_profile_codice_fiscale", "uq_legal_entities_tax_code"),
    ("uq_emitter_profile_partita_iva", "uq_legal_entities_vat_number"),
    ("uq_emitter_profile_predefinita", "uq_legal_entities_default"),
    ("ix_invoice_register_gaps_azienda_id", "ix_invoice_register_gaps_legal_entity_id"),
    ("ix_invoices_anno_numero", "ix_invoices_year_number"),
    ("ix_invoices_azienda_id", "ix_invoices_legal_entity_id"),
    ("ix_invoices_causale_trgm", "ix_invoices_memo_trgm"),
    ("ix_invoices_data_emissione", "ix_invoices_issue_date"),
    ("ix_invoices_data_scadenza", "ix_invoices_due_date"),
    ("ix_invoices_stato_pagamento", "ix_invoices_payment_status"),
    ("uq_invoices_azienda_anno_numero", "uq_invoices_legal_entity_year_number"),
    ("ix_people_cognome_desc_id", "ix_people_last_name_desc_id"),
    ("ix_people_cognome_id", "ix_people_last_name_id"),
    ("ix_people_cognome_trgm", "ix_people_last_name_trgm"),
    ("ix_people_nome_trgm", "ix_people_name_trgm"),
    ("ix_proposals_stato_created_at", "ix_proposals_status_created_at"),
    ("uq_templates_nome", "uq_templates_name"),
    ("ix_time_entries_deal_data", "ix_time_entries_deal_date"),
    ("ix_time_entries_user_data", "ix_time_entries_user_date"),
]

SEQUENCE_RENAMES = [("proforma_riferimento_seq", "proforma_reference_seq")]

OLD_CHECKS = [
    (
        "approvals",
        "ck_approvals_canale",
        "CHECK (((canale)::text = ANY ((ARRAY['email'::character varying, "
        "'posta_certificata'::character varying, 'raccomandata'::character varying, "
        "'corriere'::character varying, 'altro'::character varying])::text[])))",
    ),
    (
        "attivita",
        "ck_attivita_completata_il",
        "CHECK ((((stato)::text = 'completata'::text) = (completata_il IS NOT NULL)))",
    ),
    (
        "attivita",
        "ck_attivita_origine",
        "CHECK (((origine)::text = ANY ((ARRAY['manuale'::character varying, "
        "'automazione'::character varying, 'sollecito'::character varying])::text[])))",
    ),
    (
        "attivita",
        "ck_attivita_regola_origine",
        "CHECK (((regola IS NULL) OR ((origine)::text = 'automazione'::text)))",
    ),
    (
        "attivita",
        "ck_attivita_stato",
        "CHECK (((stato)::text = ANY ((ARRAY['aperta'::character varying, 'completata'::character "
        "varying, 'annullata'::character varying])::text[])))",
    ),
    (
        "attivita",
        "ck_attivita_un_solo_riferimento",
        "CHECK (((((((customer_id IS NOT NULL))::integer + ((person_id IS NOT NULL))::integer) + "
        "((deal_id IS NOT NULL))::integer) + ((invoice_id IS NOT NULL))::integer) <= 1))",
    ),
    (
        "automation_config",
        "ck_automation_config_deal_fermo_giorni",
        "CHECK (((a4_deal_fermo_giorni >= 1) AND (a4_deal_fermo_giorni <= 365)))",
    ),
    (
        "contract_expenses",
        "ck_contract_expenses_importo_positive",
        "CHECK ((importo > (0)::numeric))",
    ),
    (
        "contract_expenses",
        "ck_contract_expenses_riferimento_matches_pre_autorizzata",
        "CHECK (((pre_autorizzata AND (riferimento_autorizzazione IS NOT NULL)) OR ((NOT "
        "pre_autorizzata) AND (riferimento_autorizzazione IS NULL))))",
    ),
    ("contracts", "ck_contracts_fine_ordered", "CHECK (((fine IS NULL) OR (inizio <= fine)))"),
    (
        "contracts",
        "ck_contracts_preavviso_rinnovo_required",
        "CHECK ((((tipo_rinnovo)::text = 'nessuno'::text) OR (preavviso_rinnovo_giorni IS NOT "
        "NULL)))",
    ),
    (
        "contracts",
        "ck_contracts_tetto_quota_cliente_range",
        "CHECK (((tetto_quota_cliente IS NULL) OR ((tetto_quota_cliente > (0)::numeric) AND "
        "(tetto_quota_cliente < (100)::numeric))))",
    ),
    (
        "contracts",
        "ck_contracts_tipo_rinnovo",
        "CHECK (((tipo_rinnovo)::text = ANY ((ARRAY['nessuno'::character varying, "
        "'esplicito'::character varying, 'opzione_controparte'::character varying, "
        "'tacito'::character varying])::text[])))",
    ),
    ("costs", "ck_costs_importo_non_zero", "CHECK ((importo <> (0)::numeric))"),
    (
        "emitter_profile",
        "ck_emitter_profile_default_active",
        "CHECK ((NOT (predefinita AND (NOT attiva))))",
    ),
    (
        "invoice_lines",
        "ck_invoice_lines_natura_agrees_with_rate",
        "CHECK (((aliquota_iva = (0)::numeric) = (natura IS NOT NULL)))",
    ),
    ("invoice_lines", "ck_invoice_lines_numero_positive", "CHECK ((numero_linea >= 1))"),
    ("invoice_register_gaps", "ck_invoice_register_gaps_numero_positive", "CHECK ((numero >= 1))"),
    ("invoices", "ck_invoices_anno_numero_together", "CHECK (((anno IS NULL) = (numero IS NULL)))"),
    (
        "invoices",
        "ck_invoices_annullamento_complete",
        "CHECK ((((annullata_il IS NULL) AND (motivo_annullamento IS NULL)) OR (((stato)::text = "
        "'annullata'::text) AND (annullata_il IS NOT NULL) AND (motivo_annullamento IS NOT NULL))))",
    ),
    (
        "invoices",
        "ck_invoices_competenza_ordered",
        "CHECK (((competenza_da IS NULL) OR (competenza_da <= competenza_a)))",
    ),
    (
        "invoices",
        "ck_invoices_competenza_together",
        "CHECK (((competenza_da IS NULL) = (competenza_a IS NULL)))",
    ),
    (
        "invoices",
        "ck_invoices_incasso_requires_state",
        "CHECK (((data_incasso IS NULL) OR ((stato_pagamento)::text = 'incassato'::text)))",
    ),
    (
        "invoices",
        "ck_invoices_no_delete_once_consumed",
        "CHECK (((deleted_at IS NULL) OR ((numero IS NULL) AND ((stato)::text <> "
        "'consumata'::text))))",
    ),
    ("invoices", "ck_invoices_numero_positive", "CHECK (((numero IS NULL) OR (numero > 0)))"),
    (
        "invoices",
        "ck_invoices_numero_requires_issued_fattura",
        "CHECK (((numero IS NULL) OR (((tipo)::text = 'fattura'::text) AND ((stato)::text <> "
        "'bozza'::text))))",
    ),
    (
        "invoices",
        "ck_invoices_ordine_needs_numero",
        "CHECK (((ordine_numero IS NOT NULL) OR ((ordine_data IS NULL) AND (ordine_cig IS NULL) AND "
        "(ordine_cup IS NULL))))",
    ),
    (
        "invoices",
        "ck_invoices_riferimento_only_on_proforma",
        "CHECK (((riferimento IS NULL) OR ((tipo)::text = 'proforma'::text)))",
    ),
    (
        "invoices",
        "ck_invoices_tipo_stato",
        "CHECK (((((tipo)::text = 'fattura'::text) AND ((stato)::text = ANY "
        "((ARRAY['bozza'::character varying, 'emessa'::character varying, 'annullata'::character "
        "varying])::text[]))) OR (((tipo)::text = 'proforma'::text) AND ((stato)::text = ANY "
        "((ARRAY['bozza'::character varying, 'confermata'::character varying, 'consumata'::character "
        "varying])::text[])))))",
    ),
    (
        "notes",
        "ck_notes_fonte",
        "CHECK (((fonte IS NULL) OR ((fonte)::text = ANY ((ARRAY['web'::character varying, "
        "'mcp'::character varying, 'sistema'::character varying, 'rebase'::character "
        "varying])::text[]))))",
    ),
    ("period_locks", "ck_period_locks_mese", "CHECK (((mese >= 1) AND (mese <= 12)))"),
    (
        "proposals",
        "ck_proposals_contract_id_required_unless_contratto",
        "CHECK ((((target_type)::text = 'contratto'::text) OR (contract_id IS NOT NULL)))",
    ),
    (
        "proposals",
        "ck_proposals_stato",
        "CHECK (((stato)::text = ANY ((ARRAY['in_attesa'::character varying, 'accettata'::character "
        "varying, 'rifiutata'::character varying])::text[])))",
    ),
    (
        "proposals",
        "ck_proposals_target_type",
        "CHECK (((target_type)::text = ANY ((ARRAY['contratto'::character varying, "
        "'giornata'::character varying])::text[])))",
    ),
    (
        "proposals",
        "ck_proposals_tipo_estratto",
        "CHECK (((tipo_estratto)::text = ANY ((ARRAY['citato'::character varying, "
        "'trascritto'::character varying])::text[])))",
    ),
    (
        "rate_cards",
        "ck_rate_cards_ore_minime_only_orario",
        "CHECK ((((tipo)::text = 'orario'::text) OR (ore_minime IS NULL)))",
    ),
    (
        "rate_cards",
        "ck_rate_cards_periodo_erogazione_values",
        "CHECK (((periodo_erogazione IS NULL) OR ((periodo_erogazione)::text = ANY "
        "((ARRAY['mensile'::character varying, 'trimestrale'::character varying, "
        "'annuale'::character varying, 'una_tantum'::character varying])::text[]))))",
    ),
    (
        "rate_cards",
        "ck_rate_cards_periodo_only_ricorrente_fisso",
        "CHECK ((((tipo)::text = 'ricorrente_fisso'::text) OR (periodo_erogazione IS NULL)))",
    ),
    (
        "rate_cards",
        "ck_rate_cards_tipo",
        "CHECK (((tipo)::text = ANY ((ARRAY['ricorrente_fisso'::character varying, "
        "'giornaliero'::character varying, 'orario'::character varying, 'una_tantum'::character "
        "varying])::text[])))",
    ),
    (
        "rate_cards",
        "ck_rate_cards_unita",
        "CHECK (((unita)::text = ANY ((ARRAY['ora'::character varying, 'giorno'::character varying, "
        "'mese'::character varying, 'anno'::character varying, 'forfait'::character "
        "varying])::text[])))",
    ),
    (
        "renewal_assumptions",
        "ck_renewal_assumptions_probabilita_range",
        "CHECK (((probabilita >= 0) AND (probabilita <= 100)))",
    ),
    (
        "renewal_assumptions",
        "ck_renewal_assumptions_volume_atteso_non_negative",
        "CHECK ((volume_atteso >= (0)::numeric))",
    ),
    (
        "time_entries",
        "ck_time_entries_costo_origine",
        "CHECK (((costo_origine)::text = ANY ((ARRAY['manuale'::character varying, "
        "'utente'::character varying, 'assente'::character varying])::text[])))",
    ),
    (
        "time_entries",
        "ck_time_entries_ore_range",
        "CHECK (((ore > (0)::numeric) AND (ore <= (24)::numeric)))",
    ),
    (
        "time_entries",
        "ck_time_entries_tariffa_origine",
        "CHECK (((tariffa_origine)::text = ANY ((ARRAY['manuale'::character varying, "
        "'deal'::character varying, 'utente'::character varying, 'assente'::character "
        "varying])::text[])))",
    ),
    (
        "work_units",
        "ck_work_units_stato",
        "CHECK (((stato)::text = ANY ((ARRAY['proposto'::character varying, 'approvato'::character "
        "varying, 'lavorato'::character varying, 'lavorato_senza_approvazione'::character varying, "
        "'fatturato'::character varying, 'pagato'::character varying, 'contestato'::character "
        "varying, 'revocato'::character varying, 'rifiutato'::character varying, "
        "'non_fatturabile'::character varying])::text[])))",
    ),
]

NEW_CHECKS = [
    (
        "approvals",
        "ck_approvals_channel",
        "CHECK (((channel)::text = ANY ((ARRAY['email'::character varying, "
        "'certified_mail'::character varying, 'registered_mail'::character varying, "
        "'courier'::character varying, 'other'::character varying])::text[])))",
    ),
    (
        "tasks",
        "ck_tasks_completed_at",
        "CHECK ((((status)::text = 'completed'::text) = (completed_at IS NOT NULL)))",
    ),
    (
        "tasks",
        "ck_tasks_origin",
        "CHECK (((origin)::text = ANY ((ARRAY['manual'::character varying, 'automation'::character "
        "varying, 'payment_reminder'::character varying])::text[])))",
    ),
    (
        "tasks",
        "ck_tasks_rule_origin",
        "CHECK (((rule IS NULL) OR ((origin)::text = 'automation'::text)))",
    ),
    (
        "tasks",
        "ck_tasks_status",
        "CHECK (((status)::text = ANY ((ARRAY['open'::character varying, 'completed'::character "
        "varying, 'cancelled'::character varying])::text[])))",
    ),
    (
        "tasks",
        "ck_tasks_single_reference",
        "CHECK (((((((customer_id IS NOT NULL))::integer + ((person_id IS NOT NULL))::integer) + "
        "((deal_id IS NOT NULL))::integer) + ((invoice_id IS NOT NULL))::integer) <= 1))",
    ),
    (
        "automation_config",
        "ck_automation_config_stalled_deal_days",
        "CHECK (((a4_stalled_deal_days >= 1) AND (a4_stalled_deal_days <= 365)))",
    ),
    (
        "contract_expenses",
        "ck_contract_expenses_amount_positive",
        "CHECK ((amount > (0)::numeric))",
    ),
    (
        "contract_expenses",
        "ck_contract_expenses_reference_matches_pre_authorized",
        "CHECK (((pre_authorized AND (authorization_reference IS NOT NULL)) OR ((NOT pre_authorized) "
        "AND (authorization_reference IS NULL))))",
    ),
    ("contracts", "ck_contracts_end_ordered", 'CHECK ((("end" IS NULL) OR (start <= "end")))'),
    (
        "contracts",
        "ck_contracts_notice_renewal_required",
        "CHECK ((((renewal_type)::text = 'none'::text) OR (renewal_notice_days IS NOT NULL)))",
    ),
    (
        "contracts",
        "ck_contracts_customer_share_cap_range",
        "CHECK (((customer_share_cap IS NULL) OR ((customer_share_cap > (0)::numeric) AND "
        "(customer_share_cap < (100)::numeric))))",
    ),
    (
        "contracts",
        "ck_contracts_renewal_type",
        "CHECK (((renewal_type)::text = ANY ((ARRAY['none'::character varying, 'explicit'::character "
        "varying, 'counterparty_option'::character varying, 'tacit'::character varying])::text[])))",
    ),
    ("costs", "ck_costs_amount_not_zero", "CHECK ((amount <> (0)::numeric))"),
    (
        "legal_entities",
        "ck_legal_entities_default_active",
        "CHECK ((NOT (is_default AND (NOT active))))",
    ),
    (
        "invoice_lines",
        "ck_invoice_lines_vat_nature_agrees_with_rate",
        "CHECK (((vat_rate = (0)::numeric) = (vat_nature IS NOT NULL)))",
    ),
    ("invoice_lines", "ck_invoice_lines_number_positive", "CHECK ((line_number >= 1))"),
    ("invoice_register_gaps", "ck_invoice_register_gaps_number_positive", "CHECK ((number >= 1))"),
    ("invoices", "ck_invoices_year_number_together", "CHECK (((year IS NULL) = (number IS NULL)))"),
    (
        "invoices",
        "ck_invoices_cancellation_complete",
        "CHECK ((((cancelled_at IS NULL) AND (cancellation_reason IS NULL)) OR (((status)::text = "
        "'cancelled'::text) AND (cancelled_at IS NOT NULL) AND (cancellation_reason IS NOT NULL))))",
    ),
    (
        "invoices",
        "ck_invoices_accrual_ordered",
        "CHECK (((accrual_from IS NULL) OR (accrual_from <= accrual_to)))",
    ),
    (
        "invoices",
        "ck_invoices_accrual_together",
        "CHECK (((accrual_from IS NULL) = (accrual_to IS NULL)))",
    ),
    (
        "invoices",
        "ck_invoices_collection_requires_state",
        "CHECK (((payment_date IS NULL) OR ((payment_status)::text = 'paid'::text)))",
    ),
    (
        "invoices",
        "ck_invoices_no_delete_once_consumed",
        "CHECK (((deleted_at IS NULL) OR ((number IS NULL) AND ((status)::text <> "
        "'consumed'::text))))",
    ),
    ("invoices", "ck_invoices_number_positive", "CHECK (((number IS NULL) OR (number > 0)))"),
    (
        "invoices",
        "ck_invoices_number_requires_issued_invoice",
        "CHECK (((number IS NULL) OR (((type)::text = 'invoice'::text) AND ((status)::text <> "
        "'draft'::text))))",
    ),
    (
        "invoices",
        "ck_invoices_order_needs_number",
        "CHECK (((purchase_order_number IS NOT NULL) OR ((purchase_order_date IS NULL) AND "
        "(purchase_order_tender_code IS NULL) AND (purchase_order_project_code IS NULL))))",
    ),
    (
        "invoices",
        "ck_invoices_reference_only_on_proforma",
        "CHECK (((reference IS NULL) OR ((type)::text = 'proforma'::text)))",
    ),
    (
        "invoices",
        "ck_invoices_type_status",
        "CHECK (((((type)::text = 'invoice'::text) AND ((status)::text = ANY "
        "((ARRAY['draft'::character varying, 'issued'::character varying, 'cancelled'::character "
        "varying])::text[]))) OR (((type)::text = 'proforma'::text) AND ((status)::text = ANY "
        "((ARRAY['draft'::character varying, 'confirmed'::character varying, 'consumed'::character "
        "varying])::text[])))))",
    ),
    (
        "notes",
        "ck_notes_source",
        "CHECK (((source IS NULL) OR ((source)::text = ANY ((ARRAY['web'::character varying, "
        "'mcp'::character varying, 'system'::character varying, 'rebase'::character "
        "varying])::text[]))))",
    ),
    ("period_locks", "ck_period_locks_month", "CHECK (((month >= 1) AND (month <= 12)))"),
    (
        "proposals",
        "ck_proposals_contract_id_required_unless_contract",
        "CHECK ((((target_type)::text = 'contract'::text) OR (contract_id IS NOT NULL)))",
    ),
    (
        "proposals",
        "ck_proposals_status",
        "CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'accepted'::character "
        "varying, 'rejected'::character varying])::text[])))",
    ),
    (
        "proposals",
        "ck_proposals_target_type",
        "CHECK (((target_type)::text = ANY ((ARRAY['contract'::character varying, "
        "'work_unit'::character varying])::text[])))",
    ),
    (
        "proposals",
        "ck_proposals_excerpt_type",
        "CHECK (((excerpt_type)::text = ANY ((ARRAY['quoted'::character varying, "
        "'transcribed'::character varying])::text[])))",
    ),
    (
        "rate_cards",
        "ck_rate_cards_minimum_hours_only_hourly",
        "CHECK ((((type)::text = 'hourly'::text) OR (minimum_hours IS NULL)))",
    ),
    (
        "rate_cards",
        "ck_rate_cards_delivery_period_values",
        "CHECK (((delivery_period IS NULL) OR ((delivery_period)::text = ANY "
        "((ARRAY['monthly'::character varying, 'quarterly'::character varying, 'annual'::character "
        "varying, 'one_off'::character varying])::text[]))))",
    ),
    (
        "rate_cards",
        "ck_rate_cards_period_only_fixed_recurring",
        "CHECK ((((type)::text = 'fixed_recurring'::text) OR (delivery_period IS NULL)))",
    ),
    (
        "rate_cards",
        "ck_rate_cards_type",
        "CHECK (((type)::text = ANY ((ARRAY['fixed_recurring'::character varying, 'daily'::character "
        "varying, 'hourly'::character varying, 'one_off'::character varying])::text[])))",
    ),
    (
        "rate_cards",
        "ck_rate_cards_unit",
        "CHECK (((unit)::text = ANY ((ARRAY['hour'::character varying, 'day'::character varying, "
        "'month'::character varying, 'year'::character varying, 'flat'::character "
        "varying])::text[])))",
    ),
    (
        "renewal_assumptions",
        "ck_renewal_assumptions_probability_range",
        "CHECK (((probability >= 0) AND (probability <= 100)))",
    ),
    (
        "renewal_assumptions",
        "ck_renewal_assumptions_expected_volume_not_negative",
        "CHECK ((expected_volume >= (0)::numeric))",
    ),
    (
        "time_entries",
        "ck_time_entries_cost_source",
        "CHECK (((cost_source)::text = ANY ((ARRAY['manual'::character varying, 'user'::character "
        "varying, 'absent'::character varying])::text[])))",
    ),
    (
        "time_entries",
        "ck_time_entries_hours_range",
        "CHECK (((hours > (0)::numeric) AND (hours <= (24)::numeric)))",
    ),
    (
        "time_entries",
        "ck_time_entries_rate_source",
        "CHECK (((rate_source)::text = ANY ((ARRAY['manual'::character varying, 'deal'::character "
        "varying, 'user'::character varying, 'absent'::character varying])::text[])))",
    ),
    (
        "work_units",
        "ck_work_units_status",
        "CHECK (((status)::text = ANY ((ARRAY['proposed'::character varying, 'approved'::character "
        "varying, 'worked'::character varying, 'worked_without_approval'::character varying, "
        "'invoiced'::character varying, 'paid'::character varying, 'disputed'::character varying, "
        "'revoked'::character varying, 'rejected'::character varying, 'non_billable'::character "
        "varying])::text[])))",
    ),
]

OLD_LITERAL_INDEXES = [
    (
        "attivita",
        "uq_attivita_regola_aperta",
        "CREATE UNIQUE INDEX uq_attivita_regola_aperta ON public.attivita USING btree (regola, "
        "COALESCE(customer_id, person_id, deal_id, invoice_id)) WHERE ((regola IS NOT NULL) AND "
        "((stato)::text = 'aperta'::text) AND (deleted_at IS NULL))",
    ),
    (
        "work_units",
        "uq_work_units_contract_data_live",
        "CREATE UNIQUE INDEX uq_work_units_contract_data_live ON public.work_units USING btree "
        "(contract_id, data) WHERE ((stato)::text <> ALL ((ARRAY['rifiutato'::character varying, "
        "'revocato'::character varying])::text[]))",
    ),
]

NEW_LITERAL_INDEXES = [
    (
        "tasks",
        "uq_tasks_rule_open",
        "CREATE UNIQUE INDEX uq_tasks_rule_open ON public.tasks USING btree (rule, "
        "COALESCE(customer_id, person_id, deal_id, invoice_id)) WHERE ((rule IS NOT NULL) AND "
        "((status)::text = 'open'::text) AND (deleted_at IS NULL))",
    ),
    (
        "work_units",
        "uq_work_units_contract_date_live",
        "CREATE UNIQUE INDEX uq_work_units_contract_date_live ON public.work_units USING btree "
        "(contract_id, date) WHERE ((status)::text <> ALL ((ARRAY['rejected'::character varying, "
        "'revoked'::character varying])::text[]))",
    ),
]

COLUMN_VALUES = {
    ("tasks", "status"): {"aperta": "open", "completata": "completed", "annullata": "cancelled"},
    ("tasks", "origin"): {
        "manuale": "manual",
        "automazione": "automation",
        "sollecito": "payment_reminder",
    },
    ("tasks", "rule"): {
        "offerta_inviata_richiama": "sent_offer_follow_up",
        "deal_fermo": "stalled_deal",
        "fattura_scaduta": "overdue_invoice",
    },
    ("approvals", "channel"): {
        "posta_certificata": "certified_mail",
        "raccomandata": "registered_mail",
        "corriere": "courier",
        "altro": "other",
    },
    ("contracts", "renewal_type"): {
        "nessuno": "none",
        "esplicito": "explicit",
        "opzione_controparte": "counterparty_option",
        "tacito": "tacit",
    },
    ("contracts", "status"): {
        "bozza": "draft",
        "attivo": "active",
        "terminato": "terminated",
        "scaduto": "expired",
    },
    ("rate_cards", "type"): {
        "ricorrente_fisso": "fixed_recurring",
        "giornaliero": "daily",
        "orario": "hourly",
        "una_tantum": "one_off",
    },
    ("rate_cards", "unit"): {
        "ora": "hour",
        "giorno": "day",
        "mese": "month",
        "anno": "year",
        "forfait": "flat",
    },
    ("rate_cards", "delivery_period"): {
        "mensile": "monthly",
        "trimestrale": "quarterly",
        "annuale": "annual",
        "una_tantum": "one_off",
    },
    ("documents", "type"): {
        "offerta": "offer",
        "contratto": "contract",
        "verbale": "minutes",
        "documento": "document",
        "fattura": "invoice",
        "fattura_xml": "invoice_xml",
        "rapporto_ore": "hours_report",
    },
    ("documents", "status"): {
        "bozza": "draft",
        "inviata": "sent",
        "accettata": "accepted",
        "rifiutata": "rejected",
    },
    ("templates", "type"): {
        "offerta": "offer",
        "contratto": "contract",
        "verbale": "minutes",
        "documento": "document",
        "rapporto_ore": "hours_report",
        "sollecito": "payment_reminder",
    },
    ("invoices", "type"): {"fattura": "invoice"},
    ("invoices", "status"): {
        "bozza": "draft",
        "emessa": "issued",
        "annullata": "cancelled",
        "confermata": "confirmed",
        "consumata": "consumed",
    },
    ("invoices", "payment_status"): {"da_incassare": "unpaid", "incassato": "paid"},
    ("invoices", "imported_from"): {"esterno": "external"},
    ("notes", "source"): {"sistema": "system"},
    ("note_revisions", "source"): {"sistema": "system"},
    ("proposals", "target_type"): {"contratto": "contract", "giornata": "work_unit"},
    ("proposals", "excerpt_type"): {"citato": "quoted", "trascritto": "transcribed"},
    ("proposals", "status"): {
        "in_attesa": "pending",
        "accettata": "accepted",
        "rifiutata": "rejected",
    },
    ("time_entries", "rate_source"): {"manuale": "manual", "utente": "user", "assente": "absent"},
    ("time_entries", "cost_source"): {"manuale": "manual", "utente": "user", "assente": "absent"},
    ("work_units", "status"): {
        "proposto": "proposed",
        "approvato": "approved",
        "lavorato": "worked",
        "lavorato_senza_approvazione": "worked_without_approval",
        "fatturato": "invoiced",
        "pagato": "paid",
        "contestato": "disputed",
        "revocato": "revoked",
        "rifiutato": "rejected",
        "non_fatturabile": "non_billable",
    },
    ("work_unit_transitions", "previous_status"): {
        "proposto": "proposed",
        "approvato": "approved",
        "lavorato": "worked",
        "lavorato_senza_approvazione": "worked_without_approval",
        "fatturato": "invoiced",
        "pagato": "paid",
        "contestato": "disputed",
        "revocato": "revoked",
        "rifiutato": "rejected",
        "non_fatturabile": "non_billable",
    },
    ("work_unit_transitions", "new_status"): {
        "proposto": "proposed",
        "approvato": "approved",
        "lavorato": "worked",
        "lavorato_senza_approvazione": "worked_without_approval",
        "fatturato": "invoiced",
        "pagato": "paid",
        "contestato": "disputed",
        "revocato": "revoked",
        "rifiutato": "rejected",
        "non_fatturabile": "non_billable",
    },
    ("users", "role"): {"collaboratore": "collaborator"},
    ("invitations", "role"): {"collaboratore": "collaborator"},
    ("pipeline_stages", "code"): {
        "contattato": "contacted",
        "negoziazione": "negotiation",
        "offerta": "offer",
        "perso": "lost",
        "vinto": "won",
    },
    ("cost_categories", "code"): {
        "consulenza_esterna": "external_consulting",
        "software_licenze": "software_licenses",
        "viaggi_trasferte": "travel",
        "materiali": "materials",
        "altro": "other",
    },
    ("email_drafts", "send_state"): {
        "bozza": "draft",
        "in_invio": "sending",
        "inviato": "sent",
        "incerto": "uncertain",
        "fallito": "failed",
    },
    ("space_settings", "key"): {
        "solleciti_grace_days": "payment_reminders_grace_days",
        "solleciti_min_interval_days": "payment_reminders_min_interval_days",
        "solleciti_max_reminders": "payment_reminders_max_reminders",
        "concentrazione_soglia_preferita": "preferred_concentration_threshold",
    },
    ("field_definitions", "entity_type"): {"attivita": "task"},
    ("reminder_preference", "type"): {
        "attivita_due": "task_due",
        "attivita_overdue": "task_overdue",
    },
    ("reminder_ack", "type"): {"attivita_due": "task_due", "attivita_overdue": "task_overdue"},
    ("reminder_snooze", "type"): {"attivita_due": "task_due", "attivita_overdue": "task_overdue"},
    ("reminder_delivery", "type"): {"attivita_due": "task_due", "attivita_overdue": "task_overdue"},
}

REMINDER_KEY_TABLES = ["reminder_ack", "reminder_snooze", "reminder_delivery"]

REMINDER_TYPES = {"attivita_due": "task_due", "attivita_overdue": "task_overdue"}

DEFAULTS = [
    ("tasks", "status", "'aperta'::character varying", "'open'::character varying"),
    ("tasks", "origin", "'manuale'::character varying", "'manual'::character varying"),
    ("contracts", "status", "'bozza'::character varying", "'draft'::character varying"),
    ("proposals", "excerpt_type", "'citato'::character varying", "'quoted'::character varying"),
    ("proposals", "status", "'in_attesa'::character varying", "'pending'::character varying"),
    ("work_units", "status", "'proposto'::character varying", "'proposed'::character varying"),
]

ACTIVITY_KINDS = [
    ("attivita", "task", "completata", "completed"),
    ("attivita", "task", "annullata", "cancelled"),
    ("attivita", "task", "riaperta", "reopened"),
    (None, None, "automazione.stage_spostato", "automation.stage_moved"),
    (None, None, "automazione.non_eseguita", "automation.not_executed"),
    (None, None, "automazione.configurazione_modificata", "automation.config_changed"),
    (None, None, "automazione.attivita_aperta", "automation.task_opened"),
    (None, None, "digest.inviato", "digest.sent"),
    (None, None, "document.importato", "document.imported"),
    (None, None, "drive.account_collegato", "drive.account_connected"),
    (None, None, "drive.account_scollegato", "drive.account_disconnected"),
    (None, None, "drive.credenziale_revocata", "drive.credential_revoked"),
    (None, None, "drive.radici_impostate", "drive.roots_set"),
    (None, None, "gmail.account_collegato", "gmail.account_connected"),
    (None, None, "gmail.account_scollegato", "gmail.account_disconnected"),
    (None, None, "gmail.backfill_eseguito", "gmail.backfill_run"),
    (None, None, "gmail.bozza_creata", "gmail.draft_created"),
    (None, None, "gmail.bozza_eliminata", "gmail.draft_deleted"),
    (None, None, "gmail.bozza_modificata", "gmail.draft_edited"),
    (None, None, "gmail.credenziale_revocata", "gmail.credential_revoked"),
    (None, None, "gmail.impostazioni_modificate", "gmail.settings_changed"),
    (None, None, "gmail.messaggio_inviato", "gmail.message_sent"),
    (None, None, "gmail.messaggio_ricevuto", "gmail.message_received"),
    (None, None, "gmail.sollecito_preparato", "gmail.payment_reminder_prepared"),
    (None, None, "gmail.sync_eseguito", "gmail.sync_run"),
    (None, None, "gmail.invio_incerto", "gmail.send_uncertain"),
    (None, None, "hub.richiesta_collegata", "hub.request_linked"),
    (None, None, "hub.richiesta_ricevuta", "hub.request_received"),
    (None, None, "slack.abbinamenti_rifatti", "slack.matches_rebuilt"),
    (None, None, "slack.abbinamento_manuale", "slack.manual_match"),
    (None, None, "slack.abbinamento_rimosso", "slack.match_removed"),
    (None, None, "slack.canale_impostato", "slack.channel_set"),
    (None, None, "slack.canale_rimosso", "slack.channel_removed"),
    (None, None, "slack.workspace_collegato", "slack.workspace_connected"),
    (None, None, "slack.workspace_scollegato", "slack.workspace_disconnected"),
]

ACTIVITY_ENTITIES = {"attivita": "task", "emitter_profile": "legal_entity"}

KIND_KEYS = {
    "note_added": {"nota_id": "note_id", "fonte": "source"},
    "note_edited": {"nota_id": "note_id", "fonte": "source"},
    "note_deleted": {"nota_id": "note_id", "fonte": "source"},
    "created": {
        "titolo": "title",
        "tipo": "type",
        "ore": "hours",
        "tariffa_applicata": "applied_rate",
        "tariffa_origine": "rate_source",
        "importo": "amount",
        "ruolo": "role",
        "righe": "lines",
        "totale": "total",
        "ragione_sociale": "legal_name",
        "rinnova": "renew",
        "scadenza": "due_date",
    },
    "version_added": {"numero": "number"},
    "document.importato": {"origine": "origin", "titolo": "title", "tipo": "type"},
    "state_changed": {"da": "from", "a": "to"},
    "archived": {"costi": "costs"},
    "time_bound_to_invoice": {"voci": "entries"},
    "closed": {"anno": "year", "mese": "month"},
    "reopened": {"anno": "year", "mese": "month"},
    "rates_updated": {"prima": "before", "dopo": "after"},
    "rate_updated": {"prima": "before", "dopo": "after"},
    "rates_recalculated": {
        "tariffa_prima": "rate_before",
        "tariffa_dopo": "rate_after",
        "costo_prima": "cost_before",
        "costo_dopo": "cost_after",
    },
    "rejected": {"deciso_da": "decided_by", "motivo": "reason"},
    "accepted": {"deciso_da": "decided_by", "id_risultato": "result_id"},
    "rate_card_added": {
        "tipo": "type",
        "valido_da": "valid_from",
        "precedente_valido_da": "previous_valid_from",
        "precedente_valido_a": "previous_valid_to",
    },
    "rate_card_updated": {"valido_da": "valid_from"},
    "day_approved": {"data": "date"},
    "day_created": {"giornata": "work_unit", "stato": "status"},
    "day_state_changed": {"giornata": "work_unit", "da": "from", "a": "to"},
    "approval_added": {"canale": "channel"},
    "approval_recorded": {"giorni": "days", "canale": "channel"},
    "updated": {"campi": "fields", "chiavi": "keys", "digest_settimanale": "weekly_digest"},
    "completata": {"il": "at"},
    "digest.inviato": {"settimana": "week", "destinatari": "recipients", "sezioni": "sections"},
    "lines_replaced": {"righe": "lines", "totale": "total"},
    "payment_state_changed": {"stato_pagamento": "payment_status"},
    "imported": {
        "anno": "year",
        "numero": "number",
        "totale": "total",
        "importata_da": "imported_from",
    },
    "gap_declared": {
        "azienda_id": "legal_entity_id",
        "anno": "year",
        "numero": "number",
        "motivo": "reason",
    },
    "issued": {
        "anno": "year",
        "numero": "number",
        "totale": "total",
        "origine_proforma_id": "origin_proforma_id",
    },
    "annulled": {"numero": "number", "anno": "year", "motivo": "reason"},
    "renewed": {"successore": "successor", "inizio": "start", "fine": "end"},
    "clause_note_added": {"riferimento_clausola": "clause_reference"},
    "clause_note_updated": {"riferimento_clausola": "clause_reference"},
    "renewal_assumption_set": {"probabilita": "probability", "orizzonte_al": "horizon_until"},
    "hub.richiesta_collegata": {"richiesta": "request", "azienda": "legal_entity"},
    "hub.richiesta_ricevuta": {"richiesta": "request"},
    "automazione.attivita_aperta": {"regola": "rule", "titolo": "title", "scadenza": "due_date"},
    "automazione.stage_spostato": {
        "regola": "rule",
        "documento_id": "document_id",
        "da": "from",
        "a": "to",
        "attivata_da": "triggered_by",
        "attivata_via": "triggered_via",
    },
    "automazione.non_eseguita": {
        "regola": "rule",
        "motivo": "reason",
        "documento_id": "document_id",
        "attivata_da": "triggered_by",
        "attivata_via": "triggered_via",
    },
    "expense_added": {"importo": "amount", "rimborsabile": "reimbursable"},
    "gmail.sollecito_preparato": {"numero": "number", "livello": "level", "inviato": "sent"},
    "gmail.account_scollegato": {
        "messaggi_cancellati": "deleted_messages",
        "messaggi_cancellati_conteggio": "count_deleted_messages",
    },
    "slack.canale_impostato": {"canale": "channel"},
    "pat_full_access_changed": {"full_access_prima": "full_access_before"},
    "email_template_saved": {},
}

KIND_VALUES = {
    "created": {
        "code": {
            "contattato": "contacted",
            "negoziazione": "negotiation",
            "offerta": "offer",
            "perso": "lost",
            "vinto": "won",
            "consulenza_esterna": "external_consulting",
            "software_licenze": "software_licenses",
            "viaggi_trasferte": "travel",
            "materiali": "materials",
            "altro": "other",
        },
        "tipo": {
            "offerta": "offer",
            "contratto": "contract",
            "verbale": "minutes",
            "documento": "document",
            "fattura": "invoice",
            "fattura_xml": "invoice_xml",
            "rapporto_ore": "hours_report",
            "sollecito": "payment_reminder",
            "ricorrente_fisso": "fixed_recurring",
            "giornaliero": "daily",
            "orario": "hourly",
            "una_tantum": "one_off",
            "attivita_due": "task_due",
            "attivita_overdue": "task_overdue",
        },
        "ruolo": {"collaboratore": "collaborator"},
        "tariffa_origine": {"manuale": "manual", "utente": "user", "assente": "absent"},
    },
    "document.importato": {
        "tipo": {
            "offerta": "offer",
            "contratto": "contract",
            "verbale": "minutes",
            "documento": "document",
            "fattura": "invoice",
            "fattura_xml": "invoice_xml",
            "rapporto_ore": "hours_report",
            "sollecito": "payment_reminder",
            "ricorrente_fisso": "fixed_recurring",
            "giornaliero": "daily",
            "orario": "hourly",
            "una_tantum": "one_off",
            "attivita_due": "task_due",
            "attivita_overdue": "task_overdue",
        }
    },
    "rate_card_added": {
        "tipo": {
            "offerta": "offer",
            "contratto": "contract",
            "verbale": "minutes",
            "documento": "document",
            "fattura": "invoice",
            "fattura_xml": "invoice_xml",
            "rapporto_ore": "hours_report",
            "sollecito": "payment_reminder",
            "ricorrente_fisso": "fixed_recurring",
            "giornaliero": "daily",
            "orario": "hourly",
            "una_tantum": "one_off",
            "attivita_due": "task_due",
            "attivita_overdue": "task_overdue",
        }
    },
    "state_changed": {
        "da": {
            "bozza": "draft",
            "inviata": "sent",
            "accettata": "accepted",
            "rifiutata": "rejected",
        },
        "a": {
            "bozza": "draft",
            "inviata": "sent",
            "accettata": "accepted",
            "rifiutata": "rejected",
        },
    },
    "day_created": {
        "stato": {
            "proposto": "proposed",
            "approvato": "approved",
            "lavorato": "worked",
            "lavorato_senza_approvazione": "worked_without_approval",
            "fatturato": "invoiced",
            "pagato": "paid",
            "contestato": "disputed",
            "revocato": "revoked",
            "rifiutato": "rejected",
            "non_fatturabile": "non_billable",
        }
    },
    "day_state_changed": {
        "da": {
            "proposto": "proposed",
            "approvato": "approved",
            "lavorato": "worked",
            "lavorato_senza_approvazione": "worked_without_approval",
            "fatturato": "invoiced",
            "pagato": "paid",
            "contestato": "disputed",
            "revocato": "revoked",
            "rifiutato": "rejected",
            "non_fatturabile": "non_billable",
        },
        "a": {
            "proposto": "proposed",
            "approvato": "approved",
            "lavorato": "worked",
            "lavorato_senza_approvazione": "worked_without_approval",
            "fatturato": "invoiced",
            "pagato": "paid",
            "contestato": "disputed",
            "revocato": "revoked",
            "rifiutato": "rejected",
            "non_fatturabile": "non_billable",
        },
    },
    "payment_state_changed": {"stato_pagamento": {"da_incassare": "unpaid", "incassato": "paid"}},
    "imported": {"importata_da": {"esterno": "external"}},
    "approval_added": {
        "canale": {
            "posta_certificata": "certified_mail",
            "raccomandata": "registered_mail",
            "corriere": "courier",
            "altro": "other",
        }
    },
    "approval_recorded": {
        "canale": {
            "posta_certificata": "certified_mail",
            "raccomandata": "registered_mail",
            "corriere": "courier",
            "altro": "other",
        }
    },
    "note_added": {"fonte": {"sistema": "system"}},
    "note_edited": {"fonte": {"sistema": "system"}},
    "note_deleted": {"fonte": {"sistema": "system"}},
    "automazione.attivita_aperta": {
        "regola": {
            "offerta_inviata_richiama": "sent_offer_follow_up",
            "deal_fermo": "stalled_deal",
            "fattura_scaduta": "overdue_invoice",
        }
    },
    "automazione.stage_spostato": {
        "regola": {
            "offerta_inviata_richiama": "sent_offer_follow_up",
            "deal_fermo": "stalled_deal",
            "fattura_scaduta": "overdue_invoice",
        }
    },
    "automazione.non_eseguita": {
        "regola": {
            "offerta_inviata_richiama": "sent_offer_follow_up",
            "deal_fermo": "stalled_deal",
            "fattura_scaduta": "overdue_invoice",
        },
        "motivo": {
            "stage_bersaglio_assente": "target_stage_missing",
            "stage_bersaglio_ambiguo": "target_stage_ambiguous",
            "gia_nello_stato": "already_in_state",
            "regola_disattivata": "rule_disabled",
        },
    },
}

NAME_KINDS = ["created", "email_template_saved"]

PREFERENCE_ENTITIES = ["reminder_preference"]

PERSON_ENTITIES = ["person"]

ENTITY_TABLES = {
    "customer": "customers",
    "person": "people",
    "deal": "deals",
    "invoice": "invoices",
    "document": "documents",
    "contract": "contracts",
    "emitter_profile": "emitter_profile",
    "legal_entity": "emitter_profile",
    "fiscal_profile": "fiscal_profile",
    "pipeline_stage": "pipeline_stages",
    "cost_category": "cost_categories",
    "template": "templates",
    "user": "users",
    "attivita": "attivita",
    "task": "attivita",
    "contract_expense": "contract_expenses",
    "proposal": "proposals",
}

NEW_KIND_OF_OLD = {
    "completata": "completed",
    "annullata": "cancelled",
    "riaperta": "reopened",
    "automazione.stage_spostato": "automation.stage_moved",
    "automazione.non_eseguita": "automation.not_executed",
    "automazione.configurazione_modificata": "automation.config_changed",
    "automazione.attivita_aperta": "automation.task_opened",
    "digest.inviato": "digest.sent",
    "document.importato": "document.imported",
    "drive.account_collegato": "drive.account_connected",
    "drive.account_scollegato": "drive.account_disconnected",
    "drive.credenziale_revocata": "drive.credential_revoked",
    "drive.radici_impostate": "drive.roots_set",
    "gmail.account_collegato": "gmail.account_connected",
    "gmail.account_scollegato": "gmail.account_disconnected",
    "gmail.backfill_eseguito": "gmail.backfill_run",
    "gmail.bozza_creata": "gmail.draft_created",
    "gmail.bozza_eliminata": "gmail.draft_deleted",
    "gmail.bozza_modificata": "gmail.draft_edited",
    "gmail.credenziale_revocata": "gmail.credential_revoked",
    "gmail.impostazioni_modificate": "gmail.settings_changed",
    "gmail.messaggio_inviato": "gmail.message_sent",
    "gmail.messaggio_ricevuto": "gmail.message_received",
    "gmail.sollecito_preparato": "gmail.payment_reminder_prepared",
    "gmail.sync_eseguito": "gmail.sync_run",
    "gmail.invio_incerto": "gmail.send_uncertain",
    "hub.richiesta_collegata": "hub.request_linked",
    "hub.richiesta_ricevuta": "hub.request_received",
    "slack.abbinamenti_rifatti": "slack.matches_rebuilt",
    "slack.abbinamento_manuale": "slack.manual_match",
    "slack.abbinamento_rimosso": "slack.match_removed",
    "slack.canale_impostato": "slack.channel_set",
    "slack.canale_rimosso": "slack.channel_removed",
    "slack.workspace_collegato": "slack.workspace_connected",
    "slack.workspace_scollegato": "slack.workspace_disconnected",
}

SNAPSHOT_TOP = {
    "versione": "version",
    "emittente": "legal_entity",
    "cliente": "customer",
    "fiscale": "tax",
}

SNAPSHOT_PARTY = {
    "ragione_sociale": "legal_name",
    "partita_iva": "vat_number",
    "codice_fiscale": "tax_code",
    "codice_sdi": "sdi_code",
    "pec": "certified_email",
    "indirizzo": "address",
    "comune": "city",
    "provincia": "province",
    "nazione": "country",
    "telefono": "phone",
    "sito_web": "website",
}

SNAPSHOT_TAX = {
    "codice_regime": "regime_code",
    "aliquota_iva_default": "default_vat_rate",
    "natura_default": "default_vat_nature",
    "riferimento_normativo": "legal_reference",
    "applica_bollo": "apply_stamp_duty",
    "soglia_bollo": "stamp_duty_threshold",
    "importo_bollo": "stamp_duty_amount",
    "condizioni_pagamento": "payment_terms",
    "modalita_pagamento": "payment_method",
    "giorni_scadenza": "due_days",
}

POLICY_KEYS = {
    "richiede_preautorizzazione": "requires_pre_authorization",
    "importo_tetto": "amount_cap",
}

POLICY_KINDS = {
    "non_rimborsabile": "not_reimbursable",
    "rimborsabile": "reimbursable",
    "rimborsabile_con_tetto": "reimbursable_with_cap",
}

PROPOSAL_CONTRACT_FIELDS = {
    "titolo": "title",
    "inizio": "start",
    "fine": "end",
    "tipo_rinnovo": "renewal_type",
    "preavviso_rinnovo_giorni": "renewal_notice_days",
    "preavviso_disdetta_giorni": "termination_notice_days",
    "giorni_pagamento": "payment_days",
    "pagamento_fine_mese": "payment_end_of_month",
    "cadenza_fatturazione": "billing_cadence",
    "divisa": "currency",
    "tetto_quota_cliente": "customer_share_cap",
    "politica_spese": "expense_policy",
}

PROPOSAL_WORK_UNIT_FIELDS = {
    "data": "date",
    "quantita": "quantity",
    "descrizione": "description",
    "approvazione": "approval",
}

EVIDENCE_KEYS = {"canale": "channel", "mittente": "sender", "ricevuto_il": "received_at"}

DECLARED_VARIABLE_KEYS = {
    "nome": "name",
    "etichetta": "label",
    "tipo": "type",
    "obbligatoria": "required",
}

ACTOR_KINDS = {"sistema": "system"}

ORIGIN_KINDS = {"manuale": "manual", "agente": "agent"}

ATTACHMENT_KINDS = {"fattura_pdf": "invoice_pdf", "registro_pdf": "register_pdf"}

OLD_POLICY_TABLES = [
    "activities",
    "approvals",
    "attivita",
    "clause_notes",
    "contract_email_templates",
    "contract_expenses",
    "contracts",
    "costs",
    "customers",
    "deals",
    "document_versions",
    "documents",
    "email_drafts",
    "emitter_profile",
    "fiscal_profile",
    "gmail_message_links",
    "gmail_messages",
    "invoice_counters",
    "invoice_lines",
    "invoice_register_gaps",
    "invoices",
    "note_revisions",
    "notes",
    "payment_reminders",
    "people",
    "proposals",
    "rate_cards",
    "renewal_assumptions",
    "time_entries",
    "time_timers",
    "work_unit_transitions",
    "work_units",
]

OLD_TRIGGERS = [
    ("approvals", "trg_approvals_immutable"),
    ("contract_expenses", "trg_contract_expenses_set_rimborsabile"),
    ("work_unit_transitions", "trg_work_unit_transitions_immutable"),
    ("work_units", "trg_work_units_enforce_state_machine"),
    ("work_units", "trg_work_units_log_transition"),
]

OLD_FUNCTIONS = [
    "azienda_visibile(uuid)",
    "cliente_archiviato(uuid)",
    "contract_expense_set_rimborsabile()",
    "contract_visibile(uuid)",
    "customer_visibile(uuid)",
    "deal_attivi_del_cliente(uuid)",
    "deal_visibile(uuid)",
    "document_visibile(uuid)",
    "entita_visibile(text, uuid, boolean)",
    "invoice_visibile(uuid)",
    "mailbox_mia(uuid)",
    "person_visibile(uuid)",
    "raise_immutable_violation()",
    "scope_tutte()",
    "sollecito_di_messaggio(uuid, text)",
    "tipo_fattura_del_pdf(uuid)",
    "work_unit_enforce_state_machine()",
    "work_unit_log_transition()",
    "work_unit_visibile(uuid)",
]

OLD_DEFINER_FUNCTIONS = [
    "sollecito_di_messaggio(uuid, text)",
    "tipo_fattura_del_pdf(uuid)",
    "deal_attivi_del_cliente(uuid)",
    "cliente_archiviato(uuid)",
]

NEW_FUNCTIONS = [
    "CREATE OR REPLACE FUNCTION payment_reminder_of_message(uuid, text) RETURNS uuid\n"
    "    LANGUAGE sql STABLE SECURITY DEFINER\n"
    "    SET search_path = public\n"
    "    SET pigrocrm.legal_entities = '*'\n"
    "    AS $$\n"
    "      SELECT r.invoice_id\n"
    "      FROM email_drafts d\n"
    "      JOIN payment_reminders r ON r.id = d.payment_reminder_id\n"
    "      WHERE d.google_account_id = $1 AND d.sent_gmail_message_id = $2\n"
    "      LIMIT 1\n"
    "    $$",
    "CREATE OR REPLACE FUNCTION invoice_type_of_pdf(uuid) RETURNS text\n"
    "    LANGUAGE sql STABLE SECURITY DEFINER\n"
    "    SET search_path = public\n"
    "    SET pigrocrm.legal_entities = '*'\n"
    "    AS $$\n"
    "      SELECT type FROM invoices WHERE pdf_document_id = $1 LIMIT 1\n"
    "    $$",
    "CREATE OR REPLACE FUNCTION active_deals_of_customer(uuid) RETURNS bigint\n"
    "    LANGUAGE sql STABLE SECURITY DEFINER\n"
    "    SET search_path = public\n"
    "    SET pigrocrm.legal_entities = '*'\n"
    "    AS $$\n"
    "      SELECT count(*) FROM deals WHERE customer_id = $1 AND deleted_at IS NULL\n"
    "    $$",
    "CREATE OR REPLACE FUNCTION customer_archived(uuid) RETURNS boolean\n"
    "    LANGUAGE sql STABLE SECURITY DEFINER\n"
    "    SET search_path = public\n"
    "    SET pigrocrm.legal_entities = '*'\n"
    "    AS $$\n"
    "      SELECT deleted_at IS NOT NULL FROM customers WHERE id = $1\n"
    "    $$",
    "CREATE OR REPLACE FUNCTION scope_all() RETURNS boolean\n"
    "    LANGUAGE sql STABLE AS $$\n"
    "      SELECT current_setting('pigrocrm.legal_entities', true) = '*'\n"
    "    $$",
    "CREATE OR REPLACE FUNCTION legal_entity_visible(uuid) RETURNS boolean\n"
    "    LANGUAGE sql STABLE AS $$\n"
    "      SELECT CASE current_setting('pigrocrm.legal_entities', true)\n"
    "        WHEN '*' THEN true\n"
    "        ELSE $1 = ANY (string_to_array(\n"
    "          coalesce(current_setting('pigrocrm.legal_entities', true), ''), ',')::uuid[])\n"
    "      END\n"
    "    $$",
    "CREATE OR REPLACE FUNCTION customer_visible(uuid) RETURNS boolean\n"
    "    LANGUAGE sql STABLE AS $$\n"
    "      SELECT current_setting('pigrocrm.legal_entities', true) = '*' OR EXISTS (\n"
    "        SELECT 1 FROM customers c WHERE c.id = $1 AND "
    "legal_entity_visible(c.legal_entity_id))\n"
    "    $$",
    "CREATE OR REPLACE FUNCTION person_visible(uuid) RETURNS boolean\n"
    "    LANGUAGE sql STABLE AS $$\n"
    "      SELECT current_setting('pigrocrm.legal_entities', true) = '*' OR EXISTS (\n"
    "        SELECT 1 FROM people p JOIN customers c ON c.id = p.customer_id\n"
    "        WHERE p.id = $1 AND legal_entity_visible(c.legal_entity_id))\n"
    "    $$",
    "CREATE OR REPLACE FUNCTION deal_visible(uuid) RETURNS boolean\n"
    "    LANGUAGE sql STABLE AS $$\n"
    "      SELECT current_setting('pigrocrm.legal_entities', true) = '*' OR EXISTS (\n"
    "        SELECT 1 FROM deals d WHERE d.id = $1 AND legal_entity_visible(d.legal_entity_id))\n"
    "    $$",
    "CREATE OR REPLACE FUNCTION invoice_visible(uuid) RETURNS boolean\n"
    "    LANGUAGE sql STABLE AS $$\n"
    "      SELECT current_setting('pigrocrm.legal_entities', true) = '*' OR EXISTS (\n"
    "        SELECT 1 FROM invoices i WHERE i.id = $1 AND "
    "legal_entity_visible(i.legal_entity_id))\n"
    "    $$",
    "CREATE OR REPLACE FUNCTION contract_visible(uuid) RETURNS boolean\n"
    "    LANGUAGE sql STABLE AS $$\n"
    "      SELECT current_setting('pigrocrm.legal_entities', true) = '*' OR EXISTS (\n"
    "        SELECT 1 FROM contracts k WHERE k.id = $1 AND "
    "legal_entity_visible(k.legal_entity_id))\n"
    "    $$",
    "CREATE OR REPLACE FUNCTION document_visible(uuid) RETURNS boolean\n"
    "    LANGUAGE sql STABLE AS $$\n"
    "      SELECT current_setting('pigrocrm.legal_entities', true) = '*' OR EXISTS (\n"
    "        SELECT 1 FROM documents d WHERE d.id = $1 AND "
    "legal_entity_visible(d.legal_entity_id))\n"
    "    $$",
    "CREATE OR REPLACE FUNCTION work_unit_visible(uuid) RETURNS boolean\n"
    "    LANGUAGE sql STABLE AS $$\n"
    "      SELECT current_setting('pigrocrm.legal_entities', true) = '*' OR EXISTS (\n"
    "        SELECT 1 FROM work_units w JOIN contracts k ON k.id = w.contract_id\n"
    "        WHERE w.id = $1 AND legal_entity_visible(k.legal_entity_id))\n"
    "    $$",
    "CREATE OR REPLACE FUNCTION mailbox_mine(uuid) RETURNS boolean\n"
    "    LANGUAGE sql STABLE AS $$\n"
    "      SELECT EXISTS (\n"
    "        SELECT 1 FROM google_accounts g WHERE g.id = $1\n"
    "        AND g.user_id::text = coalesce(current_setting('pigrocrm.user_id', true), ''))\n"
    "    $$",
    "CREATE OR REPLACE FUNCTION entity_visible(text, uuid, boolean) RETURNS boolean\n"
    "    LANGUAGE sql STABLE AS $$\n"
    "      SELECT CASE $1\n"
    "        WHEN 'customer' THEN customer_visible($2)\n"
    "        WHEN 'person' THEN person_visible($2)\n"
    "        WHEN 'deal' THEN deal_visible($2)\n"
    "        WHEN 'contract' THEN contract_visible($2)\n"
    "        WHEN 'document' THEN document_visible($2)\n"
    "        WHEN 'invoice' THEN invoice_visible($2)\n"
    "        WHEN 'work_unit' THEN work_unit_visible($2)\n"
    "        WHEN 'legal_entity' THEN legal_entity_visible($2)\n"
    "        WHEN 'fiscal_profile' THEN current_setting('pigrocrm.legal_entities', true) = '*' OR "
    "EXISTS (\n"
    "          SELECT 1 FROM fiscal_profile f WHERE f.id = $2 AND "
    "legal_entity_visible(f.legal_entity_id))\n"
    "        WHEN 'payment_reminder' THEN current_setting('pigrocrm.legal_entities', true) = '*' "
    "OR EXISTS (\n"
    "          SELECT 1 FROM payment_reminders r WHERE r.id = $2 AND "
    "invoice_visible(r.invoice_id))\n"
    "        WHEN 'email_draft' THEN current_setting('pigrocrm.legal_entities', true) = '*' OR "
    "EXISTS (\n"
    "          SELECT 1 FROM email_drafts e WHERE e.id = $2\n"
    "          AND entity_visible(e.entity_type, e.entity_id, scope_all()))\n"
    "        WHEN 'proposal' THEN current_setting('pigrocrm.legal_entities', true) = '*' OR "
    "EXISTS (\n"
    "          SELECT 1 FROM proposals p WHERE p.id = $2 AND document_visible(p.document_id)\n"
    "          AND (p.contract_id IS NULL OR contract_visible(p.contract_id)))\n"
    "        WHEN 'contract_expense' THEN current_setting('pigrocrm.legal_entities', true) = '*' "
    "OR EXISTS (\n"
    "          SELECT 1 FROM contract_expenses x WHERE x.id = $2 AND "
    "contract_visible(x.contract_id))\n"
    "        WHEN 'approval' THEN current_setting('pigrocrm.legal_entities', true) = '*' OR "
    "EXISTS (\n"
    "          SELECT 1 FROM approvals a WHERE a.id = $2 AND contract_visible(a.contract_id))\n"
    "        WHEN 'time_entry' THEN current_setting('pigrocrm.legal_entities', true) = '*' OR "
    "EXISTS (\n"
    "          SELECT 1 FROM time_entries t WHERE t.id = $2 AND deal_visible(t.deal_id))\n"
    "        WHEN 'timer' THEN current_setting('pigrocrm.legal_entities', true) = '*' OR EXISTS "
    "(\n"
    "          SELECT 1 FROM time_timers t WHERE t.id = $2\n"
    "          AND (t.deal_id IS NULL OR deal_visible(t.deal_id)))\n"
    "        WHEN 'cost' THEN current_setting('pigrocrm.legal_entities', true) = '*' OR EXISTS (\n"
    "          SELECT 1 FROM costs c WHERE c.id = $2\n"
    "          AND (c.legal_entity_id IS NULL OR legal_entity_visible(c.legal_entity_id)))\n"
    "        WHEN 'task' THEN current_setting('pigrocrm.legal_entities', true) = '*' OR EXISTS (\n"
    "          SELECT 1 FROM tasks a WHERE a.id = $2 AND (\n"
    "            (a.customer_id IS NULL OR customer_visible(a.customer_id))\n"
    "            AND (a.person_id IS NULL OR person_visible(a.person_id))\n"
    "            AND (a.deal_id IS NULL OR deal_visible(a.deal_id))\n"
    "            AND (a.invoice_id IS NULL OR invoice_visible(a.invoice_id))))\n"
    "        ELSE $3\n"
    "      END\n"
    "    $$",
]

NEW_FUNCTION_SIGNATURES = [
    "payment_reminder_of_message(uuid, text)",
    "invoice_type_of_pdf(uuid)",
    "active_deals_of_customer(uuid)",
    "customer_archived(uuid)",
    "scope_all()",
    "legal_entity_visible(uuid)",
    "customer_visible(uuid)",
    "person_visible(uuid)",
    "deal_visible(uuid)",
    "invoice_visible(uuid)",
    "contract_visible(uuid)",
    "document_visible(uuid)",
    "work_unit_visible(uuid)",
    "mailbox_mine(uuid)",
    "entity_visible(text, uuid, boolean)",
    "raise_immutable_violation()",
    "work_unit_enforce_state_machine()",
    "work_unit_log_transition()",
    "contract_expense_set_reimbursable()",
]

NEW_DEFINER_FUNCTIONS = [
    "payment_reminder_of_message(uuid, text)",
    "invoice_type_of_pdf(uuid)",
    "active_deals_of_customer(uuid)",
    "customer_archived(uuid)",
]

NEW_POLICIES = {
    "legal_entities": ("legal_entity_visible(id)", None),
    "fiscal_profile": ("legal_entity_visible(legal_entity_id)", None),
    "invoice_counters": ("legal_entity_visible(legal_entity_id)", None),
    "invoice_register_gaps": ("legal_entity_visible(legal_entity_id)", None),
    "customers": ("legal_entity_visible(legal_entity_id)", None),
    "deals": ("legal_entity_visible(legal_entity_id)", None),
    "contracts": ("legal_entity_visible(legal_entity_id)", None),
    "documents": ("legal_entity_visible(legal_entity_id)", None),
    "invoices": ("legal_entity_visible(legal_entity_id)", None),
    "costs": (
        "(legal_entity_id IS NULL AND scope_all()) OR legal_entity_visible(legal_entity_id)",
        None,
    ),
    "people": (
        "scope_all() OR ((customer_id IS NULL AND scope_all()) OR customer_visible(customer_id))",
        "scope_all() OR ((customer_id IS NULL AND scope_all()) OR customer_visible(customer_id))",
    ),
    "time_entries": (
        "scope_all() OR (deal_visible(deal_id))",
        "scope_all() OR (deal_visible(deal_id))",
    ),
    "time_timers": (
        "scope_all() OR ((deal_id IS NULL AND scope_all()) OR deal_visible(deal_id))",
        "scope_all() OR ((deal_id IS NULL AND scope_all()) OR deal_visible(deal_id))",
    ),
    "invoice_lines": (
        "scope_all() OR (invoice_visible(invoice_id))",
        "scope_all() OR (invoice_visible(invoice_id))",
    ),
    "payment_reminders": (
        "scope_all() OR (invoice_visible(invoice_id))",
        "scope_all() OR (invoice_visible(invoice_id))",
    ),
    "document_versions": (
        "scope_all() OR (document_visible(document_id))",
        "scope_all() OR (document_visible(document_id))",
    ),
    "rate_cards": (
        "scope_all() OR (contract_visible(contract_id))",
        "scope_all() OR (contract_visible(contract_id))",
    ),
    "renewal_assumptions": (
        "scope_all() OR (contract_visible(contract_id))",
        "scope_all() OR (contract_visible(contract_id))",
    ),
    "contract_expenses": (
        "scope_all() OR (contract_visible(contract_id))",
        "scope_all() OR (contract_visible(contract_id))",
    ),
    "work_units": (
        "scope_all() OR (contract_visible(contract_id))",
        "scope_all() OR (contract_visible(contract_id))",
    ),
    "approvals": (
        "scope_all() OR (contract_visible(contract_id))",
        "scope_all() OR (contract_visible(contract_id))",
    ),
    "work_unit_transitions": (
        "scope_all() OR (work_unit_visible(work_unit_id))",
        "scope_all() OR (work_unit_visible(work_unit_id))",
    ),
    "proposals": (
        "scope_all() OR (document_visible(document_id) AND (contract_id IS NULL OR "
        "contract_visible(contract_id)))",
        "scope_all() OR (document_visible(document_id) AND (contract_id IS NULL OR "
        "contract_visible(contract_id)))",
    ),
    "tasks": (
        "scope_all() OR (CASE WHEN customer_id IS NULL AND person_id IS NULL AND deal_id IS "
        "NULL AND invoice_id IS NULL THEN scope_all() ELSE (customer_id IS NULL OR "
        "customer_visible(customer_id)) AND (person_id IS NULL OR "
        "person_visible(person_id)) AND (deal_id IS NULL OR deal_visible(deal_id)) AND "
        "(invoice_id IS NULL OR invoice_visible(invoice_id)) END)",
        "scope_all() OR (CASE WHEN customer_id IS NULL AND person_id IS NULL AND deal_id IS "
        "NULL AND invoice_id IS NULL THEN scope_all() ELSE (customer_id IS NULL OR "
        "customer_visible(customer_id)) AND (person_id IS NULL OR "
        "person_visible(person_id)) AND (deal_id IS NULL OR deal_visible(deal_id)) AND "
        "(invoice_id IS NULL OR invoice_visible(invoice_id)) END)",
    ),
    "gmail_message_links": (
        "scope_all() OR (entity_visible(entity_type, entity_id, scope_all()))",
        "true",
    ),
    "email_drafts": (
        "scope_all() OR (entity_visible(entity_type, entity_id, scope_all()) AND "
        "(payment_reminder_id IS NULL OR scope_all() OR EXISTS (SELECT 1 FROM "
        "payment_reminders r WHERE r.id = email_drafts.payment_reminder_id AND "
        "invoice_visible(r.invoice_id))))",
        "scope_all() OR (entity_visible(entity_type, entity_id, scope_all()) AND "
        "(payment_reminder_id IS NULL OR scope_all() OR EXISTS (SELECT 1 FROM "
        "payment_reminders r WHERE r.id = email_drafts.payment_reminder_id AND "
        "invoice_visible(r.invoice_id))))",
    ),
    "gmail_messages": (
        "scope_all() OR ((mailbox_mine(google_account_id) OR EXISTS (SELECT 1 FROM "
        "gmail_message_links l WHERE l.gmail_message_id = gmail_messages.id AND "
        "entity_visible(l.entity_type, l.entity_id, scope_all())) OR (NOT EXISTS "
        "(SELECT 1 FROM gmail_message_links l WHERE l.gmail_message_id = "
        "gmail_messages.id) AND scope_all())) AND (scope_all() OR "
        "payment_reminder_of_message(google_account_id, gmail_message_id) IS NULL "
        "OR invoice_visible(payment_reminder_of_message(google_account_id, "
        "gmail_message_id))))",
        "scope_all() OR ((mailbox_mine(google_account_id) OR EXISTS (SELECT 1 FROM "
        "gmail_message_links l WHERE l.gmail_message_id = gmail_messages.id AND "
        "entity_visible(l.entity_type, l.entity_id, scope_all())) OR (NOT EXISTS "
        "(SELECT 1 FROM gmail_message_links l WHERE l.gmail_message_id = "
        "gmail_messages.id) AND scope_all())) AND (scope_all() OR "
        "payment_reminder_of_message(google_account_id, gmail_message_id) IS NULL "
        "OR invoice_visible(payment_reminder_of_message(google_account_id, "
        "gmail_message_id))))",
    ),
    "activities": (
        "scope_all() OR ((CASE WHEN entity_type = 'invoice_register' THEN "
        "legal_entity_visible(NULLIF(payload->>'legal_entity_id', '')::uuid) ELSE "
        "entity_visible(entity_type, entity_id, true) END) AND (payload->>'invoice_id' "
        "IS NULL OR scope_all() OR invoice_visible(NULLIF(payload->>'invoice_id', "
        "'')::uuid)))",
        "scope_all() OR ((CASE WHEN entity_type = 'invoice_register' THEN "
        "legal_entity_visible(NULLIF(payload->>'legal_entity_id', '')::uuid) ELSE "
        "entity_visible(entity_type, entity_id, true) END) AND (payload->>'invoice_id' "
        "IS NULL OR scope_all() OR invoice_visible(NULLIF(payload->>'invoice_id', "
        "'')::uuid)))",
    ),
    "contract_email_templates": (
        "scope_all() OR (contract_visible(contract_id))",
        "scope_all() OR (contract_visible(contract_id))",
    ),
    "clause_notes": (
        "scope_all() OR (contract_visible(contract_id))",
        "scope_all() OR (contract_visible(contract_id))",
    ),
    "notes": (
        "scope_all() OR entity_visible(entity_type, entity_id, false)",
        "scope_all() OR entity_visible(entity_type, entity_id, false)",
    ),
    "note_revisions": (
        "scope_all() OR EXISTS (SELECT 1 FROM notes n WHERE n.id = note_id AND "
        "entity_visible(n.entity_type, n.entity_id, false))",
        "scope_all() OR EXISTS (SELECT 1 FROM notes n WHERE n.id = note_id AND "
        "entity_visible(n.entity_type, n.entity_id, false))",
    ),
}

NEW_TRIGGERS = [
    ("approvals", "trg_approvals_immutable"),
    ("contract_expenses", "trg_contract_expenses_set_reimbursable"),
    ("work_unit_transitions", "trg_work_unit_transitions_immutable"),
    ("work_units", "trg_work_units_enforce_state_machine"),
    ("work_units", "trg_work_units_log_transition"),
]

NEW_TRIGGER_SQL = (
    "\n"
    "CREATE OR REPLACE FUNCTION raise_immutable_violation() RETURNS TRIGGER AS $$\n"
    "BEGIN\n"
    "    RAISE EXCEPTION '% on % is not allowed: rows in this table are immutable once written "
    "(id=%)',\n"
    "        TG_OP, TG_TABLE_NAME, COALESCE(NEW.id, OLD.id)\n"
    "        USING ERRCODE = 'check_violation';\n"
    "END;\n"
    "$$ LANGUAGE plpgsql;\n"
    "\n"
    "CREATE OR REPLACE FUNCTION work_unit_enforce_state_machine() RETURNS TRIGGER AS $$\n"
    "DECLARE\n"
    "    v_requires_approval boolean;\n"
    "    -- What the caller actually asked for, captured before anything below rewrites\n"
    "    -- `NEW.status` -- the edge check runs against this, not against the possibly\n"
    "    -- redirected value, so a state that was never allowed to reach 'worked' at\n"
    "    -- all cannot sneak into 'worked_without_approval' by asking for the\n"
    "    -- unapproved write instead.\n"
    "    v_requested text := NEW.status;\n"
    "BEGIN\n"
    "    SELECT requires_prior_approval INTO v_requires_approval\n"
    "    FROM contracts WHERE id = NEW.contract_id;\n"
    "\n"
    "    IF v_requires_approval IS NULL THEN\n"
    "        RAISE EXCEPTION 'work_units.contract_id % does not reference a contract', "
    "NEW.contract_id\n"
    "            USING ERRCODE = 'foreign_key_violation';\n"
    "    END IF;\n"
    "\n"
    "    IF TG_OP = 'INSERT' THEN\n"
    "        IF v_requested NOT IN ('proposed', 'approved', 'worked') THEN\n"
    "            RAISE EXCEPTION\n"
    "                'a work_unit cannot be created directly in state %; '\n"
    "                'the legal entry states are proposed, approved, worked',\n"
    "                v_requested\n"
    "                USING ERRCODE = 'check_violation';\n"
    "        END IF;\n"
    "    ELSIF v_requested <> OLD.status\n"
    "        AND NOT ((OLD.status || '->' || v_requested) = ANY (ARRAY[\n"
    "            'approved->revoked',\n"
    "            'approved->worked',\n"
    "            'disputed->non_billable',\n"
    "            'disputed->revoked',\n"
    "            'disputed->worked',\n"
    "            'invoiced->disputed',\n"
    "            'invoiced->paid',\n"
    "            'proposed->approved',\n"
    "            'proposed->rejected',\n"
    "            'proposed->revoked',\n"
    "            'proposed->worked',\n"
    "            'worked->disputed',\n"
    "            'worked->invoiced',\n"
    "            'worked->non_billable',\n"
    "            'worked->revoked',\n"
    "            'worked_without_approval->disputed',\n"
    "            'worked_without_approval->invoiced',\n"
    "            'worked_without_approval->non_billable',\n"
    "            'worked_without_approval->revoked',\n"
    "            'worked_without_approval->worked'\n"
    "        ]))\n"
    "    THEN\n"
    "        RAISE EXCEPTION 'work_unit % cannot move from % to %', NEW.id, OLD.status, "
    "v_requested\n"
    "            USING ERRCODE = 'check_violation';\n"
    "    END IF;\n"
    "\n"
    "    -- The automatic redirect (0013): a 'worked' write with no approval on a\n"
    "    -- contract that requires one is never rejected outright -- it is recorded,\n"
    "    -- flagged, and recovered the moment an approval is linked (spec §5).\n"
    "    IF NEW.status = 'worked' AND v_requires_approval AND NEW.approval_id IS NULL THEN\n"
    "        NEW.status := 'worked_without_approval';\n"
    "    END IF;\n"
    "\n"
    "    -- The recovery half: the flagged state returns to plain 'worked' the instant\n"
    "    -- an approval is linked, whether the caller asked for 'worked' explicitly\n"
    "    -- (already validated as a legal edge above) or only set approval_id while\n"
    "    -- leaving status untouched (v_requested = OLD.status, so the edge check above\n"
    "    -- was a same-state no-op and this is the only thing that moves it).\n"
    "    IF NEW.status = 'worked_without_approval' AND NEW.approval_id IS NOT NULL THEN\n"
    "        NEW.status := 'worked';\n"
    "    END IF;\n"
    "\n"
    "    RETURN NEW;\n"
    "END;\n"
    "$$ LANGUAGE plpgsql;\n"
    "\n"
    "CREATE OR REPLACE FUNCTION work_unit_log_transition() RETURNS TRIGGER AS $$\n"
    "DECLARE\n"
    "    v_actor jsonb;\n"
    "    v_reason text;\n"
    "BEGIN\n"
    "    IF TG_OP = 'UPDATE' AND NEW.status = OLD.status THEN\n"
    "        RETURN NEW;\n"
    "    END IF;\n"
    "\n"
    "    -- Session-local settings the repository layer sets immediately before the\n"
    "    -- write (`set_config(..., true)`, scoped to the transaction), falling back to\n"
    "    -- a system actor and a generic reason when unset rather than failing -- the log\n"
    "    -- must never go silently incomplete (spec §5, `0012...sql:97-105`).\n"
    "    v_actor := COALESCE(\n"
    "        NULLIF(current_setting('pigrocrm.actor', true), '')::jsonb,\n"
    '        \'{"kind": "system"}\'::jsonb\n'
    "    );\n"
    "    v_reason := COALESCE(\n"
    "        NULLIF(current_setting('pigrocrm.reason', true), ''),\n"
    "        'nessun motivo fornito'\n"
    "    );\n"
    "\n"
    "    INSERT INTO work_unit_transitions\n"
    "        (id, work_unit_id, previous_status, new_status, actor, reason)\n"
    "    VALUES (\n"
    "        gen_random_uuid(),\n"
    "        NEW.id,\n"
    "        CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.status END,\n"
    "        NEW.status,\n"
    "        v_actor,\n"
    "        v_reason\n"
    "    );\n"
    "\n"
    "    RETURN NEW;\n"
    "END;\n"
    "$$ LANGUAGE plpgsql;\n"
    "\n"
    "DROP TRIGGER IF EXISTS trg_approvals_immutable ON approvals;\n"
    "CREATE TRIGGER trg_approvals_immutable\n"
    "    BEFORE UPDATE ON approvals\n"
    "    FOR EACH ROW EXECUTE FUNCTION raise_immutable_violation();\n"
    "\n"
    "DROP TRIGGER IF EXISTS trg_work_unit_transitions_immutable ON work_unit_transitions;\n"
    "CREATE TRIGGER trg_work_unit_transitions_immutable\n"
    "    BEFORE UPDATE OR DELETE ON work_unit_transitions\n"
    "    FOR EACH ROW EXECUTE FUNCTION raise_immutable_violation();\n"
    "\n"
    "DROP TRIGGER IF EXISTS trg_work_units_enforce_state_machine ON work_units;\n"
    "CREATE TRIGGER trg_work_units_enforce_state_machine\n"
    "    BEFORE INSERT OR UPDATE ON work_units\n"
    "    FOR EACH ROW EXECUTE FUNCTION work_unit_enforce_state_machine();\n"
    "\n"
    "DROP TRIGGER IF EXISTS trg_work_units_log_transition ON work_units;\n"
    "CREATE TRIGGER trg_work_units_log_transition\n"
    "    AFTER INSERT OR UPDATE ON work_units\n"
    "    FOR EACH ROW EXECUTE FUNCTION work_unit_log_transition();\n"
    "\n"
    "\n"
    "CREATE OR REPLACE FUNCTION contract_expense_set_reimbursable() RETURNS TRIGGER AS $$\n"
    "DECLARE\n"
    "    v_policy jsonb;\n"
    "    v_kind text;\n"
    "    v_requires_pre_authorization boolean;\n"
    "BEGIN\n"
    "    SELECT c.expense_policy INTO v_policy\n"
    "    FROM contracts c WHERE c.id = NEW.contract_id;\n"
    "\n"
    "    IF v_policy IS NULL THEN\n"
    "        RAISE EXCEPTION 'contract_expenses.contract_id % does not reference a contract',\n"
    "            NEW.contract_id\n"
    "            USING ERRCODE = 'foreign_key_violation';\n"
    "    END IF;\n"
    "\n"
    "    v_kind := v_policy ->> 'kind';\n"
    "    IF v_kind IS NULL THEN\n"
    "        RAISE EXCEPTION\n"
    "            'contracts.expense_policy for contract % has no \"kind\" tag: %',\n"
    "            NEW.contract_id, v_policy\n"
    "            USING ERRCODE = 'check_violation';\n"
    "    END IF;\n"
    "    v_requires_pre_authorization := COALESCE(\n"
    "        (v_policy ->> 'requires_pre_authorization')::boolean, false\n"
    "    );\n"
    "\n"
    "    -- mastro's own `expense_set_reimbursable`, translated one for one: never\n"
    "    -- reimbursable at all under 'not_reimbursable', regardless of\n"
    "    -- pre-authorisation; otherwise reimbursable unless the policy requires\n"
    "    -- pre-authorisation and this expense does not have it -- in which case it is\n"
    "    -- still recorded, just flagged, never rejected outright.\n"
    "    NEW.reimbursable := (v_kind <> 'not_reimbursable')\n"
    "        AND (NOT v_requires_pre_authorization OR NEW.pre_authorized);\n"
    "\n"
    "    RETURN NEW;\n"
    "END;\n"
    "$$ LANGUAGE plpgsql;\n"
    "\n"
    "DROP TRIGGER IF EXISTS trg_contract_expenses_set_reimbursable ON contract_expenses;\n"
    "CREATE TRIGGER trg_contract_expenses_set_reimbursable\n"
    "    BEFORE INSERT OR UPDATE ON contract_expenses\n"
    "    FOR EACH ROW EXECUTE FUNCTION contract_expense_set_reimbursable();\n"
)

# ---------------------------------------------------------------- the new RLS surface


def statements() -> list[str]:
    """The functions, then per table the policy, `ENABLE` and `FORCE`, then the hardening
    of the four definer functions: what the test template applies after `create_all`."""
    out = list(NEW_FUNCTIONS)
    for table, (using, check) in NEW_POLICIES.items():
        out.append(
            f"CREATE POLICY legal_entity_scope ON {table} FOR ALL "
            f"USING ({using}) WITH CHECK ({check or using})"
        )
        out.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        out.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for function in NEW_DEFINER_FUNCTIONS:
        out.append(f"ALTER FUNCTION {function} SET search_path = pg_catalog, public, pg_temp")
        out.append(f"REVOKE EXECUTE ON FUNCTION {function} FROM PUBLIC")
    return out


# ---------------------------------------------------------------- vocabulary helpers


def _invert(mapping: dict[str, str]) -> dict[str, str]:
    return {new: old for old, new in mapping.items()}


def _map_value(value: Any, mapping: dict[str, str]) -> Any:
    return mapping.get(value, value) if isinstance(value, str) else value


def _entity_columns(entity: str, up: bool) -> dict[str, str]:
    table = ENTITY_TABLES.get(entity)
    columns = dict(COLUMNS_BY_OLD_TABLE.get(table, {})) if table else {}
    if table == "users":
        columns["aziende"] = "legal_entities"
    return columns if up else _invert(columns)


def _old_kind(entity: str, kind: str) -> str:
    for _entity_old, entity_new, kind_old, kind_new in ACTIVITY_KINDS:
        if kind_new == kind and entity_new in (None, entity):
            return kind_old
    return kind


def _entity_new_table(entity: str) -> str | None:
    old = ENTITY_TABLES.get(entity)
    return dict(TABLE_RENAMES).get(old, old) if old else None


def _map_column_value(table: str | None, column: str, value: Any, up: bool) -> Any:
    mapping = COLUMN_VALUES.get((table, column)) if table else None
    if mapping is None:
        return value
    return _map_value(value, mapping if up else _invert(mapping))


def _rewrite_activity(entity: str, kind: str, payload: Any, up: bool) -> Any:
    """`entity` and `kind` are the OLD words on the way up and the NEW ones on the way down."""
    if not isinstance(payload, dict):
        return payload
    if not up:
        kind = _old_kind(entity, kind)
    key_maps: list[dict[str, str]] = []
    keys = KIND_KEYS.get(kind)
    if keys is not None:
        key_maps.append(keys if up else _invert(keys))
    if entity in PREFERENCE_ENTITIES:
        key_maps.append({"tipo": "type"} if up else {"type": "tipo"})
    first_name = "first_name" if entity in PERSON_ENTITIES else "name"
    values = KIND_VALUES.get(kind, {})
    out: dict[str, Any] = {}
    for key, value in payload.items():
        new_key = key
        for key_map in key_maps:
            if key in key_map:
                new_key = key_map[key]
        if key == "nome" and up and new_key == "nome":
            new_key = first_name
        if not up and key in ("name", "first_name") and new_key == key and kind in NAME_KINDS:
            new_key = "nome"
        value_map = values.get(key if up else new_key)
        if value_map is not None:
            value = _map_value(value, value_map if up else _invert(value_map))
        out[new_key] = value
    changed = out.get("changed")
    columns = _entity_columns(entity, up)
    if isinstance(changed, list):
        out["changed"] = [_map_value(name, columns) for name in changed]
        table = _entity_new_table(entity)
        for side in ("before", "after"):
            if isinstance(out.get(side), dict):
                out[side] = {
                    columns.get(k, k): _map_column_value(
                        table, k if not up else columns.get(k, k), v, up
                    )
                    for k, v in out[side].items()
                }
    if entity == "space_settings":
        mapping = COLUMN_VALUES[("space_settings", "key")]
        for name in ("keys", "chiavi"):
            if isinstance(out.get(name), list):
                out[name] = [_map_value(k, mapping if up else _invert(mapping)) for k in out[name]]
    return out


def _rename_keys(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {mapping.get(k, k): _rename_keys(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [_rename_keys(item, mapping) for item in value]
    return value


def _rewrite_snapshot(snapshot: Any, up: bool) -> Any:
    if not isinstance(snapshot, dict):
        return snapshot
    top = SNAPSHOT_TOP if up else _invert(SNAPSHOT_TOP)
    party = SNAPSHOT_PARTY if up else _invert(SNAPSHOT_PARTY)
    tax = SNAPSHOT_TAX if up else _invert(SNAPSHOT_TAX)
    out: dict[str, Any] = {}
    for key, value in snapshot.items():
        new_key = top.get(key, key)
        if new_key in ("emittente", "cliente", "legal_entity", "customer") and isinstance(
            value, dict
        ):
            value = {party.get(k, k): v for k, v in value.items()}
        elif new_key in ("fiscale", "tax") and isinstance(value, dict):
            value = {tax.get(k, k): v for k, v in value.items()}
        out[new_key] = value
    return out


def _rewrite_policy(policy: Any, up: bool) -> Any:
    if not isinstance(policy, dict):
        return policy
    keys = POLICY_KEYS if up else _invert(POLICY_KEYS)
    kinds = POLICY_KINDS if up else _invert(POLICY_KINDS)
    out = {keys.get(k, k): v for k, v in policy.items()}
    if isinstance(out.get("kind"), str):
        out["kind"] = kinds.get(out["kind"], out["kind"])
    return out


def _rewrite_fields(target_type: str, fields: Any, up: bool) -> Any:
    """`proposals.proposed_fields` / `accepted_fields`, keyed by the target's field names."""
    if not isinstance(fields, dict):
        return fields
    if target_type in ("contract", "contratto"):
        names = PROPOSAL_CONTRACT_FIELDS if up else _invert(PROPOSAL_CONTRACT_FIELDS)
    else:
        names = PROPOSAL_WORK_UNIT_FIELDS if up else _invert(PROPOSAL_WORK_UNIT_FIELDS)
    out: dict[str, Any] = {}
    for key, value in fields.items():
        new_key = names.get(key, key)
        if new_key in ("expense_policy", "politica_spese"):
            value = _rewrite_policy(value, up)
        elif new_key in ("renewal_type", "tipo_rinnovo"):
            mapping = COLUMN_VALUES[("contracts", "renewal_type")]
            value = _map_value(value, mapping if up else _invert(mapping))
        elif new_key in ("approval", "approvazione") and isinstance(value, dict):
            evidence = EVIDENCE_KEYS if up else _invert(EVIDENCE_KEYS)
            value = {evidence.get(k, k): v for k, v in value.items()}
            for name in ("channel", "canale"):
                if name in value:
                    mapping = COLUMN_VALUES[("approvals", "channel")]
                    value[name] = _map_value(value[name], mapping if up else _invert(mapping))
        out[new_key] = value
    return out


def _rewrite_variables(variables: Any, up: bool) -> Any:
    if not isinstance(variables, list):
        return variables
    names = DECLARED_VARIABLE_KEYS if up else _invert(DECLARED_VARIABLE_KEYS)
    return [
        {names.get(k, k): v for k, v in item.items()} if isinstance(item, dict) else item
        for item in variables
    ]


def _json_tables(up: bool) -> list[tuple[str, str, Any]]:
    """`(table, column, rewrite(row_json, row))` for every JSON column but the timeline's."""
    kinds = ACTOR_KINDS if up else _invert(ACTOR_KINDS)
    origin_kinds = ORIGIN_KINDS if up else _invert(ORIGIN_KINDS)
    attachments = ATTACHMENT_KINDS if up else _invert(ATTACHMENT_KINDS)

    def origin(value: Any, row: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("kind"), str):
            return {**value, "kind": origin_kinds.get(value["kind"], value["kind"])}
        return value

    def actor(value: Any, row: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("kind"), str):
            return {**value, "kind": kinds.get(value["kind"], value["kind"])}
        return value

    return [
        ("invoices", "snapshot", lambda v, r: _rewrite_snapshot(v, up)),
        ("contracts", "expense_policy", lambda v, r: _rewrite_policy(v, up)),
        ("approvals", "origin", origin),
        ("work_unit_transitions", "actor", actor),
        ("templates", "declared_variables", lambda v, r: _rewrite_variables(v, up)),
        (
            "contract_email_templates",
            "attachments",
            lambda v, r: [attachments.get(i, i) for i in v] if isinstance(v, list) else v,
        ),
    ]


def _update_json(connection: Any, table: str, column: str, rewrite: Any) -> None:
    rows = connection.execute(
        sa.text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")  # noqa: S608
    ).fetchall()
    changed = []
    for row in rows:
        new = rewrite(row[1], row)
        if new != row[1]:
            changed.append({"id": row[0], "value": new})
    if changed:
        statement = sa.text(
            f"UPDATE {table} SET {column} = :value WHERE id = :id"  # noqa: S608
        ).bindparams(sa.bindparam("value", type_=sa.JSON(none_as_null=True)))
        # `JSONB` columns take the JSON the bound parameter serialises.
        connection.execute(statement, changed)


def _update_proposals(connection: Any, up: bool) -> None:
    rows = connection.execute(
        sa.text("SELECT id, target_type, proposed_fields, accepted_fields FROM proposals")
    ).fetchall()
    changed = []
    for row_id, target, proposed, accepted in rows:
        new_proposed = _rewrite_fields(target, proposed, up)
        new_accepted = _rewrite_fields(target, accepted, up)
        if new_proposed != proposed or new_accepted != accepted:
            changed.append({"id": row_id, "p": new_proposed, "a": new_accepted})
    if changed:
        connection.execute(
            sa.text(
                "UPDATE proposals SET proposed_fields = :p, accepted_fields = :a WHERE id = :id"
            ).bindparams(
                sa.bindparam("p", type_=sa.JSON(none_as_null=True)),
                sa.bindparam("a", type_=sa.JSON(none_as_null=True)),
            ),
            changed,
        )


def _update_activities(connection: Any, up: bool) -> None:
    """Payload first, in the vocabulary the row is in; then `kind` and `entity_type`."""
    rows = connection.execute(
        sa.text("SELECT id, entity_type, kind, payload FROM activities")
    ).fetchall()
    changed = []
    for row_id, entity, kind, payload in rows:
        new = _rewrite_activity(entity, kind, payload, up)
        if new != payload:
            changed.append({"id": row_id, "payload": new})
    if changed:
        connection.execute(
            sa.text("UPDATE activities SET payload = :payload WHERE id = :id").bindparams(
                sa.bindparam("payload", type_=sa.JSON(none_as_null=True))
            ),
            changed,
        )
    for entity_old, entity_new, kind_old, kind_new in ACTIVITY_KINDS:
        source, target = ((entity_old, kind_old), (entity_new, kind_new))
        if not up:
            source, target = target, source
        if source[0] is None:
            connection.execute(
                sa.text("UPDATE activities SET kind = :new WHERE kind = :old"),
                {"old": source[1], "new": target[1]},
            )
        else:
            connection.execute(
                sa.text(
                    "UPDATE activities SET kind = :new WHERE kind = :old AND entity_type = :entity"
                ),
                {"old": source[1], "new": target[1], "entity": source[0]},
            )
    for old, new in ACTIVITY_ENTITIES.items():
        source, target = (old, new) if up else (new, old)
        connection.execute(
            sa.text("UPDATE activities SET entity_type = :new WHERE entity_type = :old"),
            {"old": source, "new": target},
        )


def _case_update(connection: Any, table: str, column: str, mapping: dict[str, str]) -> None:
    """One statement, so a new word that is also an old one is not rewritten twice."""
    if not mapping:
        return
    params = {}
    cases = []
    for index, (old, new) in enumerate(mapping.items()):
        params[f"o{index}"] = old
        params[f"n{index}"] = new
        cases.append(f"WHEN :o{index} THEN :n{index}")
    olds = ", ".join(f":o{index}" for index in range(len(mapping)))
    connection.execute(
        sa.text(
            f"UPDATE {table} SET {column} = CASE {column} {' '.join(cases)} END "  # noqa: S608
            f"WHERE {column} IN ({olds})"
        ),
        params,
    )


def _rewrite_data(connection: Any, up: bool) -> None:
    _update_activities(connection, up)
    for table, column, rewrite in _json_tables(up):
        _update_json(connection, table, column, rewrite)
    _update_proposals(connection, up)
    for (table, column), mapping in COLUMN_VALUES.items():
        _case_update(connection, table, column, mapping if up else _invert(mapping))
    for table in REMINDER_KEY_TABLES:
        for old, new in REMINDER_TYPES.items():
            source, target = (old, new) if up else (new, old)
            connection.execute(
                sa.text(
                    f"UPDATE {table} SET key = :target || substr(key, :cut) "  # noqa: S608
                    "WHERE key LIKE :pattern"
                ),
                {"target": target, "cut": len(source) + 1, "pattern": f"{source}:%"},
            )
    for table, column, old_default, new_default in DEFAULTS:
        default = new_default if up else old_default
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {default}")


# ---------------------------------------------------------------- shared steps


def _load(filename: str) -> Any:
    versions = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        f"pigrocrm_migration_{filename[:4]}", versions / filename
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_statements() -> list[str]:
    out: list[str] = []
    for filename in LEGACY_REVISIONS:
        out.extend(_load(filename).statements())
    return out


def _switch_off_rls(tables: Sequence[str], policy: str) -> None:
    for table in tables:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def _grant_definers(functions: Sequence[str]) -> None:
    connection = op.get_bind()
    role = make_url(get_settings().database_url).username
    exists = (
        role
        and connection.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
        ).first()
    )
    if role and exists:
        quoted = connection.dialect.identifier_preparer.quote(role)
        for function in functions:
            op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO {quoted}")


def _rename_all(up: bool) -> None:
    if up:
        for old, new in TABLE_RENAMES:
            op.execute(f"ALTER TABLE {old} RENAME TO {new}")
        for table, columns in COLUMN_RENAMES.items():
            for old, new in columns.items():
                op.execute(f'ALTER TABLE {table} RENAME COLUMN "{old}" TO "{new}"')
        for table, old, new in CONSTRAINT_RENAMES:
            op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {old} TO {new}")
        for old, new in INDEX_RENAMES:
            op.execute(f"ALTER INDEX {old} RENAME TO {new}")
        for old, new in SEQUENCE_RENAMES:
            op.execute(f"ALTER SEQUENCE {old} RENAME TO {new}")
        return
    for old, new in SEQUENCE_RENAMES:
        op.execute(f"ALTER SEQUENCE {new} RENAME TO {old}")
    for old, new in INDEX_RENAMES:
        op.execute(f"ALTER INDEX {new} RENAME TO {old}")
    for table, old, new in CONSTRAINT_RENAMES:
        op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {new} TO {old}")
    for table, columns in COLUMN_RENAMES.items():
        for old, new in columns.items():
            op.execute(f'ALTER TABLE {table} RENAME COLUMN "{new}" TO "{old}"')
    for old, new in TABLE_RENAMES:
        op.execute(f"ALTER TABLE {new} RENAME TO {old}")


def upgrade() -> None:
    _switch_off_rls(OLD_POLICY_TABLES, "ambito_azienda")
    for table, trigger in OLD_TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    for function in OLD_FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {function}")
    for table, name, _definition in OLD_CHECKS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
    for _table, name, _definition in OLD_LITERAL_INDEXES:
        op.execute(f"DROP INDEX {name}")
    _rename_all(True)
    _rewrite_data(op.get_bind(), True)
    for table, name, definition in NEW_CHECKS:
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} {definition}")
    for _table, _name, definition in NEW_LITERAL_INDEXES:
        op.execute(definition)
    op.execute(NEW_TRIGGER_SQL)
    for statement in statements():
        op.execute(statement)
    _grant_definers(NEW_DEFINER_FUNCTIONS)


def downgrade() -> None:
    _switch_off_rls(list(NEW_POLICIES), "legal_entity_scope")
    for table, trigger in NEW_TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    for function in NEW_FUNCTION_SIGNATURES:
        op.execute(f"DROP FUNCTION IF EXISTS {function}")
    for table, name, _definition in NEW_CHECKS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
    for _table, name, _definition in NEW_LITERAL_INDEXES:
        op.execute(f"DROP INDEX {name}")
    _rewrite_data(op.get_bind(), False)
    _rename_all(False)
    for table, name, definition in OLD_CHECKS:
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} {definition}")
    for _table, _name, definition in OLD_LITERAL_INDEXES:
        op.execute(definition)
    op.execute(_load("0042_work_units_and_approvals.py")._WORK_UNIT_TRIGGER_SQL)
    op.execute(_load("0043_contract_expenses.py")._CONTRACT_EXPENSE_TRIGGER_SQL)
    for statement in _legacy_statements():
        op.execute(statement)
    _grant_definers(OLD_DEFINER_FUNCTIONS)
