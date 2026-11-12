"""The Italian word a person reads for each persisted English code.

The database and the JSON API carry English codes; every sentence, mail, digest line,
CLI line and generated document that names one of them goes through `label`, so the
text is the one it was before the rename (the owner's rule: no visible text changes).
"""

from __future__ import annotations

TASK_STATUS: dict[str, str] = {
    "open": "aperta",
    "completed": "completata",
    "cancelled": "annullata",
}

TASK_ORIGIN: dict[str, str] = {
    "manual": "manuale",
    "automation": "automazione",
    "payment_reminder": "sollecito",
}

APPROVAL_CHANNEL: dict[str, str] = {
    "certified_mail": "posta_certificata",
    "registered_mail": "raccomandata",
    "courier": "corriere",
    "other": "altro",
}

RENEWAL_TYPE: dict[str, str] = {
    "none": "nessuno",
    "explicit": "esplicito",
    "counterparty_option": "opzione_controparte",
    "tacit": "tacito",
}

CONTRACT_STATUS: dict[str, str] = {
    "draft": "bozza",
    "active": "attivo",
    "terminated": "terminato",
    "expired": "scaduto",
}

RATE_CARD_TYPE: dict[str, str] = {
    "fixed_recurring": "ricorrente_fisso",
    "daily": "giornaliero",
    "hourly": "orario",
    "one_off": "una_tantum",
}

RATE_CARD_UNIT: dict[str, str] = {
    "hour": "ora",
    "day": "giorno",
    "month": "mese",
    "year": "anno",
    "flat": "forfait",
}

DELIVERY_PERIOD: dict[str, str] = {
    "monthly": "mensile",
    "quarterly": "trimestrale",
    "annual": "annuale",
    "one_off": "una_tantum",
}

DOCUMENT_TYPE: dict[str, str] = {
    "offer": "offerta",
    "contract": "contratto",
    "minutes": "verbale",
    "document": "documento",
    "invoice": "fattura",
    "invoice_xml": "fattura_xml",
    "hours_report": "rapporto_ore",
}

DOCUMENT_STATUS: dict[str, str] = {
    "draft": "bozza",
    "sent": "inviata",
    "accepted": "accettata",
    "rejected": "rifiutata",
}

TEMPLATE_TYPE: dict[str, str] = {
    "offer": "offerta",
    "contract": "contratto",
    "minutes": "verbale",
    "document": "documento",
    "hours_report": "rapporto_ore",
    "payment_reminder": "sollecito",
}

INVOICE_TYPE: dict[str, str] = {
    "invoice": "fattura",
}

INVOICE_STATUS: dict[str, str] = {
    "draft": "bozza",
    "issued": "emessa",
    "cancelled": "annullata",
    "confirmed": "confermata",
    "consumed": "consumata",
}

PAYMENT_STATUS: dict[str, str] = {
    "unpaid": "da_incassare",
    "paid": "incassato",
}

PROPOSAL_TARGET_TYPE: dict[str, str] = {
    "contract": "contratto",
    "work_unit": "giornata",
}

PROPOSAL_STATUS: dict[str, str] = {
    "pending": "in_attesa",
    "accepted": "accettata",
    "rejected": "rifiutata",
}

WORK_UNIT_STATUS: dict[str, str] = {
    "proposed": "proposto",
    "approved": "approvato",
    "worked": "lavorato",
    "worked_without_approval": "lavorato_senza_approvazione",
    "invoiced": "fatturato",
    "paid": "pagato",
    "disputed": "contestato",
    "revoked": "revocato",
    "rejected": "rifiutato",
    "non_billable": "non_fatturabile",
}

SEND_STATE: dict[str, str] = {
    "draft": "bozza",
    "sending": "in_invio",
    "sent": "inviato",
    "uncertain": "incerto",
    "failed": "fallito",
}

USER_ROLE: dict[str, str] = {
    "collaborator": "collaboratore",
}

PIPELINE_STAGE_CODE: dict[str, str] = {
    "contacted": "contattato",
    "negotiation": "negoziazione",
    "offer": "offerta",
    "lost": "perso",
    "won": "vinto",
}

TASK_RULE: dict[str, str] = {
    "sent_offer_follow_up": "offerta_inviata_richiama",
    "stalled_deal": "deal_fermo",
    "overdue_invoice": "fattura_scaduta",
}

