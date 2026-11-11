"""The one revenue and cost basis of a legal entity's year (REB-1071, taxes design § 2.1).

An estimate is computed from a year's own figures, and which figures belong to a year depends
on the regime: a person's and the flat-rate regime's income counts what was **collected** and
**paid** in the year (cash), a company's counts what **accrued** in it. The pack says which
(`TaxYearLaw.income_basis`, `fiscal/law_income_basis.py`, with the law's own words), and
`year_basis` is the only function that turns that into numbers. Every path that computes an
estimate reads it: `AnalyticsService.get_fiscal_estimate`, and with it the REST route, the MCP
tool and the economic overview. Before it, `/api/analytics/fiscal` summed the invoices issued in
the year while the economic overview passed what was collected, so the same year had two
revenues.

* **Cash.** Revenue is the taxable share of every payment settled in the year, by the date of
  the settlement (the due item's, the same reading the ceiling uses: an invoice issued in
  December and paid in January belongs to January, and one paid in instalments across 31
  December to both years for what arrived in each), less the pack's tagged charge lines (the
  flat-rate regime's INPS surcharge, `count_by_the_coefficient`), whose place in the base is
  still to verify with the accountant. Costs, for a regime that deducts them, are the cost of
  the purchases paid in the year (a purchase with due items by what its settlements paid, one
  with none by its document date, said as an assumption, since purchases do not write due
  items yet) and the payroll items settled in the year.
* **Accrual.** Revenue is the taxable amount of the invoices whose competence (the accrual
  period they declare, else the issue date) falls in the legal entity's financial year; costs
  are the purchases by document date, and the personnel cost of the months worked as the
  staff module estimates it (`PayrollService.personnel_cost_by_kind`), told apart as purchases,
  staff and directors because IRAP reads them as different lines. The financial year is the one
  closing in `year`, by the legal entity's own year end (31 December unless its statutes say
  otherwise).

A regime whose income is the revenue times a coefficient (the flat-rate regime) has no costs to
deduct, so none are computed and `costs` is `None`. What a reader could expect in the figure
and is not in it is `left_out`, and what is in it on a stand-in for a fact the product does not
hold yet is `assumptions`, each with the reason, so a figure can say what it was computed from.
Nothing here is a tax: it is the input every tax estimate reads.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ColumnElement, and_, exists, func, select
from sqlalchemy.orm import Session

from pigrocrm.core.due_items.models import PAYROLL_SOURCE_KIND, DueItem, DueItemSettlement
from pigrocrm.core.fiscal.basis_schemas import BasisCosts, BasisEmployee, BasisLine, YearBasis
from pigrocrm.core.fiscal.ceiling import (
    charge_line_clause,
    collected_charge_lines_for_calendar_year,
    paid_revenue_for_calendar_year,
)
from pigrocrm.core.fiscal.law_income_basis import IncomeBasisLaw
from pigrocrm.core.fiscal.pack import FiscalPack
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.money import ZERO_MONEY, round_money
from pigrocrm.core.purchases.models import Purchase
from pigrocrm.core.staff.payroll import PayrollService

# The source kind of the due item a purchase will write (`due_items/models.py::SOURCE_KINDS`).
PURCHASE_SOURCE_KIND = "purchase"


def year_basis(
    session: Session, legal_entity: LegalEntity, year: int, pack: FiscalPack
) -> YearBasis | None:
    """The year of `legal_entity` on the basis `pack` states for it, `None` when the pack's law
    for `year` states none (a year before the law, or a regime with no income basis): a basis is
    never chosen here. One legal entity's always: every figure reads its own rows alone."""
    law = pack.law_for(year)
    if law is None or law.income_basis is None:
        return None
    needs_costs = law.flat_rate is None
    if law.income_basis.basis == "cash":
        return _cash(session, legal_entity, year, pack, law.income_basis, needs_costs)
    return _accrual(session, legal_entity, year, pack, law.income_basis, needs_costs)


