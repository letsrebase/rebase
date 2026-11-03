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
    """Mirrors the conditional CHECKs on `rate_cards`: `minimum_hours` only for
    `type = 'hourly'`; `expected_quantity` only for a day or hour card; `delivery_period`
    for a `fixed_recurring` card, or for a day or hour card only together with
    `expected_quantity` and as one_off, monthly or annual."""
    kind = payload["type"]
    day_or_hour = kind in ("daily", "hourly")
    period = payload.get("delivery_period")
    quantity = payload.get("expected_quantity")
    if kind != "hourly" and payload.get("minimum_hours") is not None:
        raise ValidationFailed(
            "rate_card",
            "minimum_hours",
            "significativo solo per una scheda di tipo 'hourly'",
            expected="assente per ogni altro tipo",
        )
    if quantity is not None and not day_or_hour:
        raise ValidationFailed(
            "rate_card",
            "expected_quantity",
            "significativo solo per una scheda di tipo 'daily' o 'hourly'",
            expected="assente per ogni altro tipo",
        )
    if period is not None and kind != "fixed_recurring" and not day_or_hour:
        raise ValidationFailed(
            "rate_card",
            "delivery_period",
            "significativo solo per una scheda di tipo 'fixed_recurring', 'daily' o 'hourly'",
            expected="assente per ogni altro tipo",
        )
    if day_or_hour and (period is None) != (quantity is None):
        raise ValidationFailed(
            "rate_card",
            "expected_quantity",
            "expected_quantity e delivery_period vanno impostati insieme, o nessuno dei due",
            expected="entrambi valorizzati oppure entrambi assenti",
        )
    if day_or_hour and period is not None and period not in ("one_off", "monthly", "annual"):
        raise ValidationFailed(
            "rate_card",
            "delivery_period",
            "per una scheda 'daily' o 'hourly' ammette solo one_off, monthly o annual",
            expected="one_off, monthly o annual",
        )
