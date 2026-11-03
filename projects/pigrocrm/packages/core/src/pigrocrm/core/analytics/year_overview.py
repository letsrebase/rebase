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
from pigrocrm.core.fiscal.ceiling import is_statutory_charge_line
from pigrocrm.core.fiscal.pack import PACK_IT_FLAT_RATE
from pigrocrm.core.fiscal.regime import frozen_pack_id
from pigrocrm.core.invoices.models import InvoiceLine
from pigrocrm.core.invoices.pdf import stamp_duty_declaration_virtual
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.money import ZERO_MONEY, sum_money


def build_year(
    repo: AnalyticsRepository, legal_entity: LegalEntity, year: int
) -> tuple[YearOverview, dict[UUID, list[InvoiceLine]]]:
    """The overview and, beside it, the lines of exactly the invoices it lists.

    The invoices first, then the lines of exactly those: an invoice issued between the
    two statements is in neither, so the register and the file always agree.

    The rivalsa is read off the lines any pack tags as its own charge, whatever pack the
    azienda is on today: an invoice keeps the lines it was issued with.
    """
    invoices = repo.year_invoices(year, legal_entity.id)
    lines_by_invoice: dict[UUID, list[InvoiceLine]] = defaultdict(list)
    for line in repo.year_lines([invoice.id for invoice, _ in invoices]):
        lines_by_invoice[line.invoice_id].append(line)

    register: list[YearRegisterRow] = []
    regimes: dict[tuple[str | None, str], tuple[int, Decimal]] = {}
    treatments: dict[tuple[Decimal, str | None, str | None], tuple[int, Decimal]] = {}
    for invoice, customer in invoices:
        # The register holds issued invoices only, and the table's constraints give
        # every one of them a number and a date: a row without either is corrupt data,
        # not something to print around.
        if invoice.number is None or invoice.issue_date is None:
            raise RuntimeError(f"issued invoice {invoice.id} has no number or no issue date")
        snapshot = invoice.snapshot or {}
        # What the document printed, as frozen at issue. A customer who had no partita
        # IVA then has none here, whatever the customer record says today; the live
        # record is only the fallback for an invoice with no snapshot at all.
        cliente = snapshot.get("customer")
        tax = snapshot.get("tax") or {}
        social_security_surcharge = sum_money(
            line.total_price
            for line in lines_by_invoice[invoice.id]
            if is_statutory_charge_line(line)
        )
        register.append(
            YearRegisterRow(
                invoice_id=invoice.id,
                year=year,
                number=invoice.number,
                issue_date=invoice.issue_date,
                customer_id=invoice.customer_id,
                legal_name=(
                    cliente["legal_name"]
                    if cliente is not None
                    else customer.legal_name
                    if customer is not None
                    else "Cliente non disponibile"
                ),
                vat_number=(
                    cliente.get("vat_number")
                    if cliente is not None
                    else customer.vat_number
                    if customer is not None
                    else None
                ),
                sdi_document_type=invoice.sdi_document_type,
                taxable_amount=invoice.taxable_amount,
                social_security_surcharge=social_security_surcharge,
                stamp_duty=invoice.stamp_duty,
                total=invoice.total,
                stamp_duty_declaration=stamp_duty_declaration_virtual(invoice.stamp_duty),
                payment_status=invoice.payment_status,
                payment_date=invoice.payment_date,
            )
        )
        code = tax.get("regime_code")
        regime = (code, frozen_pack_id(code, tax.get("pack_id") or PACK_IT_FLAT_RATE))
        count, total = regimes.get(regime, (0, ZERO_MONEY))
        regimes[regime] = (count + 1, total + invoice.taxable_amount)
        for line in lines_by_invoice[invoice.id]:
            key = (line.vat_rate, line.vat_nature, line.legal_reference)
            count, total = treatments.get(key, (0, ZERO_MONEY))
            treatments[key] = (count + 1, total + line.total_price)

    overview = YearOverview(
        year=year,
        legal_entity_id=legal_entity.id,
        legal_entity_name=legal_entity.name,
        total=YearTotals(
            invoices=len(register),
            taxable_amount=sum_money(row.taxable_amount for row in register),
            social_security_surcharge=sum_money(row.social_security_surcharge for row in register),
            stamp_duty=sum_money(row.stamp_duty for row in register),
            total=sum_money(row.total for row in register),
        ),
        regimes=[
            YearRegime(regime_code=code, pack_id=pack, invoices=count, revenue=total)
            for (code, pack), (count, total) in sorted(
                regimes.items(), key=lambda item: (item[0][0] or "", item[0][1])
            )
        ],
        treatments=[
            YearTreatment(
                vat_rate=rate,
                vat_nature=vat_nature,
                legal_reference=reference,
                lines=count,
                revenue=total,
            )
            for (rate, vat_nature, reference), (count, total) in sorted(
                treatments.items(),
                key=lambda item: (item[0][0], item[0][1] or "", item[0][2] or ""),
            )
        ],
        register=register,
        cancelled=repo.year_annulled(year, legal_entity.id),
    )
    return overview, lines_by_invoice
