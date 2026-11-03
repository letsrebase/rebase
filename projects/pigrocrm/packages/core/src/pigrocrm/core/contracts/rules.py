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
    if (payload.get("payment_days") is None) != (payload.get("payment_end_of_month") is None):
        raise ValidationFailed(
            ENTITY,
            "payment_days",
            "payment_days e payment_end_of_month vanno impostati insieme, o nessuno dei due",
            expected="entrambi valorizzati oppure entrambi assenti",
        )


def check_renewal_notice(payload: dict[str, Any]) -> None:
    """Mirrors `ck_contracts_notice_renewal_required`: applicable, and required,
    for every renewal type except 'none'."""
    if payload["renewal_type"] != "none" and payload.get("renewal_notice_days") is None:
        raise ValidationFailed(
            ENTITY,
            "renewal_notice_days",
            "obbligatorio per ogni tipo di rinnovo diverso da 'none'",
            expected="un numero di giorni",
        )


def check_rate_card_shape(payload: dict[str, Any]) -> None:
    """Mirrors the two conditional CHECKs on `rate_cards`: `minimum_hours` only for
    `type = 'hourly'`, `delivery_period` only for `type = 'fixed_recurring'`."""
    if payload["type"] != "hourly" and payload.get("minimum_hours") is not None:
        raise ValidationFailed(
            "rate_card",
            "minimum_hours",
            "significativo solo per una scheda di tipo 'hourly'",
            expected="assente per ogni altro tipo",
        )
    if payload["type"] != "fixed_recurring" and payload.get("delivery_period") is not None:
        raise ValidationFailed(
            "rate_card",
            "delivery_period",
            "significativo solo per una scheda di tipo 'fixed_recurring'",
            expected="assente per ogni altro tipo",
        )