# The `kind` of a contract's expense policy (`contracts.expense_policy`), and the type of a
# reminder (`reminder_preference.type`): codes 0068 renamed that a message lists as the values
# a field accepts.
EXPENSE_POLICY_KIND: dict[str, str] = {
    "not_reimbursable": "non_rimborsabile",
    "reimbursable": "rimborsabile",
    "reimbursable_with_cap": "rimborsabile_con_tetto",
}

REMINDER_TYPE: dict[str, str] = {
    "task_due": "attivita_due",
    "task_overdue": "attivita_overdue",
}

# The door a note came through, as a card names it («fonte: sistema»); the other sources
# (`web`, `mcp`, `rebase`) were English words already.
NOTE_SOURCE: dict[str, str] = {"system": "sistema"}

# The `activities.kind` of the events that were named in Italian, as a timeline line prints
# them. The three kinds of a task (`completed`, `cancelled`, `reopened`) are not here: they
# share their English word with the kinds of other entities, and no card prints a task's.
ACTIVITY_KIND: dict[str, str] = {
    "automation.stage_moved": "automazione.stage_spostato",
    "automation.not_executed": "automazione.non_eseguita",
    "automation.config_changed": "automazione.configurazione_modificata",
    "automation.task_opened": "automazione.attivita_aperta",
    "digest.sent": "digest.inviato",
    "document.imported": "document.importato",
    "drive.account_connected": "drive.account_collegato",
    "drive.account_disconnected": "drive.account_scollegato",
    "drive.credential_revoked": "drive.credenziale_revocata",
    "drive.roots_set": "drive.radici_impostate",
    "gmail.account_connected": "gmail.account_collegato",
    "gmail.account_disconnected": "gmail.account_scollegato",
    "gmail.backfill_run": "gmail.backfill_eseguito",
    "gmail.draft_created": "gmail.bozza_creata",
    "gmail.draft_deleted": "gmail.bozza_eliminata",
    "gmail.draft_edited": "gmail.bozza_modificata",
    "gmail.credential_revoked": "gmail.credenziale_revocata",
    "gmail.settings_changed": "gmail.impostazioni_modificate",
    "gmail.message_sent": "gmail.messaggio_inviato",
    "gmail.message_received": "gmail.messaggio_ricevuto",
    "gmail.payment_reminder_prepared": "gmail.sollecito_preparato",
    "gmail.sync_run": "gmail.sync_eseguito",
    "gmail.send_uncertain": "gmail.invio_incerto",
    "hub.request_linked": "hub.richiesta_collegata",
    "hub.request_received": "hub.richiesta_ricevuta",
    "slack.matches_rebuilt": "slack.abbinamenti_rifatti",
    "slack.manual_match": "slack.abbinamento_manuale",
    "slack.match_removed": "slack.abbinamento_rimosso",
    "slack.channel_set": "slack.canale_impostato",
    "slack.channel_removed": "slack.canale_rimosso",
    "slack.workspace_connected": "slack.workspace_collegato",
    "slack.workspace_disconnected": "slack.workspace_scollegato",
}