# --- cash ---------------------------------------------------------------------------------


def _cash(
    session: Session,
    legal_entity: LegalEntity,
    year: int,
    pack: FiscalPack,
    rule: IncomeBasisLaw,
    needs_costs: bool,
) -> YearBasis:
    first, last = date(year, 1, 1), date(year, 12, 31)
    left_out: list[BasisLine] = []
    assumptions: list[BasisLine] = []

    collected = paid_revenue_for_calendar_year(session, year, legal_entity.id)
    surcharge = collected_charge_lines_for_calendar_year(
        session, year, _untaxed_markers(pack, year), legal_entity.id
    )
    if surcharge != 0:
        left_out.append(_surcharge_line(surcharge))
    # What the year's invoicing left for later: issued in the year, not collected by its end.
    not_collected = round_money(
        _sum_taxable(session, legal_entity.id, Invoice.year == year)
        - paid_revenue_for_calendar_year(session, year, legal_entity.id, issued_in_year=True)
    )
    if not_collected != 0:
        left_out.append(
            BasisLine(
                code="not_collected",
                amount=not_collected,
                reason=(
                    f"Fatture del {year} non ancora incassate al 31 dicembre: contano "
                    "nell'anno in cui arriva il pagamento."
                ),
            )
        )

    costs: BasisCosts | None = None
    if needs_costs:
        paid, untracked, untracked_count = _purchases_paid(session, legal_entity.id, first, last)
        purchases = round_money(paid + untracked)
        staff = _payroll_paid(session, legal_entity.id, first, last)
        costs = BasisCosts(
            purchases=purchases,
            staff=staff,
            director=ZERO_MONEY,
            total=round_money(purchases + staff),
        )
        if untracked_count:
            assumptions.append(
                BasisLine(
                    code="purchases_by_document_date",
                    amount=untracked,
                    count=untracked_count,
                    reason=(
                        "Gli acquisti non registrano ancora quando sono pagati: contano alla "
                        "data del documento."
                    ),
                )
            )
        left_out += _shared_purchases(session, first, last)

    return YearBasis(
        year=year,
        legal_entity_id=legal_entity.id,
        basis="cash",
        period_from=first,
        period_to=last,
        label=f"Incassato nel {year}, secondo il principio di cassa",
        wording=rule.wording,
        source=rule.source,
        revenue=round_money(collected - surcharge),
        costs=costs,
        left_out=left_out,
        assumptions=assumptions,
    )


def _purchases_paid(
    session: Session, legal_entity_id: UUID, first: date, last: date
) -> tuple[Decimal, Decimal, int]:
    """What the purchases cost that were paid in `[first, last]`: `(paid, untracked, count)`.
    `paid` is each settlement's share of the cost of a purchase that has due items (a live
    settlement by its own date, the cost times what it paid over the item's amount); `untracked`
    is the cost of the purchases dated in the window that have no due item at all, which the
    product cannot say were paid and counts at their document date, `count` of them."""
    share = Purchase.cost_amount * DueItemSettlement.amount / DueItem.amount
    paid = session.execute(
        select(func.coalesce(func.sum(share), 0))
        .select_from(Purchase)
        .join(
            DueItem,
            and_(DueItem.source_kind == PURCHASE_SOURCE_KIND, DueItem.source_id == Purchase.id),
        )
        .join(DueItemSettlement, DueItemSettlement.due_item_id == DueItem.id)
        .where(
            *_registered_purchase(legal_entity_id),
            DueItemSettlement.reversed_at.is_(None),
            DueItemSettlement.settled_on >= first,
            DueItemSettlement.settled_on <= last,
        )
    ).scalar_one()
    untracked, count = session.execute(
        select(func.coalesce(func.sum(Purchase.cost_amount), 0), func.count(Purchase.id)).where(
            *_registered_purchase(legal_entity_id),
            Purchase.date >= first,
            Purchase.date <= last,
            ~exists().where(
                DueItem.source_kind == PURCHASE_SOURCE_KIND, DueItem.source_id == Purchase.id
            ),
        )
    ).one()
    return round_money(Decimal(paid)), round_money(Decimal(untracked)), int(count)


