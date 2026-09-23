"""The recurring-fee occurrence schedule and the "projected" revenue figure -- REB-375.

REB-352's own signed-off mapping (§1.3, §1.7, §5 item 6) names this the last piece of
"Bring mastro's ledger, invoice import and forecasting into PigroCRM": a contract's own
recurring-fee schedule, read beyond its irrevocability window, plus a human-recorded
`RenewalAssumption`, prorated across its own horizon. This is a **new, separate**
figure: PigroCRM's existing `CashOverview.proiettato` (analytics/schemas.py) stays
exactly what it is -- a draft/proforma-based figure, computed nowhere near this module
and untouched by it. Nothing here writes to that schema, and nothing there reads this
module.

Every function is pure: no session, no repository, no clock read. `ContractProjectionService`
(contracts/service.py) is the one caller that fetches rows and resolves "today" before
handing them here, so this module is unit-testable with plain constructed objects and
carries none of `db/clock.py`'s ban on the ad-hoc process clock.

Ported one for one from mastro's `certainty.ts` (`github.com/fiorelorenzo/mastro`,
commit `53ef2942`), with two deliberate translations recorded where they happen:
`probabilita` is a `0..100` integer (`RenewalAssumption.probabilita`, matching
`Deal.probabilita`'s own shape) where mastro carries a `0..1` fraction, and every
amount is a `Decimal` rounded through `pigrocrm.core.money.round_money` where mastro
carries exact-integer minor units -- this project's own single authority on rounding,
not a second one invented here.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from pigrocrm.core.contracts.models import Contract, RateCard, RenewalAssumption
from pigrocrm.core.money import ZERO_MONEY, round_money

# The disbursement periods that repeat -- see RateCard.periodo_erogazione
# (contracts/models.py) for the full closed set, which also admits 'una_tantum'
# (handled separately below: one occurrence, not a step).
_PERIOD_STEP_MONTHS: dict[str, int] = {"mensile": 1, "trimestrale": 3, "annuale": 12}

RECURRING_FEE_TIPO = "ricorrente_fisso"


@dataclass(frozen=True, slots=True)
class RecurringFeeOccurrence:
    """One expected disbursement of a fixed recurring fee: `data` is the day it is
    recognised, `importo` the amount recognised on it -- one rate card's own
    `importo`, unchanged, per occurrence."""

    data: date
    importo: Decimal


@dataclass(frozen=True, slots=True)
class ContractProjection:
    """The figure `ContractProjectionService.project` returns: `programmato` is
    every recurring-fee occurrence beyond the irrevocability window and within
    [`da`, `a`), `da_rinnovo` is the renewal assumption's own prorated share of the
    same window, `totale` is their sum -- the genuine "projected" figure the
    Done-when criterion names, distinct from `CashOverview.proiettato`."""

    contract_id: UUID
    come_di: date
    da: date
    a: date
    finestra_irrevocabilita_fino_al: date | None
    programmato: Decimal
    da_rinnovo: Decimal
    totale: Decimal


def _add_months(start: date, months: int) -> date:
    """`start` plus a whole number of months, day-of-month clamped to the target
    month's own length -- the same clamp `invoices/scadenza.py` already applies for
    an end-of-month due date, so 31 January plus one month lands on 28/29 February
    rather than raising."""
    total = start.year * 12 + (start.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def generate_recurring_fee_occurrences(
    contract: Contract, rate_cards: Sequence[RateCard], through: date
) -> list[RecurringFeeOccurrence]:
    """Every fixed recurring-fee rate card of `contract`, expanded into its own
    occurrence dates -- one per `periodo_erogazione`, starting at the card's own
    `valido_da` -- through the earliest of the card's own `valido_a`, the contract's
    own `fine`, and `through`.

    `through` is the caller's own horizon, never inferred here: an open-ended
    contract (`fine IS NULL`) has no natural stopping point, so how far this expands
    is the query's own concern, exactly mastro's own contract of "occurrences are
    pre-expanded by the caller" (`certainty.ts`'s `RecurringFeeContract.occurrences`).

    A card with no `periodo_erogazione` recorded contributes nothing: there is no
    cadence to project, and inventing one would be exactly the guessed figure this
    slice's own design (mastro's "no fourth certainty tier") refuses to produce.
    """
    occurrences: list[RecurringFeeOccurrence] = []
    for card in rate_cards:
        if card.tipo != RECURRING_FEE_TIPO or card.periodo_erogazione is None:
            continue
        bound = through
        if card.valido_a is not None and card.valido_a < bound:
            bound = card.valido_a
        if contract.fine is not None and contract.fine < bound:
            bound = contract.fine
        if card.valido_da > bound:
            continue

        if card.periodo_erogazione == "una_tantum":
            occurrences.append(RecurringFeeOccurrence(data=card.valido_da, importo=card.importo))
            continue

        step = _PERIOD_STEP_MONTHS[card.periodo_erogazione]
        occurrence_date = card.valido_da
        while occurrence_date <= bound:
            occurrences.append(RecurringFeeOccurrence(data=occurrence_date, importo=card.importo))
            occurrence_date = _add_months(occurrence_date, step)
    return occurrences


def irrevocability_window_end(contract: Contract, come_di: date) -> date | None:
    """The irrevocability window's inclusive end: termination notice served on
    `come_di` still runs the contract `preavviso_disdetta_giorni` more days --
    mastro's `irrevocabilityWindowEnd` (`certainty.ts:197-206`). `None` once the
    contract has already ended by `come_di`: a finished contract guarantees nothing
    further, and there is no window left to be beyond."""
    if contract.fine is not None and contract.fine < come_di:
        return None
    notice_end = come_di + timedelta(days=contract.preavviso_disdetta_giorni)
    if contract.fine is not None and contract.fine < notice_end:
        return contract.fine
    return notice_end


def schedule_end(
    contract: Contract, window_end: date, occurrences: Sequence[RecurringFeeOccurrence]
) -> date:
    """The last date `contract`'s own recurring schedule speaks for -- mastro's
    `scheduleEnd` (`certainty.ts:219-233`). A fixed-term contract's schedule ends at
    its own `fine`; an open-ended one's ends at the last occurrence this caller
    handed in (or `window_end`, with none), never further than the caller's own
    `generate_recurring_fee_occurrences` was asked to expand."""
    if contract.fine is not None:
        return contract.fine
    if not occurrences:
        return window_end
    return max(occurrence.data for occurrence in occurrences)


def renewal_assumption_contribution(
    contract: Contract,
    assumption: RenewalAssumption | None,
    occurrences: Sequence[RecurringFeeOccurrence],
    come_di: date,
    da: date,
    a: date,
) -> Decimal:
    """A contract's projected renewal contribution over `[da, a)` -- mastro's
    `renewalAssumptionContribution` (`certainty.ts:344-385`). Zero with no assumption
    recorded (never a guessed pace), zero once the contract has already ended, zero
    once the assumption's own horizon has already elapsed before the schedule even
    stops -- otherwise `volume_atteso * probabilita/100`, spread evenly across the
    assumption's own span (`schedule_end + 1 day` through `orizzonte_al` inclusive)
    and restricted to its overlap with `[da, a)`, exact `Decimal` division rounded
    once at the end through `round_money`, the project's own single rounding
    authority."""
    if assumption is None:
        return ZERO_MONEY
    window_end = irrevocability_window_end(contract, come_di)
    if window_end is None:
        return ZERO_MONEY

    end = schedule_end(contract, window_end, occurrences)
    assumption_start = end + timedelta(days=1)
    horizon_end_exclusive = assumption.orizzonte_al + timedelta(days=1)
    if assumption_start >= horizon_end_exclusive:
        return ZERO_MONEY

    overlap_start = max(assumption_start, da)
    overlap_end_exclusive = min(horizon_end_exclusive, a)
    if overlap_start >= overlap_end_exclusive:
        return ZERO_MONEY

    total_days = (horizon_end_exclusive - assumption_start).days
    overlap_days = (overlap_end_exclusive - overlap_start).days
    share = assumption.volume_atteso * Decimal(assumption.probabilita) / Decimal(100)
    return round_money(share * Decimal(overlap_days) / Decimal(total_days))


def project(
    contract: Contract,
    rate_cards: Sequence[RateCard],
    assumption: RenewalAssumption | None,
    come_di: date,
    da: date,
    a: date,
) -> ContractProjection:
    """The genuine "projected" figure over `[da, a)`: every recurring-fee occurrence
    beyond the irrevocability window plus the renewal assumption's own prorated
    share -- mastro's `projectedAmount` (`certainty.ts:403-433`). Distinct from, and
    never combined with, `CashOverview.proiettato`."""
    occurrences = generate_recurring_fee_occurrences(contract, rate_cards, through=a)
    window_end = irrevocability_window_end(contract, come_di)
    effective_window_end = window_end if window_end is not None else come_di
    end = schedule_end(contract, effective_window_end, occurrences)

    beyond_window = [
        occurrence
        for occurrence in occurrences
        if effective_window_end < occurrence.data <= end and da <= occurrence.data < a
    ]
    programmato = round_money(sum((o.importo for o in beyond_window), ZERO_MONEY))
    da_rinnovo = renewal_assumption_contribution(contract, assumption, occurrences, come_di, da, a)
    totale = round_money(programmato + da_rinnovo)

    return ContractProjection(
        contract_id=contract.id,
        come_di=come_di,
        da=da,
        a=a,
        finestra_irrevocabilita_fino_al=window_end,
        programmato=programmato,
        da_rinnovo=da_rinnovo,
        totale=totale,
    )


__all__ = [
    "RECURRING_FEE_TIPO",
    "ContractProjection",
    "RecurringFeeOccurrence",
    "generate_recurring_fee_occurrences",
    "irrevocability_window_end",
    "project",
    "renewal_assumption_contribution",
    "schedule_end",
]