# What a person calls each kind of record `NotFound` names, with the ending the participle
# takes («non trovato», «non trovata»). The entity in the error's details stays the machine
# name; only the sentence is in words. An entity without a row reads under its own name.
ENTITY_NOUN: dict[str, tuple[str, str]] = {
    "approval": ("approvazione", "a"),
    "bank_account": ("conto bancario", "o"),
    "bank_movement": ("movimento bancario", "o"),
    "bank_statement": ("estratto conto", "o"),
    "clause_note": ("nota su una clausola", "a"),
    "contract": ("contratto", "o"),
    "contract_email_template": ("modello di email del contratto", "o"),
    "contract_expense": ("spesa del contratto", "a"),
    "cost_category": ("categoria di costo", "a"),
    "movement_match": ("abbinamento del movimento", "o"),
    "organization": ("cliente", "o"),
    "organization_iban": ("IBAN della controparte", "o"),
    "purchase": ("acquisto", "o"),
    "deal": ("deal", "o"),
    "document": ("documento", "o"),
    "document_blob": ("file del documento", "o"),
    "document_version": ("versione del documento", "a"),
    "drive_file": ("file di Drive", "o"),
    "due_item": ("scadenza", "a"),
    "due_item_settlement": ("pagamento della scadenza", "o"),
    "email_draft": ("bozza di email", "a"),
    "engagement": ("incarico", "o"),
    "field_definition": ("campo personalizzato", "o"),
    "fiscal_profile": ("profilo fiscale", "o"),
    "expense_note": ("nota spese", "a"),
    "gmail_message": ("email", "a"),
    "google_account": ("account Google", "o"),
    "mailbox": ("casella email", "a"),
    "inbox_item": ("elemento in arrivo", "o"),
    "invitation": ("invito", "o"),
    "invoice": ("fattura", "a"),
    "invoice_artifact": ("file della fattura", "o"),
    "iva_period_lock": ("chiusura del periodo IVA", "a"),
    "iva_settlement": ("liquidazione IVA", "a"),
    "recurring_expense": ("spesa ricorrente", "a"),
    "legal_entity": ("azienda", "a"),
    "accountant_export": ("esportazione per il commercialista", "a"),
    "accountant_export_link": ("link di condivisione", "o"),
    "declared_tax_figure": ("cifra dichiarata", "a"),
    "f24_group": ("F24", "o"),
    "f24_group_receipt": ("ricevuta dell'F24", "a"),
    "tax_loss": ("perdita fiscale", "a"),
    "tax_advance_method": ("metodo degli acconti", "o"),
    "tax_instalment_plan": ("piano di rate delle imposte", "o"),
    "tax_notice_plan": ("piano di rate di un avviso", "o"),
    "monthly_report": ("rendiconto mensile", "o"),
    "note": ("nota", "a"),
    "payroll_month": ("mese di paga", "o"),
    "payroll_settings": ("impostazioni delle paghe", "a"),
    "period_lock": ("mese chiuso", "o"),
    "person": ("persona", "a"),
    "personal_access_token": ("token", "o"),
    "pipeline_stage": ("stato della pipeline", "o"),
    "proposal": ("proposta", "a"),
    "rate_card": ("scheda tariffaria", "a"),
    "renewal_assumption": ("assunzione di rinnovo", "a"),
    "staff_member": ("persona dell'azienda", "a"),
    "legal_entity_site": ("sede", "a"),
    "staff_payslip": ("cedolino", "o"),
    "task": ("attività", "a"),
    "template": ("template", "o"),
    "tenant": ("spazio", "o"),
    "time_entry": ("voce di ore", "a"),
    "timer": ("timer", "o"),
    "user": ("utente", "o"),
    "work_unit": ("giornata", "a"),
}


def entity_not_found(entity: str, identifier: str) -> str:
    """«cliente 0199-abc non trovato»: the sentence of a record that is not there."""
    noun, ending = ENTITY_NOUN.get(entity, (entity, "o"))
    return f"{noun} {identifier} non trovat{ending}"


# The fields a person fixes on the way to a FatturaPA, as a refusal names them: the record
# and the field in the words of the screen where it is edited («cliente, CAP»), where the
# machine path was `customer.postal_code`. The machine `entity` and `field` stay in the error's
# details; only the sentence is in words.
_PARTY_NOUN: dict[str, str] = {"legal_entity": "azienda emittente", "customer": "cliente"}

_PARTY_FIELD: dict[str, str] = {
    "legal_name": "ragione sociale",
    "address": "indirizzo",
    "postal_code": "CAP",
    "city": "comune",
    "province": "provincia",
    "country": "nazione",
    "vat_number": "partita IVA",
    "tax_code": "codice fiscale",
    "email": "email",
    "certified_email": "PEC",
    "sdi_code": "codice destinatario (SDI)",
}

FIELD_PATH: dict[str, str] = {
    **{
        f"{entity}.{field}": f"{noun}, {word}"
        for entity, noun in _PARTY_NOUN.items()
        for field, word in _PARTY_FIELD.items()
    },
    "invoice.lines": "fattura, righe",
    "invoice.memo": "fattura, causale",
    "invoice.withholding_amount": "fattura, ritenuta",
    "invoice.purchase_order_number": "fattura, numero dell'ordine",
    "invoice.purchase_order_project_code": "fattura, codice CUP",
    "invoice.purchase_order_tender_code": "fattura, codice CIG",
    "invoice_line.description": "riga di fattura, descrizione",
    "invoice_line.unit_of_measure": "riga di fattura, unità di misura",
    "fiscal_profile.legal_reference": "profilo fiscale, riferimento normativo",
    "fiscal_profile.regime_code": "profilo fiscale, codice regime",
}


def field_path(entity: str, field: str) -> str:
    """The words for `entity.field` when `FIELD_PATH` has them, the dotted machine path when not."""
    return FIELD_PATH.get(f"{entity}.{field}", f"{entity}.{field}")


def label(vocabulary: dict[str, str], code: str | None) -> str:
    """The Italian word for `code`, or the code itself when the vocabulary has none."""
    if code is None:
        return ""
    return vocabulary.get(code, code)
