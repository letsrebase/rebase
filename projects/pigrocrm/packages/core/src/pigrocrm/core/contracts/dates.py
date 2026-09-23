"""Three dates read off a contract's own columns, none of them stored: REB-352's
§1.5 concentration cap and §1.6 cash-calendar overlay both anchor to a contract's
own dates rather than the calendar, and neither needed a new column on `contracts`
(REB-358 already carries every one of these) or a new migration.

`irrevocability_window_end` mirrors mastro's `irrevocabilityWindowEnd`
(`certainty.ts:189-196`) one for one, translated onto this schema's own columns
(`fine`, `preavviso_disdetta_giorni`) rather than reimplemented from a description.
`renewal_deadline` has no mastro original to mirror -- mastro's own renewal
assumption (`certainty.ts:133-147`) is a human-recorded belief about revenue, not a
date -- so it is derived directly from the two columns a renewal notice actually
depends on. `anniversary_year_bounds` plays mastro's `ceilingPeriod`
(`ceiling.ts:55-75`) for the one basis PigroCRM's own revenue definition can serve
today: `cash_received_contract_year`, anchored to `Contract.inizio` instead of a
jurisdiction pack's own fiscal year.
"""

from datetime import date, timedelta

from pigrocrm.core.contracts.models import Contract


def irrevocability_window_end(contract: Contract, as_of: date) -> date | None:
    """The irrevocability window's inclusive end: serving a termination notice on
    `as_of` still runs the contract `preavviso_disdetta_giorni` more days, so revenue
    through that date is guaranteed regardless of what the counterparty decides
    today. Clipped to the contract's own end when it has one -- the window cannot
    promise a day the contract itself does not reach. `None` once the contract has
    already ended by `as_of`: nothing is guaranteed by a notice period on a contract
    that is already over.
    """
    if contract.fine is not None and contract.fine < as_of:
        return None
    notice_end = as_of + timedelta(days=contract.preavviso_disdetta_giorni)
    if contract.fine is not None and contract.fine < notice_end:
        return contract.fine
    return notice_end


def renewal_deadline(contract: Contract) -> date | None:
    """The last date this contract's own renewal clause can still be exercised --
    `preavviso_rinnovo_giorni` days before `fine`. `None` for a contract with no
    renewal clause (`tipo_rinnovo == 'nessuno'`) or with no known end date: an
    open-ended contract has no date to count backward from, and there is nothing to
    give notice against.
    """
    if contract.tipo_rinnovo == "nessuno" or contract.fine is None:
        return None
    # `ck_contracts_preavviso_rinnovo_required` guarantees this whenever
    # `tipo_rinnovo <> 'nessuno'` -- the check above already ruled that out.
    assert contract.preavviso_rinnovo_giorni is not None
    return contract.fine - timedelta(days=contract.preavviso_rinnovo_giorni)


def anniversary_year_bounds(inizio: date, as_of: date) -> tuple[date, date]:
    """The `[from, to]` inclusive one-year window, anchored to `inizio`'s own month
    and day, that contains `as_of` -- a contract's own anniversary year, the way a
    `percentage_share` concentration cap resets on mastro's own
    `cash_received_contract_year` basis (`ceiling.ts:55-75`), never the calendar
    year. `as_of` must not precede `inizio`: there is no anniversary before a
    contract's own first day, and the caller (never this pure function) is the one
    that knows the friendly, domain-facing way to say so.
    """
    if as_of < inizio:
        raise ValueError("as_of precede l'inizio del contratto")

    def _anniversary(year: int) -> date:
        try:
            return date(year, inizio.month, inizio.day)
        except ValueError:
            # Only reachable for a 29 February anchor in a non-leap `year`.
            return date(year, 2, 28)

    start = _anniversary(as_of.year)
    if start > as_of:
        start = _anniversary(as_of.year - 1)
    end = _anniversary(start.year + 1) - timedelta(days=1)
    return start, end


__all__ = ["anniversary_year_bounds", "irrevocability_window_end", "renewal_deadline"]