def _payroll_paid(session: Session, legal_entity_id: UUID, first: date, last: date) -> Decimal:
    """What the payroll items (net pay, withholding, contributions, INAIL) settled in the window
    add up to: what the staff cost the legal entity in cash. Every payroll item is an outgoing
    one, and a reversed payment is not a payment."""
    return round_money(
        Decimal(
            session.execute(
                select(func.coalesce(func.sum(DueItemSettlement.amount), 0))
                .select_from(DueItem)
                .join(DueItemSettlement, DueItemSettlement.due_item_id == DueItem.id)
                .where(
                    DueItem.legal_entity_id == legal_entity_id,
                    DueItem.source_kind == PAYROLL_SOURCE_KIND,
                    DueItem.direction == "out",
                    DueItemSettlement.reversed_at.is_(None),
                    DueItemSettlement.settled_on >= first,
                    DueItemSettlement.settled_on <= last,
                )
            ).scalar_one()
        )
    )


# --- accrual ------------------------------------------------------------------------------


def _accrual(
    session: Session,
    legal_entity: LegalEntity,
    year: int,
    pack: FiscalPack,
    rule: IncomeBasisLaw,
    needs_costs: bool,
) -> YearBasis:
    first, last = financial_year(legal_entity, year)
    left_out: list[BasisLine] = []

    # The competence of an invoice is the period it declares, else the day it was issued (the
    # one reading the period P&L and the cash view share, `analytics/filters.py::accrual_date`).
    competence = func.coalesce(Invoice.accrual_from, Invoice.issue_date)
    in_window = (competence >= first, competence <= last)
    revenue = _sum_taxable(session, legal_entity.id, *in_window)
    markers = _untaxed_markers(pack, year)
    surcharge = ZERO_MONEY
    if markers:
        surcharge = round_money(
            Decimal(
                session.execute(
                    select(func.coalesce(func.sum(InvoiceLine.total_price), 0))
                    .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
                    .where(
                        *_revenue_invoices(legal_entity.id),
                        *in_window,
                        charge_line_clause(markers),
                    )
                ).scalar_one()
            )
        )
    if surcharge != 0:
        left_out.append(_surcharge_line(surcharge))

    costs: BasisCosts | None = None
    if needs_costs:
        purchases = round_money(
            Decimal(
                session.execute(
                    select(func.coalesce(func.sum(Purchase.cost_amount), 0)).where(
                        *_registered_purchase(legal_entity.id),
                        Purchase.date >= first,
                        Purchase.date <= last,
                    )
                ).scalar_one()
            )
        )
        personnel = PayrollService(session).personnel_cost_by_kind(legal_entity.id, first, last)
        costs = BasisCosts(
            purchases=purchases,
            staff=personnel.employee,
            director=personnel.director,
            total=round_money(purchases + personnel.employee + personnel.director),
            employees=[
                BasisEmployee(
                    staff_member_id=row.staff_member_id,
                    name=row.name,
                    contract_type=row.contract_type,  # type: ignore[arg-type]
                    cost=row.cost,
                    days_in_service=row.days_in_service,
                )
                for row in personnel.employees
            ],
        )
        if personnel.not_estimated_members:
            left_out.append(
                BasisLine(
                    code="staff_cost_not_estimated",
                    count=personnel.not_estimated_members,
                    reason=(
                        "Persone in servizio senza i dati di paga necessari a stimare il "
                        "costo: non sono nel totale."
                    ),
                )
            )
        left_out += _shared_purchases(session, first, last)

    calendar = (first, last) == (date(year, 1, 1), date(year, 12, 31))
    return YearBasis(
        year=year,
        legal_entity_id=legal_entity.id,
        basis="accrual",
        period_from=first,
        period_to=last,
        label=(
            f"Ricavi e costi di competenza del {year}"
            if calendar
            else (
                f"Ricavi e costi di competenza dell'esercizio dal {first:%d/%m/%Y} "
                f"al {last:%d/%m/%Y}"
            )
        ),
        wording=rule.wording,
        source=rule.source,
        revenue=round_money(revenue - surcharge),
        costs=costs,
        left_out=left_out,
        assumptions=[],
    )


