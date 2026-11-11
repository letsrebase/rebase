"""Tax credits as negative due items (REB-1076, taxes design § 2).

A credit the legal entity holds against the tax office is a due item with a **negative** amount,
source kind `tax_credit`, direction out, the tax office the counterparty with the tax named. It
points at a `TaxCredit` row that carries its origin, so an item says where it came from:

* a **withholding suffered on sales**: what customers kept on the invoices the legal entity
  collected in a tax year, credited against that year's income tax (`irpef`). Only a pack whose
  law gives the credit year (`PaymentRulesLaw.withholding_credit`, the ordinary regime of a person)
  has it: a company's rule is not in the pack and stays to verify (A5, REB-975), and the
  flat-rate regime suffers no withholding. The credit year is the law's
  (`WithholdingCreditLaw.credit_years`): a professional's income is taxed by the cash principle, so
  the income's year is the year the invoice is collected, which is also the year the customer
  withholds, and the credit goes to that year. It falls on the balance date of that tax year. A
  declared figure for the year's income tax already nets the withholdings (`tax_items`: a declared
  tax is the net tax the return shows), so once one is typed the credit item is no longer written,
  and one already written and not touched is dropped, or it would count the same money twice in the
  schedule;
* an **IVA credit carried forward**: the credit a settled IVA period leaves to the next one
  (`IvaSettlementService`). Only the latest settled period's is outstanding, since the next
  settlement takes the earlier credit in and carries the whole of it on. It falls on the date the
  period's own payment would have fallen on.

The refresh is idempotent. An item a payment touched or that is a line of an F24 group is frozen:
it owes what it owes and is never changed or dropped here. Flushes, never commits: the callers
(`TaxDueItemService.refresh_tax_due_items`, `IvaSettlementService`) own the transaction.
"""

import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.due_items.models import TAX_CREDIT_SOURCE_KIND, DueItem, DueItemSettlement
from pigrocrm.core.due_items.sales import join_sales_item
from pigrocrm.core.due_items.schemas import DueItemCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.fiscal.advance_schemas import TaxItemsReport
from pigrocrm.core.fiscal.advances import Missing, balance_date
from pigrocrm.core.fiscal.f24_models import TaxCredit
from pigrocrm.core.fiscal.f24_repository import F24GroupRepository
from pigrocrm.core.fiscal.models import IvaSettlement
from pigrocrm.core.fiscal.pack import FiscalPack
from pigrocrm.core.fiscal.repository import DeclaredTaxFigureRepository
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.money import ZERO_MONEY, round_money

IVA = "iva"
INCOME_TAX = "irpef"
WITHHOLDING_ORIGIN = "withholding_suffered"
IVA_ORIGIN = "iva_carry_forward"


