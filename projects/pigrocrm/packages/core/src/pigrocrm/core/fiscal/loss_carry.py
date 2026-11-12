"""The tax losses of earlier years an estimate carries into a later year (REB-1088, taxes design
§ 1).

The law is `fiscal/law_loss_carry.py`: an srl carries a loss without a time limit against no more
than 80% of each later year's taxable income, the first three periods of a new activity against
all of it (art. 84 TUIR); an ordinary person carries a business loss the same way and a loss of an
art or a profession not at all (art. 8 TUIR). This module applies it.

Where the losses come from
--------------------------
1. **Typed** (`tax_losses`, written by `TaxLossService`): the loss a return states for a year of
   origin and what is left of it at the end of a later year, if some was used.
2. **Estimated**, for an srl, as a fallback: a year after the law starts whose own estimated income
   is negative and for which no loss was typed. It carries the estimated loss and is labelled as
   estimated in the payload and in the assumptions, because it is an estimate of a loss, not the
   return's figure. A typed loss for the same year always replaces it.

An ordinary person's estimated loss is that of an art or a profession (the pack's basis is
art. 54 TUIR) and is not carried, which the assumptions say with the source.

Rolling the remainder forward
-----------------------------
Each loss has a remainder at the end of one year (`as_of_year`): its own year when none was used,
the year a typed remainder states otherwise. For every year between that year and the estimated
one, the income of that year (the year's own estimate, before any loss) takes what the rule lets it
and the rest goes on; the oldest loss is used first, which the law does not fix and the assumptions
say. A year whose income cannot be known (the pack has no law for it) stops the roll: the offset
then has no figure and `missing` names the year, since taking the loss as used or as untouched would
be a guess. Typing the remainder at the end of the last year that has no law is the way out.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from pigrocrm.core.fiscal.basis import year_basis
from pigrocrm.core.fiscal.basis_schemas import YearBasis
from pigrocrm.core.fiscal.law_loss_carry import LossRule
from pigrocrm.core.fiscal.loss_schemas import LossOffset, LossTax, LossUse, TaxLossRead
from pigrocrm.core.fiscal.pack import FiscalPack
from pigrocrm.core.fiscal.repository import TaxLossRepository
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.money import ZERO_MONEY, round_money

_HUNDRED = Decimal(100)

# The article each tax's rule is, for the sentences a person reads.
_ARTICLE: dict[LossTax, str] = {"ires": "art. 84 TUIR", "irpef": "art. 8 c. 3 TUIR"}


@dataclass(frozen=True)
class LossStock:
    """A loss that still has something to carry: the year it comes from, what is left of it at the
    end of `as_of_year`, whether it is an estimate and whether the first-periods limit applies
    (`full`). `states_start` is whether the loss says when the activity started (what the 100%
    needs), so the estimate can say why a loss read the general limit."""

    origin_year: int
    remaining: Decimal
    as_of_year: int
    estimated: bool
    full: bool = False
    states_start: bool = False


def stock_from_typed(row: TaxLossRead, rule: LossRule) -> LossStock:
    """The typed loss as something to carry. The first-periods limit applies when the loss states
    the year the activity started and that it is a new productive activity, and its year of origin
    is one of the first `rule.first_periods` from that start (art. 84 c. 2 TUIR)."""
    start = row.activity_start_year
    full = row.new_activity and start is not None and 0 <= row.year - start < rule.first_periods
    return LossStock(
        row.year,
        row.remaining,
        row.remaining_as_of_year,
        estimated=False,
        full=full,
        states_start=start is not None,
    )


def estimated_stocks(
    income_of: Callable[[int], Decimal | None],
    typed_years: set[int],
    first_year: int,
    year: int,
) -> list[LossStock]:
    """The losses the years `first_year` to `year - 1` estimate for themselves, for the years that
    have no typed loss: a negative income carries its own size."""
    found: list[LossStock] = []
    for origin in range(first_year, year):
        if origin in typed_years:
            continue
        income = income_of(origin)
        if income is not None and income < 0:
            found.append(LossStock(origin, round_money(-income), origin, estimated=True))
    return found


def _use(
    income: Decimal, active: list[LossStock], left: dict[int, Decimal], rule: LossRule
) -> dict[int, Decimal]:
    """What each active loss takes off `income`, oldest first, and the new remainders in `left`.
    Each class (the general limit and the first-periods limit) has its own cap, the two together
    never more than the income. The cap of a class is a share of the income, and a loss uses what
    fits under it: `rule.limit_percent` of the income for the general losses."""
    taken: dict[int, Decimal] = {}
    if income <= 0:
        return taken
    caps = {
        False: round_money(income * rule.limit_percent / _HUNDRED),
        True: round_money(income * rule.first_periods_limit_percent / _HUNDRED),
    }
    room = income
    for stock in sorted(active, key=lambda item: item.origin_year):
        take = min(left[stock.origin_year], caps[stock.full], room)
        if take <= 0:
            continue
        taken[stock.origin_year] = take
        left[stock.origin_year] -= take
        caps[stock.full] -= take
        room -= take
    return taken


def apply_losses(
    *,
    tax: LossTax,
    year: int,
    income: Decimal,
    stocks: list[LossStock],
    income_of: Callable[[int], Decimal | None],
    rule: LossRule,
    professional_loss_years: list[int] | None = None,
    professional_source: str | None = None,
) -> LossOffset:
    """What `stocks` take off the `income` of `year`, after rolling each through the years since
    it was last stated. `income_of(y)` is year `y`'s income before any loss (`None` when it cannot
    be known). `professional_loss_years` are the years an ordinary person's estimate closed in a
    loss of an art or a profession, named in the assumptions with `professional_source`, since
    such a loss is not carried."""
    usable = [stock for stock in stocks if stock.as_of_year < year and stock.remaining > 0]
    left = {stock.origin_year: stock.remaining for stock in usable}
    missing: list[str] = []
    rolled: list[int] = []
    if usable:
        for between in range(min(stock.as_of_year for stock in usable) + 1, year):
            active = [
                stock
                for stock in usable
                if stock.as_of_year < between and left[stock.origin_year] > 0
            ]
            if not active:
                continue
            earlier = income_of(between)
            if earlier is None:
                missing.append(
                    f"Non c'è un reddito stimato per il {between}: non si sa quanto delle "
                    f"perdite precedenti il {between} è stato usato. Indica il residuo delle "
                    f"perdite al termine del {between} per riportarle."
                )
                break
            if _use(earlier, active, left, rule):
                rolled.append(between)
    available = {
        stock.origin_year: left[stock.origin_year] for stock in usable if stock.as_of_year < year
    }
    taken = _use(income, usable, left, rule) if not missing else {}
    uses = [
        LossUse(
            origin_year=stock.origin_year,
            estimated=stock.estimated,
            limit_percent=(rule.first_periods_limit_percent if stock.full else rule.limit_percent),
            available=available[stock.origin_year],
            used=taken.get(stock.origin_year, ZERO_MONEY),
            remaining=left[stock.origin_year],
        )
        for stock in sorted(usable, key=lambda item: item.origin_year)
        if available[stock.origin_year] > 0
    ]
    used = round_money(sum((use.used for use in uses), ZERO_MONEY))
    remaining = round_money(sum((use.remaining for use in uses), ZERO_MONEY))
    assumptions = _assumptions(
        tax, uses, usable, rolled, rule, professional_loss_years or [], bool(missing)
    )
    sources = [rule.source]
    if professional_loss_years and professional_source is not None:
        sources.append(professional_source)
    return LossOffset(
        tax=tax,
        year=year,
        income=income,
        used=used,
        remaining=remaining,
        estimated=any(use.estimated for use in uses),
        origins=uses,
        missing=missing,
        assumptions=assumptions,
        sources=sources,
    )


def _years(years: list[int]) -> str:
    return ", ".join(str(item) for item in years)


def _assumptions(
    tax: LossTax,
    uses: list[LossUse],
    stocks: list[LossStock],
    rolled: list[int],
    rule: LossRule,
    professional_years: list[int],
    unknown: bool,
) -> list[str]:
    article = _ARTICLE[tax]
    notes: list[str] = []
    if tax == "ires":
        notes.append(
            f"Le perdite degli anni precedenti si riportano senza limiti di tempo e riducono il "
            f"reddito imponibile fino all'{rule.limit_percent:f}% ({article}, comma 1). Non si "
            "valutano la riduzione per i proventi esenti né il limite per cambio di controllo e "
            "di attività (comma 3)."
        )
    else:
        notes.append(
            f"Una perdita d'impresa si riporta e riduce i redditi d'impresa fino all'"
            f"{rule.limit_percent:f}% ({article}): il profilo non dice la categoria del reddito, "
            "e la stima tratta come d'impresa il reddito che la perdita scritta riduce. I "
            "contributi INPS stimati non tengono conto delle perdite riportate."
        )
    estimated = [use.origin_year for use in uses if use.estimated]
    if estimated:
        notes.append(
            f"La perdita del {_years(estimated)} è quella che la stima calcola per quell'anno, "
            "non una perdita dichiarata: scrivila tra le perdite per usare la cifra della "
            "dichiarazione."
        )
    if any(not stock.full and not stock.estimated and not stock.states_start for stock in stocks):
        notes.append(
            f"Il limite del {rule.first_periods_limit_percent:f}% per le perdite dei primi tre "
            "periodi di una nuova attività (art. 84 comma 2 TUIR) si applica solo se la perdita "
            "indica l'anno di costituzione e la nuova attività: senza, vale il "
            f"{rule.limit_percent:f}%."
        )
    if len(stocks) > 1:
        notes.append(
            "Le perdite si usano dalla più vecchia: la legge non fissa un ordine tra quelle con "
            "limiti diversi."
        )
    if rolled and not unknown:
        notes.append(
            f"Quanto delle perdite si è usato nel {_years(rolled)} è calcolato sui redditi "
            "stimati di quegli anni, non su quelli dichiarati."
        )
    if professional_years:
        notes.append(
            f"Il {_years(professional_years)} chiude in perdita (stima): una perdita di lavoro "
            "autonomo non si riporta agli anni dopo (art. 8 comma 1 TUIR)."
        )
    return notes


def professional_note_years(
    income_of: Callable[[int], Decimal | None], first_year: int, year: int, typed_years: set[int]
) -> list[int]:
    """The years an ordinary person's estimate closed in a loss of an art or a profession with
    no loss typed for them, which the law does not carry."""
    return [
        origin
        for origin in range(first_year, year)
        if origin not in typed_years and (income := income_of(origin)) is not None and income < 0
    ]


def income_before_loss(basis: YearBasis | None) -> Decimal | None:
    """A year's income before any carried loss: revenue less costs, `None` for a year with no
    basis or a basis that deducts no cost."""
    if basis is None or basis.costs is None:
        return None
    return round_money(basis.revenue - basis.costs.total)


def loss_offset_for(
    session: Session,
    legal_entity: LegalEntity,
    pack: FiscalPack,
    tax: LossTax,
    basis: YearBasis,
    *,
    income: Decimal | None = None,
) -> LossOffset | None:
    """What the losses of earlier years do to `tax` in `basis`'s year for one legal entity, `None`
    when the pack carries no loss against that tax in the year or the basis has no income.
    `income` is the year's income when it is not the basis's own: the srl's IRES reads it after
    the variations of the return (REB-1089), since a loss is taken off the income of the year as
    the return states it. Earlier years' estimated incomes stay before any variation. The
    typed losses are read from `tax_losses`; an srl's estimated ones come from the earlier years'
    own income. The income of an earlier year is its estimate before any loss, never a figure
    already reduced by one."""
    year = basis.year
    law = pack.loss_carry_for(year)
    if income is None:
        income = income_before_loss(basis)
    if law is None or income is None:
        return None
    rule = law.company if tax == "ires" else law.person_business
    if rule is None:
        return None
    cache: dict[int, Decimal | None] = {}

    def income_of(other: int) -> Decimal | None:
        if other not in cache:
            cache[other] = income_before_loss(year_basis(session, legal_entity, other, pack))
        return cache[other]

    rows = TaxLossRepository(session).for_entity(legal_entity.id, tax)
    typed_years = {row.origin_year for row in rows}
    first_year = pack.loss_carry[0].from_year
    stocks = [stock_from_typed(TaxLossRead.model_validate(row), rule) for row in rows]
    professional: list[int] = []
    if tax == "ires":
        stocks += estimated_stocks(income_of, typed_years, first_year, year)
    else:
        professional = professional_note_years(income_of, first_year, year, typed_years)
    if not stocks and not professional:
        return None
    return apply_losses(
        tax=tax,
        year=year,
        income=income,
        stocks=stocks,
        income_of=income_of,
        rule=rule,
        professional_loss_years=professional,
        professional_source=law.professional_not_carried_source,
    )


__all__ = [
    "LossStock",
    "apply_losses",
    "estimated_stocks",
    "income_before_loss",
    "loss_offset_for",
    "professional_note_years",
    "stock_from_typed",
]
