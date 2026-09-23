"""Proposes which already-recorded, unbilled `work_units` an imported invoice line
billed -- REB-369, translating mastro's tested `day-mapping.ts:66-114`
(`proposeDayMapping`) onto this schema's own day-rate contract shape (design record
`2026-09-23-mastro-invoice-import-onto-pigrocrm-design.md` §6, §7 item 7).

Pure -- no database access, exactly like mastro's own function and like every other
piece of `review_invoice_import` (`invoices/import_review.py`). The caller
(`import_review._propose_day_mappings`) is the one that reads a customer's contracts,
a contract's rate cards and its unbilled `work_units` through their repositories;
this module only ever receives what those reads already produced. `invariant 3`
("agents propose, humans confirm") means this function may only ever suggest
`work_unit_ids` -- linking them to an invoice line is a human's decision, made
through whichever confirm step accepts it, never written here.

Two facts justify no epsilon where mastro needs one (`QUANTITY_EPSILON`,
`day-mapping.ts:47`): mastro's `quantity` is a JavaScript `number` that has already
round-tripped through a `numeric` column and decimal-string parsing, and this
schema's `quantita` columns (`work_units.quantita`, and the `Decimal` a parsed
invoice line's own `quantita` already is) are exact-precision `Decimal`s from end to
end -- summing them never accumulates floating-point residue to absorb.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from pigrocrm.core.contracts.models import RateCard
from pigrocrm.core.money import round_money, sum_money
from pigrocrm.core.work_units.models import WorkUnit


class WorkUnitDayMappingProposal(BaseModel):
    """Mastro's `DayMappingProposal` (`day-mapping.ts:26-41`): which days a line
    billed, the period they span, and whether their rate-card price actually
    reconciles with what the line itself states -- the three facts the issue's own
    acceptance names ("the period the picked days span, how many there are, and the
    amount they price to next to what the document itself states"), never hidden
    inside a single accept/reject boolean."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    work_unit_ids: list[UUID]
    periodo_da: date
    periodo_a: date
    numero_giorni: int
    importo_proposto: Decimal
    importo_riga: Decimal
    importi_coincidono: bool


def resolve_rate_card(rate_cards: Sequence[RateCard], data: date) -> RateCard | None:
    """Mastro's `resolveRateCard` (`rate-card.ts:101-110`): the one rate card, among
    a contract's, in force on `data` -- both `valido_da`/`valido_a` inclusive, a
    `None` `valido_a` meaning still open. Assumes the contract's own cards obey the
    database's own non-overlap exclusion constraint (`ck_rate_cards_no_overlap`), so
    at most one ever matches; this function does not itself re-validate that."""
    for card in rate_cards:
        if card.valido_da <= data and (card.valido_a is None or data <= card.valido_a):
            return card
    return None


def _price_work_unit_on_date(work_unit: WorkUnit, rate_cards: Sequence[RateCard]) -> Decimal | None:
    """Mastro's `priceWorkUnitOnDate` (`work-unit-pricing.ts:25-44`): what one day is
    worth, resolved against whichever rate card is in force on its own `data`.
    `None` when no card covers that date, or the card covering it is not a
    day-rate one -- a day the calendar cannot honestly price stays unpriced, never
    guessed at, mirroring mastro's own restraint exactly."""
    card = resolve_rate_card(rate_cards, work_unit.data)
    if card is None or card.tipo != "giornaliero":
        return None
    return round_money(card.importo * work_unit.quantita)


def propose_day_mapping(
    quantita_riga: Decimal,
    importo_riga: Decimal,
    data_emissione: date,
    eligible_days: Sequence[WorkUnit],
    rate_cards: Sequence[RateCard],
) -> WorkUnitDayMappingProposal | None:
    """Proposes which of `eligible_days` this line billed. Mirrors mastro's own
    signature (`line: { quantity, amount }`, never the whole parsed line or
    `WorkUnit`/`RateCard` row): a narrow, structural interface is what keeps this
    module free of any dependency on the `invoices` schema at all.

    Only ever proposes a *complete* match: the picked days' own quantities must sum
    to exactly `quantita_riga`, taking the oldest eligible days first and never one
    dated after the invoice's own `data_emissione` -- a day worked after the invoice
    that is supposed to have billed it cannot be what it billed. A partial or
    ambiguous match (too few eligible days, or no combination that sums exactly)
    proposes nothing rather than guessing a subset: a human links days by hand
    instead, exactly as the manual invoice form already lets one do.

    `None` when the rate card in force on `data_emissione` is not `"giornaliero"`
    -- this is specifically the day-rate proposal the issue asks for, not a generic
    quantity-matcher for an hourly or a fixed-fee contract, which bill by a period
    or a lump sum a set of days cannot stand in for.
    """
    card_su_emissione = resolve_rate_card(rate_cards, data_emissione)
    if card_su_emissione is None or card_su_emissione.tipo != "giornaliero":
        return None

    candidates = sorted(
        (day for day in eligible_days if day.data <= data_emissione),
        key=lambda day: (day.data, day.id),
    )

    picked: list[WorkUnit] = []
    quantita_raccolta = Decimal("0")
    for day in candidates:
        if quantita_raccolta >= quantita_riga:
            break
        picked.append(day)
        quantita_raccolta += day.quantita

    if not picked:
        return None
    if quantita_raccolta != quantita_riga:
        return None

    importo_proposto = sum_money(_price_work_unit_on_date(day, rate_cards) for day in picked)
    date_scelte = [day.data for day in picked]
    return WorkUnitDayMappingProposal(
        work_unit_ids=[day.id for day in picked],
        periodo_da=min(date_scelte),
        periodo_a=max(date_scelte),
        numero_giorni=len(picked),
        importo_proposto=importo_proposto,
        importo_riga=importo_riga,
        importi_coincidono=importo_proposto == round_money(importo_riga),
    )


__all__ = ["WorkUnitDayMappingProposal", "propose_day_mapping", "resolve_rate_card"]