class TaxCreditService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.dues = DueItemService(session)
        self.groups = F24GroupRepository(session)

    # ---- an IVA credit carried forward ------------------------------------------------------

    def sync_iva_credit(self, row: IvaSettlement, words: str) -> None:
        """Brings the credit item of one settled IVA period in step with the settlement: a
        credit outcome that no later period has taken in is an item, anything else is none. An
        item that is frozen is left as it is."""
        credit = self.session.scalars(
            select(TaxCredit).where(TaxCredit.iva_settlement_id == row.id)
        ).first()
        outstanding = row.outcome == "credit" and not self._later_settlement(row)
        item = (
            None if credit is None else self.dues.repo.by_source(TAX_CREDIT_SOURCE_KIND, credit.id)
        )
        if item is not None and item.state != "dropped" and self._frozen(item):
            return
        if not outstanding:
            if item is not None and item.state != "dropped":
                self.dues.drop(item.id, f"credito IVA {words}: riportato o ricalcolato")
            return
        amount = round_money(-row.result)
        if credit is None:
            credit = TaxCredit(
                legal_entity_id=row.legal_entity_id,
                origin=IVA_ORIGIN,
                tax=IVA,
                tax_year=row.year,
                iva_settlement_id=row.id,
                amount=amount,
            )
            self.session.add(credit)
            self.session.flush()
        else:
            credit.amount = amount
        self._write_item(credit, item, due=row.payment_due_on, description=f"Credito IVA {words}")

    def _later_settlement(self, row: IvaSettlement) -> bool:
        return (
            self.session.scalar(
                select(IvaSettlement.id)
                .where(
                    IvaSettlement.legal_entity_id == row.legal_entity_id,
                    IvaSettlement.period_start > row.period_end,
                )
                .limit(1)
            )
            is not None
        )

    # ---- a withholding suffered on sales ----------------------------------------------------

    def refresh_withholding_credits(
        self,
        legal_entity: LegalEntity,
        pack: FiscalPack,
        *,
        today: dt.date,
        years: range,
        in_window: Callable[[dt.date], bool],
        report: TaxItemsReport,
    ) -> None:
        """One credit per tax year in `years`: what was withheld on the invoices collected in
        it, on the balance date of that year's income tax. Nothing for a pack with no credit
        law."""
        by_year = self._withheld_by_credit_year(legal_entity.id, pack, years)
        declared = DeclaredTaxFigureRepository(self.session)
        for year in years:
            law = pack.law_for(year)
            rules = pack.payment_rules_for(year)
            if (
                law is None
                or INCOME_TAX not in law.taxes
                or rules is None
                or rules.withholding_credit is None
            ):
                continue
            amount = by_year.get(year, ZERO_MONEY)
            if any(figure.tax == INCOME_TAX for figure in declared.for_year(legal_entity.id, year)):
                amount = ZERO_MONEY
            due = balance_date(
                pack,
                law,
                INCOME_TAX,
                year,
                legal_entity.fiscal_year_end_month,
                legal_entity.fiscal_year_end_day,
            )
            self._sync_withholding_year(
                legal_entity.id,
                year,
                amount,
                None if isinstance(due, Missing) else due,
                in_window=in_window,
                report=report,
            )

    def _withheld_by_credit_year(
        self, legal_entity_id: UUID, pack: FiscalPack, years: range
    ) -> dict[int, Decimal]:
        """What customers withheld, by the tax year it is credited against: each collection's
        share of its invoice's withholding (a settlement over the item, which is the amount the
        customer pays net), placed by the law's credit year."""
        share = Invoice.withholding_amount * DueItemSettlement.amount / DueItem.amount
        rows = self.session.execute(
            join_sales_item(select(DueItemSettlement.settled_on, share))
            .join(DueItemSettlement, DueItemSettlement.due_item_id == DueItem.id)
            .where(
                Invoice.legal_entity_id == legal_entity_id,
                Invoice.type == "invoice",
                Invoice.status == "issued",
                Invoice.deleted_at.is_(None),
                Invoice.withholding_amount > 0,
                DueItemSettlement.reversed_at.is_(None),
                DueItemSettlement.settled_on >= dt.date(years.start, 1, 1),
                DueItemSettlement.settled_on < dt.date(years.stop, 1, 1),
            )
        ).all()
        totals: dict[int, Decimal] = {}
        for settled_on, part in rows:
            rules = pack.payment_rules_for(settled_on.year)
            law = None if rules is None else rules.withholding_credit
            if law is None:
                continue
            # The customer withholds when it pays and the income is taxed when it is collected,
            # so the income's year and the year of the operation are the one `settled_on.year`.
            (year, *_) = law.credit_years(settled_on.year, settled_on, None)
            totals[year] = totals.get(year, ZERO_MONEY) + Decimal(part)
        return {year: round_money(total) for year, total in totals.items()}

    def _sync_withholding_year(
        self,
        legal_entity_id: UUID,
        year: int,
        amount: Decimal,
        due: dt.date | None,
        *,
        in_window: Callable[[dt.date], bool],
        report: TaxItemsReport,
    ) -> None:
        credit = self.session.scalars(
            select(TaxCredit).where(
                TaxCredit.legal_entity_id == legal_entity_id,
                TaxCredit.origin == WITHHOLDING_ORIGIN,
                TaxCredit.tax_year == year,
            )
        ).first()
        item = (
            None if credit is None else self.dues.repo.by_source(TAX_CREDIT_SOURCE_KIND, credit.id)
        )
        live = item is not None and item.state != "dropped"
        if live and item is not None and self._frozen(item):
            report.kept += 1
            return
        if amount <= 0 or due is None:
            if live and item is not None:
                self.dues.drop(item.id, "ritenute subite non piu' da portare in credito")
                report.dropped += 1
            return
        if credit is None:
            if not in_window(due):
                return
            credit = TaxCredit(
                legal_entity_id=legal_entity_id,
                origin=WITHHOLDING_ORIGIN,
                tax=INCOME_TAX,
                tax_year=year,
                amount=amount,
            )
            self.session.add(credit)
            self.session.flush()
        else:
            credit.amount = amount
        written = self._write_item(
            credit, item, due=due, description=f"Ritenute subite sulle fatture incassate nel {year}"
        )
        if written == "kept":
            report.kept += 1
        else:
            report.written += 1

    # ---- the item ---------------------------------------------------------------------------

    def _write_item(
        self, credit: TaxCredit, item: DueItem | None, *, due: dt.date, description: str
    ) -> str:
        """The credit's negative item: created, or restated while nothing touched it. `kept`
        when it is already as it should be."""
        if item is None:
            self.dues.create(
                DueItemCreate(
                    legal_entity_id=credit.legal_entity_id,
                    direction="out",
                    counterparty_type="tax_office",
                    tax=credit.tax,
                    amount=-credit.amount,
                    description=description,
                    state="expected",
                    source_kind=TAX_CREDIT_SOURCE_KIND,
                    source_id=credit.id,
                    own_date=due,
                )
            )
            return "written"
        if item.state != "dropped" and (item.amount, item.own_date) == (-credit.amount, due):
            return "kept"
        self.dues.restate(item.id, amount=-credit.amount, own_date=due)
        return "written"

    def _frozen(self, item: DueItem) -> bool:
        """A payment touched the item, or it is a line of an F24 group: it owes what it owes."""
        return (
            item.state == "settled"
            or self.dues.repo.totals(item.id).settled != 0
            or self.groups.group_of(item.id) is not None
        )


__all__ = ["TaxCreditService"]
