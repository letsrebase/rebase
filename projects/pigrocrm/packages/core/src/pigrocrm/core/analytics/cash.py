"""The year as cash, month by month, and the contract dates overlaid on it
(`AnalyticsService.cash_overview`)."""

from datetime import date
from uuid import UUID

from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.analytics.schemas import CashBase, CashMonth, CashOverview, ContractDateMarker
from pigrocrm.core.contracts.dates import irrevocability_window_end, renewal_deadline
from pigrocrm.core.contracts.repository import ContractRepository
from pigrocrm.core.db import today_local
from pigrocrm.core.money import ZERO_MONEY, round_money, share, sum_money


def contract_date_markers(
    contracts: ContractRepository, anno: int, azienda_id: UUID | None = None
) -> list[ContractDateMarker]:
    """REB-352 §1.6's overlay: every irrevocability-window close and renewal
    deadline that falls inside `anno`'s calendar, across every non-deleted
    contract. The irrevocability date is measured "as of" today regardless of
    `anno` -- a notice period is a forward-looking promise, never a fact about a
    past year -- so a past `anno` never grows one and a future one only does
    once today's window actually reaches into it. One azienda's calendar carries its
    own contracts' dates (REB-630).
    """
    year_start = date(anno, 1, 1)
    year_end = date(anno, 12, 31)
    oggi = today_local()
    markers: list[ContractDateMarker] = []
    for contract in contracts.list_active(azienda_id=azienda_id):
        end = irrevocability_window_end(contract, oggi)
        if end is not None and year_start <= end <= year_end:
            markers.append(
                ContractDateMarker(
                    contract_id=contract.id,
                    titolo=contract.titolo,
                    customer_id=contract.customer_id,
                    tipo="fine_irrevocabilita",
                    data=end,
                )
            )
        deadline = renewal_deadline(contract)
        if deadline is not None and year_start <= deadline <= year_end:
            markers.append(
                ContractDateMarker(
                    contract_id=contract.id,
                    titolo=contract.titolo,
                    customer_id=contract.customer_id,
                    tipo="scadenza_rinnovo",
                    data=deadline,
                )
            )
    markers.sort(key=lambda m: (m.data, m.titolo))
    return markers


def cash_overview(
    repo: AnalyticsRepository,
    contracts: ContractRepository,
    anno: int,
    base: CashBase,
    azienda_id: UUID | None,
) -> CashOverview:
    """The year as cash, month by month (`CashOverview`): every figure is a SUM the
    repository produced plus additions done once, here. `base` says which month a
    document falls in (ORB-133). The year's totals agree under the two readings except
    for a document whose declared period and whose money fall in different years: that
    one is in one year's view and not the other's. `azienda_id` narrows every sum to
    one azienda (REB-630, spec 2026-10-03 §1.9); without it the cash of every azienda
    adds up, which is what «tutte» exists to show, and a cost without a deal is counted
    there alone (§1.7)."""
    incassato = repo.monthly_incassato(anno, base, azienda_id)
    da_incassare = repo.monthly_da_incassare(anno, base, azienda_id)
    bozze = repo.monthly_bozze(anno, base, azienda_id)
    costi = repo.monthly_costi(anno, azienda_id)
    months = range(1, 13)
    zero = ZERO_MONEY
    stack_andamento = max(
        (incassato.get(m, zero) + costi.get(m, zero) for m in months), default=zero
    )
    stack_proiezione = max(
        (
            incassato.get(m, zero)
            + da_incassare.get(m, zero)
            + bozze.get(m, zero)
            + costi.get(m, zero)
            for m in months
        ),
        default=zero,
    )

    mesi = [
        CashMonth(
            anno=anno,
            mese=m,
            incassato=incassato.get(m, zero),
            da_incassare=da_incassare.get(m, zero),
            bozze=bozze.get(m, zero),
            costi=costi.get(m, zero),
            pila_andamento=round_money(incassato.get(m, zero) + costi.get(m, zero)),
            pila_proiezione=round_money(
                incassato.get(m, zero)
                + da_incassare.get(m, zero)
                + bozze.get(m, zero)
                + costi.get(m, zero)
            ),
            quote_andamento={
                "incassato": share(incassato.get(m, zero), stack_andamento),
                "costi": share(costi.get(m, zero), stack_andamento),
            },
            quote_proiezione={
                "incassato": share(incassato.get(m, zero), stack_proiezione),
                "da_incassare": share(da_incassare.get(m, zero), stack_proiezione),
                "bozze": share(bozze.get(m, zero), stack_proiezione),
                "costi": share(costi.get(m, zero), stack_proiezione),
            },
        )
        for m in months
    ]
    tot_incassato = sum_money(incassato.values())
    tot_da_incassare = sum_money(da_incassare.values())
    tot_bozze = sum_money(bozze.values())
    tot_costi = sum_money(costi.values())
    proiettato = round_money(tot_incassato + tot_da_incassare + tot_bozze)
    return CashOverview(
        anno=anno,
        base=base,
        azienda_id=azienda_id,
        incassato=tot_incassato,
        da_incassare=tot_da_incassare,
        bozze=tot_bozze,
        proiettato=proiettato,
        costi=tot_costi,
        lordo_effettivo=round_money(tot_incassato - tot_costi),
        lordo_proiettato=round_money(proiettato - tot_costi),
        mesi=mesi,
        scadenze_contrattuali=contract_date_markers(contracts, anno, azienda_id),
    )
