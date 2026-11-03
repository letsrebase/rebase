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


def label(vocabulary: dict[str, str], code: str | None) -> str:
    """The Italian word for `code`, or the code itself when the vocabulary has none."""
    if code is None:
        return ""
    return vocabulary.get(code, code)
