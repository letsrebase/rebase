"""The IVA periods of a legal entity's year and the two registers behind them, read only
(REB-1024, suppliers design § 6).

The registers are views over the documents, never a second copy: the invoices of the sales
register and the purchases of the purchase register are selected by the same conditions the
settlement sums (`iva_settlement.sales_conditions`, `purchase_conditions`), and a period's
totals come from the same functions (`sales_vat`, `purchase_vat`), so what a register adds up
to is the settlement's line to the cent. Nothing here is a statute: a rate, a share and a
limit are stored on a document or the pack's, a date is the pack calendar's.

A period that has been settled shows the stored settlement. One that has not (the one running
now, an ended one the daily pass has not reached) shows its running figures, computed by
`compute_figures` from the previous period's stored result, flagged `settled: false`; when the
previous period is not stored while an earlier one is, the carry-over is unknown and there is no
ladder, never a guess. An administrator's read, as the settlement's: the figures are the fiscal
position.
"""

import datetime as dt
from bisect import bisect_left
from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.iva_settlement import (
    SOURCE_KIND,
    Bounds,
    carry_in,
    compute_figures,
    deductible_vat,
    label,
    payment_date,
    period_bounds,
    purchase_conditions,
    purchase_vat,
    sales_conditions,
    sales_vat,
)
from pigrocrm.core.fiscal.law import IvaSettlementLaw
from pigrocrm.core.fiscal.models import FiscalProfile, IvaSettlement
from pigrocrm.core.fiscal.pack import FiscalPack, resolve_pack
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.fiscal.schemas import (
    IvaEntityRead,
    IvaLadderRead,
    IvaPeriodRead,
    IvaPeriodState,
    IvaPurchaseGroupRead,
    IvaPurchaseRegisterRead,
    IvaPurchaseRowRead,
    IvaPurchaseSummaryLine,
    IvaPurchaseTotals,
    IvaRateGroupRead,
    IvaSalesRegisterRead,
    IvaSalesRowRead,
    IvaSalesSummaryLine,
    IvaSalesTotals,
)
from pigrocrm.core.invoices.models import Invoice, InvoiceLine, InvoiceRegisterGap
from pigrocrm.core.invoices.totals import ComputedLine, build_recap
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.money import ZERO_MONEY, sum_money
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.purchases.models import Purchase, PurchaseVatLine

ENTITY = "iva_register"
LIST_ACTION = "list_iva_periods"
REGISTER_ACTION = "get_iva_register"
_MONTHS_PER_YEAR = 12
_QUARTERS = 4
_ONE_DAY = dt.timedelta(days=1)


class _Cycle:
    """What one legal entity's IVA cycle is in one year: its pack, the year's law and the
    frequency the year is settled in (the stored one when a period of the year is stored, else
    the profile's, as the settlement does)."""

    def __init__(
        self,
        legal_entity_id: UUID,
        pack: FiscalPack,
        law: IvaSettlementLaw,
        frequency: str,
        stored: dict[int, IvaSettlement],
    ) -> None:
        self.legal_entity_id = legal_entity_id
        self.pack = pack
        self.law = law
        self.frequency = frequency
        self.stored = stored

    @property
    def last_number(self) -> int:
        return _QUARTERS if self.frequency == "quarterly" else _MONTHS_PER_YEAR


def iva_entities(session: Session, actor: Actor) -> list[IvaEntityRead]:
    """The active legal entities the caller sees whose pack settles IVA by period: the ones
    that have registers (a flat-rate or `non-it` entity has none)."""
    actor.require_admin(LIST_ACTION)
    profiles = FiscalProfileRepository(session)
    entities: list[IvaEntityRead] = []
    for entity in LegalEntityService(session).list(actor):
        profile = profiles.get(entity.id)
        if profile is None:
            continue
        if resolve_pack(profile.pack_id, profile.pack_version).has_periodic_iva:
            entities.append(
                IvaEntityRead(
                    legal_entity_id=entity.id,
                    name=entity.name,
                    frequency=profile.iva_frequency,  # type: ignore[arg-type]
                )
            )
    return entities


