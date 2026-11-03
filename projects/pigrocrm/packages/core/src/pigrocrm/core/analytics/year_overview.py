"""The year of one azienda as an accountant reads it (`AnalyticsService.year_overview`
and `build_year_csv`)."""

from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.analytics.schemas import (
    YearOverview,
    YearRegime,
    YearRegisterRow,
    YearTotals,
    YearTreatment,
)
from pigrocrm.core.emitter.models import LegalEntity
from pigrocrm.core.fiscal.ceiling import is_statutory_charge_line
from pigrocrm.core.fiscal.pack import PACK_IT_FLAT_RATE
from pigrocrm.core.fiscal.regime import frozen_pack_id
from pigrocrm.core.invoices.models import InvoiceLine
from pigrocrm.core.invoices.pdf import dichiarazione_bollo_virtuale
from pigrocrm.core.money import ZERO_MONEY, sum_money


def build_year(
    repo: AnalyticsRepository, azienda: LegalEntity, anno: int
) -> tuple[YearOverview, dict[UUID, list[InvoiceLine]]]:
    """The overview and, beside it, the lines of exactly the invoices it lists.

    The invoices first, then the lines of exactly those: an invoice issued between the
    two statements is in neither, so the register and the file always agree.

    The rivalsa is read off the lines any pack tags as its own charge, whatever pack the
    azienda is on today: an invoice keeps the lines it was issued with.
    """
    invoices = repo.year_invoices(anno, azienda.id)
    lines_by_invoice: dict[UUID, list[InvoiceLine]] = defaultdict(list)
    for line in repo.year_lines([invoice.id for invoice, _ in invoices]):
        lines_by_invoice[line.invoice_id].append(line)

    registro: list[YearRegisterRow] = []
    regimi: dict[tuple[str | None, str], tuple[int, Decimal]] = {}
    trattamenti: dict[tuple[Decimal, str | None, str | None], tuple[int, Decimal]] = {}
    for invoice, customer in invoices:
        # The register holds issued invoices only, and the table's constraints give
        # every one of them a number and a date: a row without either is corrupt data,
        # not something to print around.
        if invoice.numero is None or invoice.data_emissione is None:
            raise RuntimeError(f"issued invoice {invoice.id} has no number or no issue date")
        snapshot = invoice.snapshot or {}
        # What the document printed, as frozen at issue. A customer who had no partita
        # IVA then has none here, whatever the customer record says today; the live
        # record is only the fallback for an invoice with no snapshot at all.
        cliente = snapshot.get("cliente")
        fiscale = snapshot.get("fiscale") or {}
        rivalsa = sum_money(
            line.prezzo_totale
            for line in lines_by_invoice[invoice.id]
            if is_statutory_charge_line(line)
        )
        registro.append(
            YearRegisterRow(
                invoice_id=invoice.id,
                anno=anno,
                numero=invoice.numero,
                data_emissione=invoice.data_emissione,
                customer_id=invoice.customer_id,
                ragione_sociale=(
                    cliente["ragione_sociale"]
                    if cliente is not None
                    else customer.ragione_sociale
                    if customer is not None
                    else "Cliente non disponibile"
                ),
                partita_iva=(
                    cliente.get("partita_iva")
                    if cliente is not None
                    else customer.partita_iva
                    if customer is not None
                    else None
                ),
                tipo_documento=invoice.tipo_documento,
                imponibile=invoice.imponibile,
                rivalsa=rivalsa,
                bollo=invoice.bollo,
                totale=invoice.totale,
                dichiarazione_bollo=dichiarazione_bollo_virtuale(invoice.bollo),
                stato_pagamento=invoice.stato_pagamento,
                data_incasso=invoice.data_incasso,
            )
        )
        codice = fiscale.get("codice_regime")
        regime = (codice, frozen_pack_id(codice, fiscale.get("pack_id") or PACK_IT_FLAT_RATE))
        count, total = regimi.get(regime, (0, ZERO_MONEY))
        regimi[regime] = (count + 1, total + invoice.imponibile)
        for line in lines_by_invoice[invoice.id]:
            key = (line.aliquota_iva, line.natura, line.riferimento_normativo)
            count, total = trattamenti.get(key, (0, ZERO_MONEY))
            trattamenti[key] = (count + 1, total + line.prezzo_totale)

    overview = YearOverview(
        anno=anno,
        azienda_id=azienda.id,
        azienda_nome=azienda.nome,
        totali=YearTotals(
            fatture=len(registro),
            imponibile=sum_money(row.imponibile for row in registro),
            rivalsa=sum_money(row.rivalsa for row in registro),
            bollo=sum_money(row.bollo for row in registro),
            totale=sum_money(row.totale for row in registro),
        ),
        regimi=[
            YearRegime(codice_regime=codice, pack_id=pack, fatture=count, ricavi=total)
            for (codice, pack), (count, total) in sorted(
                regimi.items(), key=lambda item: (item[0][0] or "", item[0][1])
            )
        ],
        trattamenti=[
            YearTreatment(
                aliquota_iva=aliquota,
                natura=natura,
                riferimento_normativo=riferimento,
                righe=count,
                ricavi=total,
            )
            for (aliquota, natura, riferimento), (count, total) in sorted(
                trattamenti.items(),
                key=lambda item: (item[0][0], item[0][1] or "", item[0][2] or ""),
            )
        ],
        registro=registro,
        annullate=repo.year_annulled(anno, azienda.id),
    )
    return overview, lines_by_invoice
