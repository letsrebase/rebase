"""The withholdings a legal entity suffered on its own invoices in a year (REB-1072, taxes design
§ 3 «solo, ordinary»).

A customer that is a withholding agent keeps part of a professional's fee and pays it to the tax
office (`Invoice.withholding_amount`, the pack's `SalesWithholding`). The tax office credits it
against the person's tax, so the estimate takes it off what IRPEF asks (taxes design § 1,
`it-standard` row). It is operated when the customer pays, so each settlement of the invoice's
due item carries its share of the invoice's withholding, in the proportion of what it settled to
what the item asks (the item asks the net receivable, `Invoice.net_receivable`): a payment in full
is the whole withholding and a payment in instalments adds up to it.

The year it is credited against is the pack's rule (`WithholdingCreditLaw`, REB-1067: art. 22 c. 1
lett. c) TUIR): a withholding on income taxed in a year is credited against that year, and the
income of a person is taxed in the year it is collected (art. 54 c. 1 TUIR, the cash basis the pack
states), which is the year of the settlement. A pack with no such rule for the year gives no
figure here, never a guess.

A company's is `company_withholdings_suffered`: same rule, other period of the income. It is by
accrual (art. 109 TUIR), so what counts is the fiscal period of the invoice, whenever it is paid
(REB-1098; A5 «The same year rule for the withholdings an srl suffers»).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.due_items.models import DueItem, DueItemSettlement
from pigrocrm.core.due_items.sales import join_sales_item
from pigrocrm.core.fiscal.ceiling import collected_in_year_filter
from pigrocrm.core.fiscal.law_payment import WithholdingCreditLaw
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.money import ZERO_MONEY, round_money


def company_withholdings_suffered(
    session: Session,
    legal_entity_id: UUID,
    period_from: date,
    period_to: date,
    rule: WithholdingCreditLaw,
) -> Decimal:
    """What the legal entity's customers withheld, whenever they paid, on the invoices whose
    competence falls in the fiscal period `period_from`..`period_to` (both days included), in euro
    and cents: the credit of that period's IRES under `rule`.

    A company's income is by accrual (art. 109 TUIR), so a withholding is credited to the period
    of the invoice's competence, the period the invoice declares (`Invoice.accrual_from`) or else
    the day it was issued (the revenue's own reading, `fiscal/basis.py`), not to the period the
    customer paid in. A payment of an invoice of an earlier period is the credit of that earlier
    period; a payment not yet made has no withholding to credit. The shares are summed before they
    are rounded. The exceptions of art. 22 c. 1 lett. c) TUIR (a withholding operated after the
    income's period, before or after the return) only matter when the return is known to be
    filed, and the credit stays with the income's period as for a person."""
    if rule.income_basis != "accrual":
        raise ValueError("a company's withholdings are credited by the period they accrue in")
    competence = func.coalesce(Invoice.accrual_from, Invoice.issue_date)
    share = Invoice.withholding_amount * DueItemSettlement.amount / DueItem.amount
    amounts = session.scalars(
        join_sales_item(select(share))
        .join(DueItemSettlement, DueItemSettlement.due_item_id == DueItem.id)
        .where(
            Invoice.legal_entity_id == legal_entity_id,
            Invoice.type == "invoice",
            Invoice.status == "issued",
            Invoice.deleted_at.is_(None),
            Invoice.withholding_amount > 0,
            DueItemSettlement.reversed_at.is_(None),
            competence >= period_from,
            competence <= period_to,
        )
    ).all()
    credited = sum((Decimal(amount) for amount in amounts), Decimal(0))
    return round_money(credited) if credited else ZERO_MONEY


def withholdings_suffered(
    session: Session, legal_entity_id: UUID, year: int, rule: WithholdingCreditLaw
) -> Decimal:
    """What the legal entity's customers withheld on the payments it collected in `year` and that
    `rule` credits against `year`'s tax, in euro and cents. The shares are summed before they
    are rounded, so the instalments never drift by a cent."""
    share = Invoice.withholding_amount * DueItemSettlement.amount / DueItem.amount
    rows: list[tuple[date, Decimal]] = [
        (settled_on, Decimal(amount))
        for settled_on, amount in session.execute(
            join_sales_item(select(DueItemSettlement.settled_on, share))
            .join(DueItemSettlement, DueItemSettlement.due_item_id == DueItem.id)
            .where(*collected_in_year_filter(year, legal_entity_id), Invoice.withholding_amount > 0)
        ).all()
    ]
    # Operated on the day of the settlement, on income taxed in the year it was collected.
    credited = sum(
        (
            amount
            for settled_on, amount in rows
            if year in rule.credit_years(settled_on.year, settled_on, None)
        ),
        Decimal(0),
    )
    return round_money(credited) if credited else ZERO_MONEY


__all__ = ["company_withholdings_suffered", "withholdings_suffered"]
