"""The rules of a contract and of its rate cards that mirror a database CHECK, shared by
the contract services and by `proposals` (which builds a contract and a card from an
accepted proposal and must refuse the same shapes)."""

from typing import Any

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fields.schemas import EntityType

# Typed as the fields module's own EntityType (not a bare `str`), matching
# CustomerService.ENTITY/DealService.ENTITY exactly -- see those classes for why a
# plain `str` would fail mypy strict at `specs_for`'s own call site.
ENTITY: EntityType = "contract"


def check_payment_terms_together(payload: dict[str, Any]) -> None:
    """Mirrors `ck_contracts_payment_terms_together`: a contract states both of its
    own payment-term facts or neither, never one paired with a term it never set."""
    if (payload.get("giorni_pagamento") is None) != (payload.get("pagamento_fine_mese") is None):
        raise ValidationFailed(
            ENTITY,
            "giorni_pagamento",
            "giorni_pagamento e pagamento_fine_mese vanno impostati insieme, o nessuno dei due",
            expected="entrambi valorizzati oppure entrambi assenti",
        )


def check_renewal_notice(payload: dict[str, Any]) -> None:
    """Mirrors `ck_contracts_preavviso_rinnovo_required`: applicable, and required,
    for every renewal type except 'nessuno'."""
    if payload["tipo_rinnovo"] != "nessuno" and payload.get("preavviso_rinnovo_giorni") is None:
        raise ValidationFailed(
            ENTITY,
            "preavviso_rinnovo_giorni",
            "obbligatorio per ogni tipo di rinnovo diverso da 'nessuno'",
            expected="un numero di giorni",
        )


def check_rate_card_shape(payload: dict[str, Any]) -> None:
    """Mirrors the two conditional CHECKs on `rate_cards`: `ore_minime` only for
    `tipo = 'orario'`, `periodo_erogazione` only for `tipo = 'ricorrente_fisso'`."""
    if payload["tipo"] != "orario" and payload.get("ore_minime") is not None:
        raise ValidationFailed(
            "rate_card",
            "ore_minime",
            "significativo solo per una scheda di tipo 'orario'",
            expected="assente per ogni altro tipo",
        )
    if payload["tipo"] != "ricorrente_fisso" and payload.get("periodo_erogazione") is not None:
        raise ValidationFailed(
            "rate_card",
            "periodo_erogazione",
            "significativo solo per una scheda di tipo 'ricorrente_fisso'",
            expected="assente per ogni altro tipo",
        )