class IvaRegisterService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.due_items = DueItemService(session)

    def list_periods(
        self, year: int, actor: Actor, *, legal_entity_id: UUID | None = None
    ) -> list[IvaPeriodRead]:
        """Every period of `year` of one legal entity, in order, each with its ladder and
        state; empty when the entity's pack settles no IVA by period in that year."""
        actor.require_admin(LIST_ACTION)
        cycle = self._cycle(year, legal_entity_id)
        if cycle is None:
            return []
        return [self._period(cycle, year, number) for number in range(1, cycle.last_number + 1)]

    def read_sales_register(
        self,
        year: int,
        number: int,
        actor: Actor,
        *,
        legal_entity_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> IvaSalesRegisterRead:
        """One period's sales register: its issued and annulled invoices and the numbers the
        register declares it will never carry, by number, `limit` at a time from `offset`, with
        the summary by rate and the totals of the whole period."""
        actor.require_admin(REGISTER_ACTION)
        cycle, bounds = self._required(year, number, legal_entity_id)
        rows = self._sales_rows(cycle.legal_entity_id, year, bounds)
        issued = [row for row in rows if row.status == "issued"]
        summary: dict[tuple[Decimal, str | None, str], list[Decimal]] = defaultdict(
            lambda: [ZERO_MONEY, ZERO_MONEY]
        )
        for row in issued:
            for group in row.groups:
                key = (group.rate, group.nature, row.exigibility or "immediate")
                summary[key][0] += group.taxable_amount
                summary[key][1] += group.vat_amount
        totals = IvaSalesTotals(
            taxable_amount=sum_money(row.taxable_amount or ZERO_MONEY for row in issued),
            vat_amount=sum_money(row.vat_amount or ZERO_MONEY for row in issued),
            vat_due=sales_vat(self.session, cycle.legal_entity_id, bounds),
            total=sum_money(row.total or ZERO_MONEY for row in issued),
        )
        return IvaSalesRegisterRead(
            items=rows[offset : offset + limit],
            total_count=len(rows),
            next_offset=offset + limit if offset + limit < len(rows) else None,
            summary=[
                IvaSalesSummaryLine(
                    rate=rate,
                    nature=nature,
                    exigibility=exigibility,  # type: ignore[arg-type]
                    taxable_amount=amounts[0],
                    vat_amount=amounts[1],
                )
                for (rate, nature, exigibility), amounts in sorted(
                    summary.items(), key=lambda item: (item[0][0], item[0][1] or "", item[0][2])
                )
            ],
            totals=totals,
        )

    def read_purchase_register(
        self,
        year: int,
        number: int,
        actor: Actor,
        *,
        legal_entity_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> IvaPurchaseRegisterRead:
        """One period's purchase register: the registered invoices and credit notes placed in
        it by their `iva_period`, with each group at its deductible share, `limit` at a time
        from `offset`, with the summary by rate and share and the totals of the whole period."""
        actor.require_admin(REGISTER_ACTION)
        cycle, bounds = self._required(year, number, legal_entity_id)
        rows = self._purchase_rows(cycle.legal_entity_id, bounds)
        summary: dict[tuple[Decimal, str | None, Decimal], list[Decimal]] = defaultdict(
            lambda: [ZERO_MONEY, ZERO_MONEY, ZERO_MONEY]
        )
        for row in rows:
            for group in row.groups:
                amounts = summary[(group.rate, group.nature, group.deductible_share)]
                amounts[0] += group.taxable_amount
                amounts[1] += group.vat_amount
                amounts[2] += group.deductible_vat
        reverse_charge_vat, deductible = purchase_vat(self.session, cycle.legal_entity_id, bounds)
        totals = IvaPurchaseTotals(
            taxable_amount=sum_money(row.taxable_amount for row in rows),
            vat_amount=sum_money(row.vat_amount for row in rows),
            deductible_vat=deductible,
            reverse_charge_vat=reverse_charge_vat,
            total=sum_money(row.total for row in rows),
        )
        return IvaPurchaseRegisterRead(
            items=rows[offset : offset + limit],
            total_count=len(rows),
            next_offset=offset + limit if offset + limit < len(rows) else None,
            summary=[
                IvaPurchaseSummaryLine(
                    rate=rate,
                    nature=nature,
                    deductible_share=share,
                    taxable_amount=amounts[0],
                    vat_amount=amounts[1],
                    deductible_vat=amounts[2],
                )
                for (rate, nature, share), amounts in sorted(
                    summary.items(), key=lambda item: (item[0][0], item[0][1] or "", item[0][2])
                )
            ],
            totals=totals,
        )

    # ---- the cycle and its periods ----------------------------------------------------------

    def _cycle(self, year: int, legal_entity_id: UUID | None) -> _Cycle | None:
        """The legal entity's cycle for `year`; `None` when its pack settles no IVA by period
        in that year."""
        legal_entity = LegalEntityService(self.session).require_single(
            legal_entity_id, entity=ENTITY
        )
        profile = FiscalProfileRepository(self.session).get(legal_entity.id)
        if profile is None:
            return None
        pack = resolve_pack(profile.pack_id, profile.pack_version)
        law = pack.law_for(year)
        if law is None or law.iva is None:
            return None
        stored = {
            row.period_number: row
            for row in self.session.scalars(
                select(IvaSettlement).where(
                    IvaSettlement.legal_entity_id == legal_entity.id,
                    IvaSettlement.year == year,
                )
            )
        }
        return _Cycle(
            legal_entity.id,
            pack,
            law.iva,
            self._frequency(profile, stored),
            stored,
        )

    @staticmethod
    def _frequency(profile: FiscalProfile, stored: dict[int, IvaSettlement]) -> str:
        """A year keeps the frequency it was first settled in, as the settlement does."""
        for row in stored.values():
            return row.frequency
        return profile.iva_frequency

    def _required(
        self, year: int, number: int, legal_entity_id: UUID | None
    ) -> tuple[_Cycle, Bounds]:
        cycle = self._cycle(year, legal_entity_id)
        if cycle is None:
            raise ValidationFailed(
                ENTITY,
                "year",
                "l'azienda non liquida l'IVA per periodo in quest'anno: non ha registri",
                expected="un anno in cui l'azienda liquida l'IVA per periodo",
            )
        if not 1 <= number <= cycle.last_number:
            raise ValidationFailed(
                ENTITY,
                "number",
                "il periodo non esiste: l'azienda liquida "
                + ("per trimestre" if cycle.frequency == "quarterly" else "per mese"),
                expected=f"un numero da 1 a {cycle.last_number}",
            )
        return cycle, period_bounds(cycle.frequency, year, number)

    def _period(self, cycle: _Cycle, year: int, number: int) -> IvaPeriodRead:
        bounds = period_bounds(cycle.frequency, year, number)
        today = today_local()
        state: IvaPeriodState
        if bounds.end < today:
            state = "ended"
        elif bounds.start <= today:
            state = "open"
        else:
            state = "future"
        carry_periods = (
            cycle.law.monthly_carry_periods
            if cycle.frequency == "monthly"
            else cycle.law.quarterly_carry_periods
        )
        read = IvaPeriodRead(
            legal_entity_id=cycle.legal_entity_id,
            frequency=cycle.frequency,  # type: ignore[arg-type]
            year=year,
            number=number,
            label=label(cycle.frequency, year, number),
            period_start=bounds.start,
            period_end=bounds.end,
            state=state,
            settled=False,
            ladder=None,
            payment_due_on=None,
            due_item_id=None,
            payment_state=None,
            carry_forward_limit=cycle.law.carry_forward_limit if number in carry_periods else None,
        )
        row = cycle.stored.get(number)
        if row is not None:
            read.settled = True
            read.ladder = IvaLadderRead.model_validate(row, from_attributes=True)
            read.payment_due_on = row.payment_due_on
            item = self.due_items.repo.by_source(SOURCE_KIND, row.id)
            if item is not None and item.state in ("expected", "settled"):
                read.due_item_id = item.id
                read.payment_state = item.state  # type: ignore[assignment]
            return read
        if state == "future":
            return read
        due = payment_date(cycle.pack, cycle.frequency, year, number)
        read.payment_due_on = due.due_date if due is not None else None
        read.ladder = self._running_ladder(cycle, bounds, number)
        return read

    def _running_ladder(self, cycle: _Cycle, bounds: Bounds, number: int) -> IvaLadderRead | None:
        """The period's figures from its documents and the previous period's stored result;
        `None` when that previous period is not stored while an earlier one is."""
        previous = self.session.scalars(
            select(IvaSettlement)
            .where(
                IvaSettlement.legal_entity_id == cycle.legal_entity_id,
                IvaSettlement.period_end < bounds.start,
            )
            .order_by(IvaSettlement.period_end.desc())
            .limit(1)
        ).first()
        if previous is not None and previous.period_end + _ONE_DAY != bounds.start:
            return None
        sales = sales_vat(self.session, cycle.legal_entity_id, bounds)
        reverse_charge, deductible = purchase_vat(self.session, cycle.legal_entity_id, bounds)
        credit_in, debit_in = carry_in(previous)
        figures = compute_figures(
            cycle.law,
            frequency=cycle.frequency,
            number=number,
            sales_vat=sales,
            reverse_charge_vat=reverse_charge,
            purchase_vat=deductible,
            credit_in=credit_in,
            debit_in=debit_in,
        )
        return IvaLadderRead(
            sales_vat=sales,
            reverse_charge_vat=reverse_charge,
            purchase_vat=deductible,
            credit_in=credit_in,
            debit_in=debit_in,
            surcharge=figures.surcharge,
            result=figures.result,
            outcome=figures.outcome,  # type: ignore[arg-type]
        )

    # ---- the rows ---------------------------------------------------------------------------

    def _sales_rows(
        self, legal_entity_id: UUID, year: int, bounds: Bounds
    ) -> list[IvaSalesRowRead]:
        found = self.session.execute(
            select(Invoice, Organization.legal_name)
            .join(Organization, Organization.id == Invoice.customer_id)
            .where(
                *sales_conditions(legal_entity_id, bounds),
                Invoice.status.in_(("issued", "cancelled")),
            )
        ).all()
        lines: dict[UUID, list[ComputedLine]] = defaultdict(list)
        if found:
            for line in self.session.scalars(
                select(InvoiceLine)
                .where(InvoiceLine.invoice_id.in_([invoice.id for invoice, _ in found]))
                .order_by(InvoiceLine.invoice_id, InvoiceLine.line_number)
            ):
                lines[line.invoice_id].append(
                    ComputedLine(
                        line_number=line.line_number,
                        description=line.description,
                        quantity=line.quantity,
                        unit_of_measure=line.unit_of_measure,
                        unit_price=line.unit_price,
                        discount_percentage=line.discount_percentage,
                        discount_amount=line.discount_amount,
                        total_price=line.total_price,
                        vat_rate=line.vat_rate,
                        vat_nature=line.vat_nature,
                        legal_reference=line.legal_reference,
                    )
                )
        rows = [
            IvaSalesRowRead(
                kind="invoice",
                invoice_id=invoice.id,
                year=invoice.year or year,
                number=invoice.number or 0,
                issue_date=invoice.issue_date,
                customer_id=invoice.customer_id,
                customer_name=customer_name,
                status=invoice.status,  # type: ignore[arg-type]
                document_type=invoice.sdi_document_type,
                exigibility="split_payment" if invoice.split_payment else "immediate",
                groups=[
                    IvaRateGroupRead(
                        rate=group.vat_rate,
                        nature=group.vat_nature,
                        taxable_amount=group.taxable_amount,
                        vat_amount=group.vat_amount,
                    )
                    for group in build_recap(lines[invoice.id])
                ],
                taxable_amount=invoice.taxable_amount,
                vat_amount=invoice.vat_amount,
                total=invoice.total,
                gap_reason=None,
            )
            for invoice, customer_name in found
        ]
        rows.extend(self._gap_rows(legal_entity_id, year, bounds))
        rows.sort(key=lambda row: (row.year, row.number))
        return rows

    def _gap_rows(self, legal_entity_id: UUID, year: int, bounds: Bounds) -> list[IvaSalesRowRead]:
        """The numbers the register of `year` declares it will never carry, placed in the period
        of the invoice just before them (the one just after when none is): a gap has no date of
        its own."""
        gaps = list(
            self.session.scalars(
                select(InvoiceRegisterGap)
                .where(
                    InvoiceRegisterGap.legal_entity_id == legal_entity_id,
                    InvoiceRegisterGap.year == year,
                )
                .order_by(InvoiceRegisterGap.number)
            )
        )
        if not gaps:
            return []
        numbered = [
            (number, issued_on)
            for number, issued_on in self.session.execute(
                select(Invoice.number, Invoice.issue_date)
                .where(
                    Invoice.legal_entity_id == legal_entity_id,
                    Invoice.type == "invoice",
                    Invoice.year == year,
                    Invoice.number.is_not(None),
                    Invoice.issue_date.is_not(None),
                    Invoice.deleted_at.is_(None),
                )
                .order_by(Invoice.number)
            )
            if number is not None and issued_on is not None
        ]
        if not numbered:
            return []
        numbers = [number for number, _ in numbered]
        rows: list[IvaSalesRowRead] = []
        for gap in gaps:
            at = bisect_left(numbers, gap.number)
            placed_on = numbered[at - 1][1] if at > 0 else numbered[0][1]
            if bounds.start <= placed_on <= bounds.end:
                rows.append(
                    IvaSalesRowRead(
                        kind="gap",
                        invoice_id=None,
                        year=gap.year,
                        number=gap.number,
                        issue_date=None,
                        customer_id=None,
                        customer_name=None,
                        status=None,
                        document_type=None,
                        exigibility=None,
                        groups=[],
                        taxable_amount=None,
                        vat_amount=None,
                        total=None,
                        gap_reason=gap.reason,
                    )
                )
        return rows

    def _purchase_rows(self, legal_entity_id: UUID, bounds: Bounds) -> list[IvaPurchaseRowRead]:
        found = self.session.execute(
            select(Purchase, Organization.legal_name)
            .outerjoin(Organization, Organization.id == Purchase.supplier_id)
            .where(*purchase_conditions(legal_entity_id, bounds))
            .order_by(Purchase.iva_period, Purchase.date, Purchase.created_at, Purchase.id)
        ).all()
        lines: dict[UUID, list[PurchaseVatLine]] = defaultdict(list)
        if found:
            for line in self.session.scalars(
                select(PurchaseVatLine)
                .where(PurchaseVatLine.purchase_id.in_([purchase.id for purchase, _ in found]))
                .order_by(PurchaseVatLine.purchase_id, PurchaseVatLine.position)
            ):
                lines[line.purchase_id].append(line)
        rows: list[IvaPurchaseRowRead] = []
        for purchase, supplier_name in found:
            groups = [
                IvaPurchaseGroupRead(
                    rate=line.rate,
                    nature=line.nature,
                    taxable_amount=line.taxable_amount,
                    vat_amount=line.vat_amount,
                    deductible_share=line.deductible_share,
                    deductible_vat=deductible_vat(line.vat_amount, line.deductible_share),
                )
                for line in lines[purchase.id]
            ]
            assert purchase.iva_period is not None  # noqa: S101 - the register's own condition
            rows.append(
                IvaPurchaseRowRead(
                    purchase_id=purchase.id,
                    registered_on=purchase.iva_period,
                    document_date=purchase.date,
                    number=purchase.number,
                    kind=purchase.kind,  # type: ignore[arg-type]
                    supplier_id=purchase.supplier_id,
                    supplier_name=supplier_name or purchase.merchant_name,
                    reverse_charge=purchase.reverse_charge,
                    split_payment=purchase.split_payment,
                    groups=groups,
                    taxable_amount=sum_money(group.taxable_amount for group in groups),
                    vat_amount=sum_money(group.vat_amount for group in groups),
                    deductible_vat=sum_money(group.deductible_vat for group in groups),
                    total=purchase.amount,
                )
            )
        return rows


__all__ = ["IvaRegisterService", "iva_entities"]
