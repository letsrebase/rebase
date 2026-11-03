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

# The door a note came through, as a card names it («fonte: sistema»); the other sources
# (`web`, `mcp`, `rebase`) were English words already.
NOTE_SOURCE: dict[str, str] = {"system": "sistema"}

# Why a digest was skipped, as the cron's log line prints it (`saltato (titolare_mancante)`);
# an exception's type name is its own word and passes through `label` unchanged.
DIGEST_SKIP: dict[str, str] = {
    "owner_missing": "titolare_mancante",
    "owner_disabled": "titolare_disattivato",
    "send_rejected": "invio_rifiutato",
    "send_not_configured": "invio_non_configurato",
}

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


def label(vocabulary: dict[str, str], code: str | None) -> str:
    """The Italian word for `code`, or the code itself when the vocabulary has none."""
    if code is None:
        return ""
    return vocabulary.get(code, code)
