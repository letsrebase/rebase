"""The recurring-fee occurrence schedule and the "projected" revenue figure -- REB-375.

REB-352's own signed-off mapping (§1.3, §1.7, §5 item 6) names this the last piece of
"Bring mastro's ledger, invoice import and forecasting into PigroCRM": a contract's own
recurring-fee schedule, read beyond its irrevocability window, plus a human-recorded
`RenewalAssumption`, prorated across its own horizon. This is a **new, separate**
figure: PigroCRM's existing `CashOverview.projected` (analytics/schemas.py) stays
exactly what it is -- a draft/proforma-based figure, computed nowhere near this module
and untouched by it. Nothing here writes to that schema, and nothing there reads this
module.

Every function is pure: no session, no repository, no clock read. `ContractProjectionService`
(contracts/service.py) is the one caller that fetches rows and resolves "today" before
handing them here, so this module is unit-testable with plain constructed objects and
carries none of `db/clock.py`'s ban on the ad-hoc process clock.

Ported one for one from mastro's `certainty.ts` (`github.com/fiorelorenzo/mastro`,
commit `53ef2942`), with two deliberate translations recorded where they happen:
`probability` is a `0..100` integer (`RenewalAssumption.probability`, matching
`Deal.probability`'s own shape) where mastro carries a `0..1` fraction, and every
amount is a `Decimal` rounded through `pigrocrm.core.money.round_money` where mastro
carries exact-integer minor units -- this project's own single authority on rounding,
not a second one invented here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from pigrocrm.core.contracts.dates import irrevocability_window_end
from pigrocrm.core.contracts.models import Contract, RateCard, RenewalAssumption
from pigrocrm.core.contracts.renewal import add_months
from pigrocrm.core.money import ZERO_MONEY, round_money

# The disbursement periods that repeat -- see RateCard.periodo_erogazione
# (contracts/models.py) for the full closed set, which also admits 'una_tantum'
# (handled separately below: one occurrence, not a step).
_PERIOD_STEP_MONTHS: dict[str, int] = {"monthly": 1, "quarterly": 3, "annual": 12}

RECURRING_FEE_TYPE = "fixed_recurring"


@dataclass(frozen=True, slots=True)
class RecurringFeeOccurrence:
    """One expected disbursement of a fixed recurring fee: `data` is the day it is
    recognised, `amount` the amount recognised on it -- one rate card's own
    `amount`, unchanged, per occurrence."""

    data: date
    amount: Decimal


@dataclass(frozen=True, slots=True)
class ContractProjection:
    """The figure `ContractProjectionService.project` returns: `scheduled` is
    every recurring-fee occurrence beyond the irrevocability window and within
    [`da`, `a`), `from_renewal` is the renewal assumption's own prorated share of the
    same window, `total` is their sum -- the genuine "projected" figure the
    Done-when criterion names, distinct from `CashOverview.projected`."""

    contract_id: UUID
    come_di: date
    da: date
    a: date
    irrevocability_window_until_to: date | None
    scheduled: Decimal
    from_renewal: Decimal
    total: Decimal


def generate_recurring_fee_occurrences(
    contract: Contract, rate_cards: Sequence[RateCard], through: date
) -> list[RecurringFeeOccurrence]:
    """Every fixed recurring-fee rate card of `contract`, expanded into its own
    occurrence dates -- one per `delivery_period`, starting at the card's own
    `valid_from` -- through the earliest of the card's own `valid_to`, the contract's
    own `end`, and `through`.

    `through` is the caller's own horizon, never inferred here: an open-ended
    contract (`fine IS NULL`) has no natural stopping point, so how far this expands
    is the query's own concern, exactly mastro's own contract of "occurrences are
    pre-expanded by the caller" (`certainty.ts`'s `RecurringFeeContract.occurrences`).

    A card with no `delivery_period` recorded contributes nothing: there is no
    cadence to project, and inventing one would be exactly the guessed figure this
    slice's own design (mastro's "no fourth certainty tier") refuses to produce.
    """
    occurrences: list[RecurringFeeOccurrence] = []
    for card in rate_cards:
        if card.type != RECURRING_FEE_TYPE or card.delivery_period is None:
            continue
        bound = through
        if card.valid_to is not None and card.valid_to < bound:
            bound = card.valid_to
        if contract.end is not None and contract.end < bound:
            bound = contract.end
        if card.valid_from > bound:
            continue

        if card.delivery_period == "one_off":
            occurrences.append(RecurringFeeOccurrence(data=card.valid_from, amount=card.amount))
            continue

        # Each date is counted from the card's own `valid_from`, never from the previous
        # occurrence: stepping from a clamped date would carry the clamp forward, and a
        # fee anchored on 31 January would fall on the 28th of every later month.
        step = _PERIOD_STEP_MONTHS[card.delivery_period]
        index = 0
        occurrence_date = card.valid_from
        while occurrence_date <= bound:
            occurrences.append(RecurringFeeOccurrence(data=occurrence_date, amount=card.amount))
            index += 1
            occurrence_date = add_months(card.valid_from, index * step)
    return occurrences


def schedule_end(
    contract: Contract, window_end: date, occurrences: Sequence[RecurringFeeOccurrence]
) -> date:
    """The last date `contract`'s own recurring schedule speaks for -- mastro's
    `scheduleEnd` (`certainty.ts:219-233`). A fixed-term contract's schedule ends at
    its own `end`; an open-ended one's ends at the last occurrence this caller
    handed in (or `window_end`, with none), never further than the caller's own
    `generate_recurring_fee_occurrences` was asked to expand."""
    if contract.end is not None:
        return contract.end
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
    assumption's own span (`schedule_end + 1 day` through `horizon_until` inclusive)
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
    horizon_end_exclusive = assumption.horizon_until + timedelta(days=1)
    if assumption_start >= horizon_end_exclusive:
        return ZERO_MONEY

    overlap_start = max(assumption_start, da)
    overlap_end_exclusive = min(horizon_end_exclusive, a)
    if overlap_start >= overlap_end_exclusive:
        return ZERO_MONEY

    total_days = (horizon_end_exclusive - assumption_start).days
    overlap_days = (overlap_end_exclusive - overlap_start).days
    share = assumption.expected_volume * Decimal(assumption.probability) / Decimal(100)
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
    never combined with, `CashOverview.projected`."""
    # An open-ended contract's schedule ends at its last occurrence (`schedule_end`), and the
    # renewal assumption starts the day after: expanded only through `a`, that end would
    # move with the window the caller asked about and the same days would be priced
    # differently from one query to the next. Through the assumption's own horizon it is
    # the schedule's own.
    through = a if assumption is None else max(a, assumption.horizon_until)
    occurrences = generate_recurring_fee_occurrences(contract, rate_cards, through=through)
    window_end = irrevocability_window_end(contract, come_di)
    effective_window_end = window_end if window_end is not None else come_di
    end = schedule_end(contract, effective_window_end, occurrences)

    beyond_window = [
        occurrence
        for occurrence in occurrences
        if effective_window_end < occurrence.data <= end and da <= occurrence.data < a
    ]
    scheduled = round_money(sum((o.amount for o in beyond_window), ZERO_MONEY))
    from_renewal = renewal_assumption_contribution(
        contract, assumption, occurrences, come_di, da, a
    )
    total = round_money(scheduled + from_renewal)

    return ContractProjection(
        contract_id=contract.id,
        come_di=come_di,
        da=da,
        a=a,
        irrevocability_window_until_to=window_end,
        scheduled=scheduled,
        from_renewal=from_renewal,
        total=total,
    )


__all__ = [
    "RECURRING_FEE_TYPE",
    "ContractProjection",
    "RecurringFeeOccurrence",
    "generate_recurring_fee_occurrences",
    "project",
    "renewal_assumption_contribution",
    "schedule_end",
]