def financial_year(legal_entity: LegalEntity, year: int) -> tuple[date, date]:
    """The financial year of `legal_entity` that closes in `year`, both days included: from the
    day after the previous year end to this year's, by the legal entity's own year end. A
    29 February year end falls on the 28th in a year that has no 29th."""
    return (
        _year_end(legal_entity, year - 1) + timedelta(days=1),
        _year_end(legal_entity, year),
    )


def _year_end(legal_entity: LegalEntity, year: int) -> date:
    month, day = legal_entity.fiscal_year_end_month, legal_entity.fiscal_year_end_day
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, month, day - 1)


# --- what both readings share --------------------------------------------------------------


def _revenue_invoices(legal_entity_id: UUID) -> tuple[ColumnElement[bool], ...]:
    """The one definition of «an invoice that is revenue», of one legal entity (duplicated from
    `analytics/filters.py::revenue_filter` for the reason `ceiling.py` gives: `analytics`
    imports `fiscal`)."""
    return (
        Invoice.type == "invoice",
        Invoice.status == "issued",
        Invoice.deleted_at.is_(None),
        Invoice.legal_entity_id == legal_entity_id,
    )


def _sum_taxable(session: Session, legal_entity_id: UUID, *where: ColumnElement[bool]) -> Decimal:
    return round_money(
        Decimal(
            session.execute(
                select(func.coalesce(func.sum(Invoice.taxable_amount), 0)).where(
                    *_revenue_invoices(legal_entity_id), *where
                )
            ).scalar_one()
        )
    )


def _registered_purchase(legal_entity_id: UUID) -> tuple[ColumnElement[bool], ...]:
    """A purchase that counts: registered, not archived, of the legal entity."""
    return (
        Purchase.deleted_at.is_(None),
        Purchase.status == "registered",
        Purchase.legal_entity_id == legal_entity_id,
    )


def _shared_purchases(session: Session, first: date, last: date) -> list[BasisLine]:
    """The purchases of the window that belong to no legal entity (a shared receipt, REB-623):
    they are in no legal entity's costs, only in the view over every one, so a figure says so."""
    amount, count = session.execute(
        select(func.coalesce(func.sum(Purchase.cost_amount), 0), func.count(Purchase.id)).where(
            Purchase.deleted_at.is_(None),
            Purchase.status == "registered",
            Purchase.legal_entity_id.is_(None),
            Purchase.date >= first,
            Purchase.date <= last,
        )
    ).one()
    if not count:
        return []
    return [
        BasisLine(
            code="shared_purchases",
            amount=round_money(Decimal(amount)),
            count=int(count),
            reason=(
                "Acquisti non attribuiti ad alcuna azienda: contano solo nella vista «tutte», "
                "non nei costi di questa azienda."
            ),
        )
    ]


def _untaxed_markers(pack: FiscalPack, year: int) -> tuple[str, ...]:
    """The description of every charge line the pack tags, in `year`, as outside the coefficient
    base."""
    return tuple(
        charge.description_line
        for charge in pack.charges_for(year)
        if not charge.count_by_the_coefficient
    )


def _surcharge_line(amount: Decimal) -> BasisLine:
    return BasisLine(
        code="inps_surcharge",
        amount=amount,
        reason=(
            "La rivalsa INPS in fattura è fuori dalla base del coefficiente; che debba restarne "
            "fuori è da verificare con il commercialista."
        ),
    )


__all__ = [
    "BasisCosts",
    "BasisLine",
    "YearBasis",
    "financial_year",
    "year_basis",
]
