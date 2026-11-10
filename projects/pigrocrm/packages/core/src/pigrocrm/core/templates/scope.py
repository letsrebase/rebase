"""The Italian names of template variables.

The model and the schemas are English, but what a person types in a template is Italian
(`{{cliente.ragione_sociale}}`, `{{emittente.partita_iva}}`) and stays so: templates are
content, and the stored ones already say it this way. Every scope builder keeps reading
the English fields and renames the *keys* it hands to the renderer through `italian_keys`.
A field that is not listed keeps its name (`email`, `logo_key`).
"""

from typing import Any

# The fields a party (a customer or a legal entity) has in common.
PARTY_KEYS: dict[str, str] = {
    "legal_name": "ragione_sociale",
    "vat_number": "partita_iva",
    "tax_code": "codice_fiscale",
    "sdi_code": "codice_sdi",
    "certified_email": "pec",
    "address": "indirizzo",
    "postal_code": "cap",
    "city": "comune",
    "province": "provincia",
    "country": "nazione",
    "phone": "telefono",
    "website": "sito_web",
}

LEGAL_ENTITY_KEYS: dict[str, str] = {
    **PARTY_KEYS,
    "name": "nome",
    "signature_key": "firma_key",
    "email_signature": "firma_email",
    "tax_regime": "regime_fiscale",
}

CUSTOMER_KEYS: dict[str, str] = {
    **PARTY_KEYS,
    "legal_entity_id": "azienda_id",
    "status": "stato",
    "payment_days": "giorni_pagamento",
    "payment_end_of_month": "pagamento_fine_mese",
}

TAX_KEYS: dict[str, str] = {
    "regime_code": "codice_regime",
    "default_vat_rate": "aliquota_iva_default",
    "default_vat_nature": "natura_default",
    "legal_reference": "riferimento_normativo",
    "apply_stamp_duty": "applica_bollo",
    "stamp_duty_threshold": "soglia_bollo",
    "stamp_duty_amount": "importo_bollo",
    "payment_terms": "condizioni_pagamento",
    "payment_method": "modalita_pagamento",
    "due_days": "giorni_scadenza",
}


def italian_keys(data: dict[str, Any], keys: dict[str, str]) -> dict[str, Any]:
    """`data` with the top-level keys renamed through `keys`."""
    return {keys.get(name, name): value for name, value in data.items()}
