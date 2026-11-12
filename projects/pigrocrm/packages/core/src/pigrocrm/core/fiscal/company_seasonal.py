"""The seasonal worker's 70% of art. 11 c. 4-octies D.Lgs. 446/1997 (REB-1090), as a pure rule.

The law allows, up to 70% of the cost, «ogni lavoratore stagionale impiegato per almeno
centoventi giorni per due periodi d'imposta, a decorrere dal secondo contratto stipulato con lo
stesso datore di lavoro nell'arco temporale di due anni a partire dalla cessazione del precedente
contratto»; Circolare 20/E of 18 May 2016, § 2, reads it so:

* contracts of one worker with the company *chain* while each starts within two years of the end
  of the one before;
* the deduction is due for the contracts of a chain from its second on, in a tax year in which
  the worker was employed, together with some earlier tax year of the chain, at least 120 days
  of effective work (the days of the first contract count, the years need not be consecutive);
* it is 70% of the cost of the days of effective work, leaving out the INAIL premium, which is
  deducted in full elsewhere (Istruzioni IRAP 2026, rigo IS7).

The records are the contract history of `StaffMember.seasonal_contracts`: each contract's dates
and its days of effective work by tax year. A day count the history does not state is a missing
fact, never zero: the answer is then «cannot tell», and the estimate names the worker.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from pigrocrm.core.fiscal.basis_schemas import BasisSeasonalContract

Outcome = Literal["due", "not_due", "unknown"]


@dataclass(frozen=True)
class SeasonalOutcome:
    """What the rule says for one worker in one tax year. `days` is the effective work of the
    contracts the deduction is due for (`due` only); `reason` is the sentence's tail, in Italian,
    for a worker the deduction is not due for or cannot be told for."""

    outcome: Outcome
    days: int = 0
    reason: str = ""


def _add_years(day: dt.date, years: int) -> dt.date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


def _years_of(contract: BasisSeasonalContract, last_year: int) -> range:
    end_year = last_year if contract.end_date is None else min(contract.end_date.year, last_year)
    return range(contract.start_date.year, end_year + 1)


def _chains(
    contracts: list[BasisSeasonalContract], window_years: int
) -> list[list[BasisSeasonalContract]]:
    """The contracts in order of start, cut where one starts more than `window_years` after the
    end of the one before."""
    chains: list[list[BasisSeasonalContract]] = []
    for contract in contracts:
        previous = chains[-1][-1] if chains else None
        if (
            previous is not None
            and previous.end_date is not None
            and contract.start_date <= _add_years(previous.end_date, window_years)
        ):
            chains[-1].append(contract)
        else:
            chains.append([contract])
    return chains


def seasonal_outcome(
    contracts: list[BasisSeasonalContract] | None,
    year: int,
    *,
    min_days: int,
    window_years: int,
) -> SeasonalOutcome:
    """The rule for the tax year `year` over a worker's contract history (`None`: not stated)."""
    if not contracts:
        return SeasonalOutcome(
            "unknown",
            reason="manca lo storico dei contratti con l'azienda (date e giorni lavorati)",
        )
    ordered = sorted(contracts, key=lambda contract: contract.start_date)
    in_year = [c for c in ordered if year in _years_of(c, year)]
    if not in_year:
        return SeasonalOutcome("not_due", reason=f"nessun contratto stagionale nel {year}")
    unstated = sorted(
        {
            years
            for contract in ordered
            if contract.start_date.year <= year
            for years in _years_of(contract, year)
            if years not in contract.worked_days
        }
    )
    if unstated:
        return SeasonalOutcome(
            "unknown",
            reason="mancano i giorni lavorati nel "
            + ", ".join(str(years) for years in unstated)
            + " in almeno un contratto",
        )
    for chain in _chains(ordered, window_years):
        position = {id(contract): index for index, contract in enumerate(chain)}
        active = [c for c in chain if year in _years_of(c, year)]
        if not active:
            continue
        later = [c for c in active if position[id(c)] >= 1]
        if not later:
            return SeasonalOutcome(
                "not_due",
                reason="è il primo contratto con l'azienda o segue il precedente oltre i "
                f"{window_years} anni dalla sua fine: la deduzione parte dal secondo",
            )
        by_year: dict[int, int] = {}
        for contract in chain:
            for worked_year, days in contract.worked_days.items():
                by_year[worked_year] = by_year.get(worked_year, 0) + days
        earlier_best = max(
            (days for worked_year, days in by_year.items() if worked_year < year and days > 0),
            default=0,
        )
        this_year = by_year.get(year, 0)
        if earlier_best == 0:
            return SeasonalOutcome(
                "not_due",
                reason="non c'è un altro periodo d'imposta con giorni lavorati: servono "
                f"{min_days} giorni in due periodi",
            )
        if this_year + earlier_best < min_days:
            return SeasonalOutcome(
                "not_due",
                reason=f"{this_year + earlier_best} giorni lavorati in due periodi d'imposta, "
                f"meno di {min_days}",
            )
        return SeasonalOutcome("due", days=sum(c.worked_days.get(year, 0) for c in later))
    return SeasonalOutcome("not_due", reason=f"nessun contratto stagionale nel {year}")
