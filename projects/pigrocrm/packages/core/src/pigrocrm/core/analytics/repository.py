"""Every aggregate query of this slice, in one file.

Deliberately one module rather than a query beside each caller: "where does `revenue`
come from" must have exactly one answer to read. The previous system's defect was a *dispersed*
aggregation -- `hoursByOfferKey.get(offer.id) || hoursByOfferKey.get(offer.fileName) ||
hoursByOfferKey.get(offer.offerName) || 0` took the **first non-empty bucket instead of
their sum**, so hours logged against an offer's name vanished if a single hour had been
logged against its id, and `linkedExpenseMap` lost costs the same way, silently. With one
required foreign key there are no buckets to merge and the sum is a `GROUP BY deal_id`;
this file is the structural counterpart of that.
"""

from collections import defaultdict
from collections.abc import Collection
from datetime import date
from decimal import Decimal
from typing import Any, NamedTuple
from uuid import UUID

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from pigrocrm.core.analytics.filters import (
    cash_date,
    day_after,
    in_year,
    invoice_legal_entity,
    revenue_date,
    revenue_filter,
)
from pigrocrm.core.analytics.schemas import CashBase, RevenueBase
from pigrocrm.core.contracts.models import RATE_CARD_DAY_HOUR_TYPES, Contract, RateCard
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.due_items.models import DueItem, DueItemSettlement
from pigrocrm.core.due_items.sales import join_sales_item, remaining_amount, still_owed
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.money import ZERO_MONEY, line_value, round_money, share, sum_hours, sum_money
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.timetracking.models import Cost, TimeEntry
from pigrocrm.core.work_units.models import WORK_UNIT_COMMITTED_STATUSES, WorkUnit


class RevenueByCustomerRow(NamedTuple):
    customer_id: UUID
    legal_name: str
    revenue: Decimal
    invoices: int
    # This row's own share of the *year's whole* revenue, in [0, 1] -- never of the
    # largest row here, which is what `InvoiceRepository._share` scales against for a
    # different question ("who owes the most"). A concentration figure caps a share of
    # the total, so the denominator is `annual_revenue(year)` itself.
    share: float
    # The legal entity whose `annual_revenue` is the denominator (REB-630): with one legal entity
    # it is the same id on every row, in «tutte» it is what groups them.
    legal_entity_id: UUID


class AnalyticsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def deal_revenue(self, deal_id: UUID) -> tuple[Decimal, int]:
        """`(Σ imponibile, count)` for one deal.

        `taxable_amount`, never `total`: the total includes VAT, which is not revenue but
        money collected on the State's behalf. Under the flat-rate regime the two coincide,
        which is exactly why the choice is made here and tested against a synthetic
        `RF01` profile rather than left to be noticed.

        The count is returned alongside because a revenue of `0.00` with three invoices
        behind it and one with none are different situations, and the UI says which.
        """
        total, count = self.session.execute(
            select(func.coalesce(func.sum(Invoice.taxable_amount), 0), func.count()).where(
                Invoice.deal_id == deal_id, *revenue_filter()
            )
        ).one()
        # `round_money`, not a bare `Decimal(...)`: `coalesce(sum(...), 0)` returns the
        # *integer literal* when a deal has no invoices at all, and `Decimal("0")`
        # reaches the wire as `"0"` while every other money field reaches it as
        # `"0.00"`. Numerically identical, and identical in every core test, since
        # `Decimal("0") == Decimal("0.00")` -- but the browser prints what it is given,
        # and money that sometimes has two decimal places and sometimes none is the
        # visible half of a defect nobody sees in a unit test. The same reason applies
        # to each of the three aggregates below.
        return round_money(Decimal(total)), int(count)

    def deal_direct_costs(self, deal_id: UUID) -> Decimal:
        return round_money(
            Decimal(
                self.session.execute(
                    select(func.coalesce(func.sum(Cost.amount), 0)).where(
                        Cost.deal_id == deal_id, Cost.deleted_at.is_(None)
                    )
                ).scalar_one()
            )
        )

    # Both scopes are generic over the statement's columns (`Select[*Ts]`, SQLAlchemy
    # 2.1's own spelling), so a scoped statement keeps the row type its `select()` gave
    # it and the caller's `session.execute(...)` stays typed. A bare `Select[Any]` would
    # work at runtime and, since 2.1, means one column of `Any` rather than any shape.

    def _customer_scope[*Ts](
        self, stmt: Select[*Ts], customer_id: UUID | None, column: InstrumentedAttribute[Any]
    ) -> Select[*Ts]:
        if customer_id is None:
            return stmt
        return stmt.where(column.in_(select(Deal.id).where(Deal.customer_id == customer_id)))

    def _legal_entity_scope[*Ts](
        self, stmt: Select[*Ts], legal_entity_id: UUID | None, column: InstrumentedAttribute[Any]
    ) -> Select[*Ts]:
        """A deal-keyed table narrowed to one legal entity through its deal (REB-630): an
        hour's legal entity is the deal's, so the predicate is the same subquery
        `TimeEntryRepository.list` uses rather than a column that table would copy. A
        cost has its own `legal_entity_id` (equal to its deal's by `CostService`, indexed)
        and every cost read in this file uses that column instead."""
        if legal_entity_id is None:
            return stmt
        return stmt.where(
            column.in_(select(Deal.id).where(Deal.legal_entity_id == legal_entity_id))
        )

    def revenue_in_range(
        self,
        date_from: date,
        date_to: date,
        customer_id: UUID | None,
        base: RevenueBase = "issue",
        legal_entity_id: UUID | None = None,
        deal_ids: Collection[UUID] | None = None,
    ) -> dict[UUID, Decimal]:
        """Revenue is attributed to the period by **its own** date -- `issue_date` --
        not by the deal's date, which does not exist, and not by one common date, which
        none of the three quantities has (§7.4). `base="accrual"` is the second
        reading (ORB-61): the accrual period the document declares, falling back to the
        emission date for a document that declared none. Same filter, same figure,
        different month."""
        when = revenue_date(base)
        stmt = (
            select(Invoice.deal_id, func.coalesce(func.sum(Invoice.taxable_amount), 0))
            .where(
                Invoice.deal_id.isnot(None), when >= date_from, when <= date_to, *revenue_filter()
            )
            .group_by(Invoice.deal_id)
        )
        if deal_ids is not None:
            stmt = stmt.where(Invoice.deal_id.in_(deal_ids))
        stmt = self._customer_scope(stmt, customer_id, Invoice.deal_id)
        # Through the deal, not the invoice's own column: the period P&L is a report
        # per deal, and `deals_in_range`, the costs and the hours all read the deal's
        # legal entity, so a deal and its revenue can never land on two sides of the line
        # (an import may put an invoice on another legal entity than its deal's).
        stmt = self._legal_entity_scope(stmt, legal_entity_id, Invoice.deal_id)
        # `deal_id is not None` restates the `WHERE` for the type checker: the column is
        # nullable on the model and the statement already keeps only the rows that have one.
        return {
            deal_id: Decimal(total)
            for deal_id, total in self.session.execute(stmt).all()
            if deal_id is not None
        }

    def costs_in_range(
        self,
        date_from: date,
        date_to: date,
        customer_id: UUID | None,
        legal_entity_id: UUID | None = None,
    ) -> tuple[dict[UUID, Decimal], Decimal]:
        """`({deal_id: total}, general_expenses)`. Costs are attributed by `costs.date`.

        General expenses -- `deal_id IS NULL` -- come back separately and are never
        distributed: any apportionment key would make a deal's margin move when a
        different deal was invoiced (§7.4). A customer filter excludes them entirely,
        because a general expense belongs to no customer by definition. A legal entity
        filter keeps the ones written for that legal entity and drops the shared ones
        (`azienda_id IS NULL`), which appear only in «tutte» (REB-630, spec §1.7).
        """
        per_deal: dict[UUID, Decimal] = {}
        stmt = (
            select(Cost.deal_id, func.coalesce(func.sum(Cost.amount), 0))
            .where(
                Cost.deal_id.isnot(None),
                Cost.date >= date_from,
                Cost.date <= date_to,
                Cost.deleted_at.is_(None),
            )
            .group_by(Cost.deal_id)
        )
        stmt = self._customer_scope(stmt, customer_id, Cost.deal_id)
        if legal_entity_id is not None:
            stmt = stmt.where(Cost.legal_entity_id == legal_entity_id)
        for deal_id, total in self.session.execute(stmt).all():
            if deal_id is not None:  # restates the `WHERE` for the type checker, as above
                per_deal[deal_id] = Decimal(total)

        if customer_id is not None:
            return per_deal, ZERO_MONEY
        general_costs = select(func.coalesce(func.sum(Cost.amount), 0)).where(
            Cost.deal_id.is_(None),
            Cost.date >= date_from,
            Cost.date <= date_to,
            Cost.deleted_at.is_(None),
        )
        if legal_entity_id is not None:
            general_costs = general_costs.where(Cost.legal_entity_id == legal_entity_id)
        general = round_money(Decimal(self.session.execute(general_costs).scalar_one()))
        return per_deal, general

    def labour_cost_in_range(
        self,
        date_from: date,
        date_to: date,
        customer_id: UUID | None,
        legal_entity_id: UUID | None = None,
    ) -> dict[UUID, Decimal]:
        """`Σ ROUND(ore × costo_applicato, 2)`, summed **per row** and then added -- never
        `ROUND(Σ ore × costo, 2)`.

        Computed in Python rather than in SQL on purpose: Postgres would round the
        product with its own rule, and §6.2 fixes `ROUND_HALF_UP` per row in
        `money.py` as the single authority. Two rounding implementations is how the
        printed column and the total come to disagree.
        """
        stmt = select(TimeEntry.deal_id, TimeEntry.hours, TimeEntry.applied_cost).where(
            TimeEntry.date >= date_from,
            TimeEntry.date <= date_to,
            TimeEntry.deleted_at.is_(None),
            TimeEntry.applied_cost.isnot(None),
        )
        stmt = self._customer_scope(stmt, customer_id, TimeEntry.deal_id)
        stmt = self._legal_entity_scope(stmt, legal_entity_id, TimeEntry.deal_id)
        grouped: dict[UUID, list[Decimal | None]] = defaultdict(list)
        for deal_id, hours, cost in self.session.execute(stmt).all():
            grouped[deal_id].append(line_value(hours, cost))
        return {deal_id: sum_money(values) for deal_id, values in grouped.items()}

    def hours_in_range(
        self,
        date_from: date,
        date_to: date,
        customer_id: UUID | None,
        legal_entity_id: UUID | None = None,
        deal_ids: Collection[UUID] | None = None,
    ) -> dict[UUID, Decimal]:
        stmt = (
            select(TimeEntry.deal_id, func.coalesce(func.sum(TimeEntry.hours), 0))
            .where(
                TimeEntry.date >= date_from,
                TimeEntry.date <= date_to,
                TimeEntry.deleted_at.is_(None),
            )
            .group_by(TimeEntry.deal_id)
        )
        if deal_ids is not None:
            stmt = stmt.where(TimeEntry.deal_id.in_(deal_ids))
        stmt = self._customer_scope(stmt, customer_id, TimeEntry.deal_id)
        stmt = self._legal_entity_scope(stmt, legal_entity_id, TimeEntry.deal_id)
        return {row[0]: Decimal(row[1]) for row in self.session.execute(stmt).all()}

    def _committed_work_units_backlog(
        self,
        date_from: date | None,
        date_to: date | None,
        customer_id: UUID | None,
        legal_entity_id: UUID | None = None,
    ) -> tuple[Decimal, int, int]:
        """`(valore, voci, voci_senza_tariffa)` -- `unbilled_backlog`'s REB-372
        contribution from `work_units`: every day in an approved-or-later state
        (`WORK_UNIT_COMMITTED_STATUSES`) with no invoice line yet, priced against
        whichever of its own contract's rate cards covers its date.

        Contributes to `valore`/`entries` only, never to `billable_uninvoiced_hours`:
        `WorkUnit.quantity` is priced in whatever unit its rate card names (a day, a
        month, a lump sum -- `RATE_CARD_UNIT`, not always an hour), so folding it into
        an hours total would silently misreport a day as an hour. A day whose date has
        no rate card in force prices at nothing and is counted in `entries_without_rate`
        instead -- the same "a missing rate is not a rate of zero" refusal `line_value`
        gives `TimeEntry`, not the harder `ValidationFailed` `unbilled_work_unit_lines`
        gives an actual invoicing attempt: a read-only aggregate must survive a coverage
        gap, not raise because of one.
        """
        stmt = select(WorkUnit.contract_id, WorkUnit.date, WorkUnit.quantity).where(
            WorkUnit.status.in_(WORK_UNIT_COMMITTED_STATUSES),
            WorkUnit.invoice_line_id.is_(None),
        )
        if date_from is not None:
            stmt = stmt.where(WorkUnit.date >= date_from)
        if date_to is not None:
            stmt = stmt.where(WorkUnit.date <= date_to)
        if customer_id is not None or legal_entity_id is not None:
            # A day's legal entity is its contract's (REB-630), as its customer is.
            stmt = stmt.join(Contract, Contract.id == WorkUnit.contract_id)
        if customer_id is not None:
            stmt = stmt.where(Contract.customer_id == customer_id)
        if legal_entity_id is not None:
            stmt = stmt.where(Contract.legal_entity_id == legal_entity_id)
        rows = self.session.execute(stmt).all()
        if not rows:
            return ZERO_MONEY, 0, 0

        contract_ids = {contract_id for contract_id, _, _ in rows}
        cards_by_contract: dict[UUID, list[RateCard]] = defaultdict(list)
        for rate_card in self.session.execute(
            select(RateCard).where(RateCard.contract_id.in_(contract_ids))
        ).scalars():
            cards_by_contract[rate_card.contract_id].append(rate_card)

        values: list[Decimal | None] = []
        for contract_id, day, quantity in rows:
            card = next(
                (
                    c
                    for c in cards_by_contract.get(contract_id, [])
                    if c.type in RATE_CARD_DAY_HOUR_TYPES
                    and c.valid_from <= day
                    and (c.valid_to is None or day <= c.valid_to)
                ),
                None,
            )
            values.append(round_money(Decimal(quantity) * card.amount) if card else None)
        return (
            sum_money(values),
            len(rows),
            sum(1 for value in values if value is None),
        )

    def unbilled_backlog(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        customer_id: UUID | None = None,
        legal_entity_id: UUID | None = None,
        *,
        hours: bool = True,
        contract_days: bool = True,
    ) -> tuple[Decimal, Decimal, int, int]:
        """`(ore, valore, voci_senza_tariffa, voci)` over billable work not yet invoiced.

        With no window it is the whole arrears, over every period there has ever been:
        "quanto ho da fatturare" is not a question about March (slice 6 §6.3). With one it
        is the same quantity restricted to the period, which is what `PeriodPnl`'s three
        slice-6 fields carry -- one aggregate over `time_entries`, called twice with
        different bounds, rather than two definitions of the same phrase.

        **"Not yet invoiced" is a fact about the invoice's state, not about the link.** An
        hour attached to a line of a *draft* is still counted here, because a draft is not
        revenue and its lines are rewritten wholesale by slice 3 without orphaning
        anything -- the reading `billed_entry_ids` fixed for slice 4 §4.3, and the one
        `deal_pnl` and `deal_summary` already use. A `NOT EXISTS` rather than that
        function's `IN`, because this query has no bounded list of entries to look up: the
        backlog is defined by the absence of a binding, over a table nobody hands us.

        `Σ ROUND(ore × tariffa_applicata, 2)` per row and then summed, in Python and not
        in SQL, for the reason `labour_cost_in_range` gives above: `money.py` is the single
        authority on `ROUND_HALF_UP`, and Postgres's own `round()` would be a second
        implementation of it. The rows pulled back are only the *unbilled* billable ones,
        which is by nature a small set -- everything that has been invoiced has left it.

        `tariffa_applicata IS NULL` rows are counted in `hours` and in `entries_without_rate`
        and contribute nothing to `valore`: a missing rate is not a rate of zero, and
        `line_value` is what refuses to conflate them.

        The two sources belong to two modules (spec 2026-11-09, A2): `hours` is the
        `time_entries` half, `contract_days` the approved `work_units`; a half that is off is
        left out of every figure, so a space with `time` off still has its contract days.

        **REB-372: `work_units` narrows this figure's own contribution, it does not
        replace it.** `TimeEntry` has no state machine at all (REB-352 §1.2's own
        citation) and every one of its billable, unbilled hours has always counted
        here unconditionally -- that half is unchanged by this method. `work_units`
        is the one source that *does* carry an approval state (REB-358/REB-359), so
        its own contribution is narrowed to `WORK_UNIT_COMMITTED_STATUSES` --
        approved-or-later days only, never a merely `proposed` one and never a
        `worked_without_approval` day the "propose, human confirms" invariant has
        not cleared -- and folded into `valore`/`entries` alongside the unchanged
        `TimeEntry` figures (`_committed_work_units_backlog`).
        """
        billed = (
            select(InvoiceLine.id)
            .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .where(InvoiceLine.id == TimeEntry.invoice_line_id, *revenue_filter())
        )
        # An entry whose line this session cannot see at all is not backlog either: an
        # import may bind a deal's hours to an invoice of another legal entity, and under the
        # policies «no issued invoice found» must not read as «not yet invoiced»
        # (REB-634; the freeze guard of `billed_entry_ids` reads it the same way).
        visible_line = select(InvoiceLine.id).where(InvoiceLine.id == TimeEntry.invoice_line_id)
        stmt = select(TimeEntry.hours, TimeEntry.applied_rate).where(
            TimeEntry.deleted_at.is_(None),
            TimeEntry.billable.is_(True),
            or_(
                TimeEntry.invoice_line_id.is_(None),
                and_(visible_line.exists(), ~billed.exists()),
            ),
        )
        if date_from is not None:
            stmt = stmt.where(TimeEntry.date >= date_from)
        if date_to is not None:
            stmt = stmt.where(TimeEntry.date <= date_to)
        stmt = self._customer_scope(stmt, customer_id, TimeEntry.deal_id)
        stmt = self._legal_entity_scope(stmt, legal_entity_id, TimeEntry.deal_id)

        rows = self.session.execute(stmt).all() if hours else []
        wu_value, wu_entries, wu_without_rate = (
            self._committed_work_units_backlog(date_from, date_to, customer_id, legal_entity_id)
            if contract_days
            else (Decimal("0.00"), 0, 0)
        )
        return (
            sum_hours([hours for hours, _ in rows]),
            sum_money([line_value(hours, rate) for hours, rate in rows]) + wu_value,
            sum(1 for _, rate in rows if rate is None) + wu_without_rate,
            len(rows) + wu_entries,
        )

    def late_entry_count(
        self, date_from: date, date_to: date, legal_entity_id: UUID | None = None
    ) -> int:
        """How many rows dated inside the period were written **after** it ended
        (`created_at > a`). It is what tells a reader whether the figure can still move,
        and it is free: a COUNT over two columns that already exist (§6.4). Under an
        legal entity the hours are its deals' and the costs the ones that carry its id
        (REB-630): a cost with a deal copied the deal's, a shared one stays out."""
        hours = (
            select(func.count())
            .select_from(TimeEntry)
            .where(
                TimeEntry.date >= date_from,
                TimeEntry.date <= date_to,
                TimeEntry.deleted_at.is_(None),
                TimeEntry.created_at >= day_after(date_to),
            )
        )
        hours = self._legal_entity_scope(hours, legal_entity_id, TimeEntry.deal_id)
        expenses = (
            select(func.count())
            .select_from(Cost)
            .where(
                Cost.date >= date_from,
                Cost.date <= date_to,
                Cost.deleted_at.is_(None),
                Cost.created_at >= day_after(date_to),
            )
        )
        if legal_entity_id is not None:
            expenses = expenses.where(Cost.legal_entity_id == legal_entity_id)
        entries = self.session.execute(hours).scalar_one()
        costs = self.session.execute(expenses).scalar_one()
        return int(entries) + int(costs)

    def monthly_paid(
        self, year: int, base: CashBase = "accrual", legal_entity_id: UUID | None = None
    ) -> dict[int, Decimal]:
        """What was paid on revenue invoices, per month of `year` -- money in the bank, so
        gross, as `sum_unpaid` reasons.

        Under `collection`, each settlement's own amount by its own date (spec 2026-11-09,
        due items, § 1.1): an invoice paid in instalments is in each month for what arrived
        in it, and one paid across 31 December is in each year for its share. Under `accrual`,
        a paid invoice's total by the accrual period it declares, the invoice being the unit
        there."""
        if base == "collection":
            month = func.extract("month", DueItemSettlement.settled_on)
            rows = self.session.execute(
                join_sales_item(select(month, func.sum(DueItemSettlement.amount)))
                .join(DueItemSettlement, DueItemSettlement.due_item_id == DueItem.id)
                .where(
                    *revenue_filter(),
                    *invoice_legal_entity(legal_entity_id),
                    DueItemSettlement.reversed_at.is_(None),
                    *in_year(DueItemSettlement.settled_on, year),
                )
                .group_by(month)
            ).all()
            return {int(m): round_money(Decimal(total)) for m, total in rows}
        when = cash_date(base, func.coalesce(Invoice.payment_date, Invoice.issue_date))
        month = func.extract("month", when)
        rows = self.session.execute(
            select(month, func.sum(Invoice.total))
            .where(
                *revenue_filter(),
                *invoice_legal_entity(legal_entity_id),
                Invoice.payment_status == "paid",
                *in_year(when, year),
            )
            .group_by(month)
        ).all()
        return {int(m): round_money(Decimal(total)) for m, total in rows}

    def monthly_unpaid(
        self, year: int, base: CashBase = "accrual", legal_entity_id: UUID | None = None
    ) -> dict[int, Decimal]:
        """What is still owed on revenue invoices, per month. Under `collection`, each due
        item's remaining amount by the month it falls due (issue date when no due date was
        set): the month the money is expected, and the part of a partly paid invoice that is
        left. Under `accrual`, an unpaid invoice's total by the accrual period it declares."""
        if base == "collection":
            due = func.coalesce(DueItem.due_date, Invoice.issue_date)
            month = func.extract("month", due)
            rows = self.session.execute(
                join_sales_item(select(month, func.sum(remaining_amount())))
                .where(
                    *revenue_filter(),
                    *invoice_legal_entity(legal_entity_id),
                    still_owed(),
                    *in_year(due, year),
                )
                .group_by(month)
            ).all()
            return {int(m): round_money(Decimal(total)) for m, total in rows}
        when = cash_date(base, func.coalesce(Invoice.due_date, Invoice.issue_date))
        month = func.extract("month", when)
        rows = self.session.execute(
            select(month, func.sum(Invoice.total))
            .where(
                *revenue_filter(),
                *invoice_legal_entity(legal_entity_id),
                Invoice.payment_status == "unpaid",
                *in_year(when, year),
            )
            .group_by(month)
        ).all()
        return {int(m): round_money(Decimal(total)) for m, total in rows}

    def monthly_drafts(
        self, year: int, base: CashBase = "accrual", legal_entity_id: UUID | None = None
    ) -> dict[int, Decimal]:
        """`Σ totale` of what is written but not yet an issued invoice: draft invoices and
        live proformas (not the ones already turned into an invoice, which would count
        twice). By the accrual period the document declares; under `collection`, by the
        document's own date. Either way, the day it was created when it has neither.

        Since ORB-63 a proforma always has a date: `issue_date` is the one the sender
        put on the document, so under `collection` a proforma dated 5 September for August's
        work is September's projected money, exactly as the fattura it becomes will be;
        under `accrual` it is August's, because that is the period it declares. A
        `invoice` draft still has no date until `issue` and stays bucketed by the day it
        was created when it declares no period; `created_at` is the fallback for it alone.
        """
        when = func.coalesce(cash_date(base, Invoice.issue_date), func.date(Invoice.created_at))
        month = func.extract("month", when)
        rows = self.session.execute(
            select(month, func.sum(Invoice.total))
            .where(
                Invoice.deleted_at.is_(None),
                *invoice_legal_entity(legal_entity_id),
                *in_year(when, year),
                (
                    ((Invoice.type == "invoice") & (Invoice.status == "draft"))
                    | (
                        (Invoice.type == "proforma")
                        & Invoice.status.in_(("draft", "issued", "confirmed"))
                    )
                ),
            )
            .group_by(month)
        ).all()
        return {int(m): round_money(Decimal(total)) for m, total in rows}

    def monthly_costs(self, year: int, legal_entity_id: UUID | None = None) -> dict[int, Decimal]:
        """`Σ importo` of costs by the month they were incurred, deal or no deal. Under
        one legal entity, the costs that carry its id: a cost with a deal copied the deal's,
        a shared one (`NULL`) is nobody's and shows only in «tutte» (REB-630, §1.7)."""
        month = func.extract("month", Cost.date)
        stmt = (
            select(month, func.sum(Cost.amount))
            .where(Cost.deleted_at.is_(None), *in_year(Cost.date, year))
            .group_by(month)
        )
        if legal_entity_id is not None:
            stmt = stmt.where(Cost.legal_entity_id == legal_entity_id)
        rows = self.session.execute(stmt).all()
        return {int(m): round_money(Decimal(total)) for m, total in rows}

    def annual_revenue(self, year: int, legal_entity_id: UUID | None = None) -> Decimal:
        """Every issued invoice of the year, deal or no deal: the fiscal estimate is
        about the person's income, so an invoice with no `deal_id` counts too. Of one
        legal entity when named (REB-630): the estimate is one legal entity's, and its caller
        always names one."""
        return round_money(
            Decimal(
                self.session.execute(
                    select(func.coalesce(func.sum(Invoice.taxable_amount), 0)).where(
                        Invoice.year == year,
                        *revenue_filter(),
                        *invoice_legal_entity(legal_entity_id),
                    )
                ).scalar_one()
            )
        )

    def revenue_years(self, legal_entity_id: UUID | None = None) -> list[int]:
        """The years in which one legal entity has issued revenue, oldest first: the years
        whose income estimate there is something to keep for (REB-974)."""
        years = self.session.execute(
            select(Invoice.year)
            .where(*revenue_filter(), *invoice_legal_entity(legal_entity_id))
            .distinct()
            .order_by(Invoice.year)
        ).scalars()
        return [year for year in years if year is not None]

    def annual_revenue_by_legal_entity(
        self, year: int, legal_entity_id: UUID | None = None
    ) -> dict[UUID, Decimal]:
        """`annual_revenue`, grouped by the issuing legal entity: the denominators of
        `revenue_by_customer` in «tutte» (REB-630), one per legal entity, since a share of
        revenue is a share of one legal entity's revenue."""
        rows = self.session.execute(
            select(Invoice.legal_entity_id, func.coalesce(func.sum(Invoice.taxable_amount), 0))
            .where(Invoice.year == year, *revenue_filter(), *invoice_legal_entity(legal_entity_id))
            .group_by(Invoice.legal_entity_id)
        ).all()
        return {legal_entity: round_money(Decimal(total)) for legal_entity, total in rows}

    def revenue_by_customer(
        self, year: int, legal_entity_id: UUID | None = None
    ) -> list[RevenueByCustomerRow]:
        """Each customer's own share of the year's invoiced revenue (§1.5, §5 item 1 of
        `docs/superpowers/specs/2026-09-23-forecasting-and-analytics-from-mastro-design.md`):
        `Σ imponibile` grouped by `customer_id`, over the same `revenue_filter()`
        `annual_revenue` applies, each row's own share of that same annual total --
        ranked, largest first.

        **Not** `InvoiceRepository.receivables_by_customer`'s `share`: that scales a
        customer's outstanding receivable against the *largest* customer's own exposure
        and answers "who currently owes the most". A concentration figure caps a
        customer's share of *total* invoiced income instead, so the denominator here is
        the whole year's `annual_revenue`, never the top row -- the two are easy to
        conflate because both render as a per-customer percentage, and they are not the
        same figure.

        A customer with no invoice this year is simply absent, the same way
        `receivables_by_customer` omits one with nothing outstanding.

        Per legal entity since REB-630 (spec 2026-10-03 §1.9): the denominator is the
        issuing legal entity's own `annual_revenue`, never the space's, because a customer
        who is a third of the SRL and a tenth of the whole has to read as a third. With
        `legal_entity_id` the rows are that legal entity's; without, every legal entity's, grouped
        in
        the order the selector lists them (the default first) and ranked inside each
        group, so a one-legal entity space reads exactly what it read before.
        """
        total = self.annual_revenue_by_legal_entity(year, legal_entity_id)
        revenue = func.coalesce(func.sum(Invoice.taxable_amount), 0)
        stmt = (
            select(
                Invoice.legal_entity_id,
                Organization.id,
                Organization.legal_name,
                revenue,
                func.count(Invoice.id),
            )
            .join(Organization, Organization.id == Invoice.customer_id)
            .join(LegalEntity, LegalEntity.id == Invoice.legal_entity_id)
            .where(Invoice.year == year, *revenue_filter(), *invoice_legal_entity(legal_entity_id))
            .group_by(
                Invoice.legal_entity_id,
                LegalEntity.is_default,
                LegalEntity.name,
                Organization.id,
                Organization.legal_name,
            )
            .order_by(
                LegalEntity.is_default.desc(),
                LegalEntity.name,
                Invoice.legal_entity_id,
                revenue.desc(),
                Organization.legal_name,
            )
        )
        lines: list[RevenueByCustomerRow] = []
        for legal_entity, customer_id, legal_name, amount, number in self.session.execute(
            stmt
        ).all():
            value = round_money(Decimal(amount))
            total_year = total.get(legal_entity, ZERO_MONEY)
            lines.append(
                RevenueByCustomerRow(
                    customer_id=customer_id,
                    legal_name=legal_name,
                    revenue=value,
                    invoices=int(number),
                    share=share(value, total_year),
                    legal_entity_id=legal_entity,
                )
            )
        return lines

    def count_over_concentration_threshold(
        self, year: int, threshold: float, legal_entity_id: UUID | None = None
    ) -> int:
        """How many organizations cross `threshold`, a configured preferred-share threshold
        (`Settings.preferred_concentration_threshold`, §3 and §5 item 2 of
        `docs/superpowers/specs/2026-09-23-forecasting-and-analytics-from-mastro-design.md`):
        the COUNT `OperationalDashboard`'s fourth signal reads (REB-371).

        Strictly above, never at it: a customer sitting exactly on the configured share
        has not yet crossed it. Reuses `revenue_by_customer`'s own rows rather than a
        second query, so the signal and the concentration table its `link`
        points to can never disagree on which customer this counts -- the same
        drill-through discipline `count_deals_invoiced_not_won` and its siblings apply
        with their own filtered lists.

        In «tutte» (REB-630) the count is of distinct organizations over the threshold in
        any legal entity, each share read against its own legal entity's revenue: never a
        space-wide share, which would hide the customer who is a third of one legal entity
        and a tenth of the whole, and never twice for a customer two legal entities both
        depend on, since the signal counts people to talk to.
        """
        return len(
            {
                row.customer_id
                for row in self.revenue_by_customer(year, legal_entity_id)
                if row.share > threshold
            }
        )

    def deals_in_range(
        self,
        date_from: date,
        date_to: date,
        customer_id: UUID | None,
        base: RevenueBase = "issue",
        legal_entity_id: UUID | None = None,
        after: tuple[str, UUID] | None = None,
        limit: int | None = None,
    ) -> list[Deal]:
        """Every deal with any activity in the window -- an issued invoice, a cost or an
        hour. Not "every deal": a period report listing deals with nothing in the period
        is the unbounded growth residual B3 describes, and the window is what bounds it.

        `base` has to be the one `revenue_in_range` was asked with: an invoice that the
        accrual reading puts in August makes its deal active in August, and a deal
        absent from this list is revenue the report silently drops.
        """
        when = revenue_date(base)
        active = (
            select(Invoice.deal_id.label("deal_id"))
            .where(
                Invoice.deal_id.isnot(None), when >= date_from, when <= date_to, *revenue_filter()
            )
            .union(
                select(Cost.deal_id.label("deal_id")).where(
                    Cost.deal_id.isnot(None),
                    Cost.date >= date_from,
                    Cost.date <= date_to,
                    Cost.deleted_at.is_(None),
                ),
                select(TimeEntry.deal_id.label("deal_id")).where(
                    TimeEntry.date >= date_from,
                    TimeEntry.date <= date_to,
                    TimeEntry.deleted_at.is_(None),
                ),
            )
            .subquery()
        )
        stmt = select(Deal).where(Deal.id.in_(select(active.c.deal_id)), Deal.deleted_at.is_(None))
        if after is not None:
            stmt = stmt.where(
                or_(Deal.name > after[0], and_(Deal.name == after[0], Deal.id > after[1]))
            )
        if customer_id is not None:
            stmt = stmt.where(Deal.customer_id == customer_id)
        if legal_entity_id is not None:
            # The deal's own column, not the invoice's: a deal keeps its legal entity when its
            # customer moves (spec §1.7), and this list is of deals.
            stmt = stmt.where(Deal.legal_entity_id == legal_entity_id)
        return list(self.session.execute(stmt.order_by(Deal.name, Deal.id).limit(limit)).scalars())

    def revenue_for_customer_in_window(
        self,
        customer_id: UUID,
        date_from: date,
        date_to: date,
        base: RevenueBase = "issue",
        legal_entity_id: UUID | None = None,
    ) -> list[tuple[str, Decimal, Decimal]]:
        """`(divisa, ricavi del cliente, ricavi totali)` per currency over `[da, a]` --
        the two figures a concentration cap divides (REB-352 §1.5), summed in one
        statement so the two sides of the ratio can never read a different snapshot.
        Same revenue definition `annual_revenue` uses (`revenue_filter`), windowed by
        `da`/`a` rather than by `Invoice.year`: a contract's own anniversary year rarely
        lines up with the calendar one `year` names. Within one legal entity when named
        (REB-630): the cap is the contract's legal entity's, so both sides of the ratio
        read that register alone.

        Grouped by `Invoice.currency` and never converted (REB-776): a sum across
        currencies is not an amount in any of them. Ordered by currency code, so the
        answer does not depend on how Postgres happened to group.
        """
        when = revenue_date(base)
        customer = func.coalesce(
            func.sum(case((Invoice.customer_id == customer_id, Invoice.taxable_amount), else_=0)), 0
        )
        total = func.coalesce(func.sum(Invoice.taxable_amount), 0)
        rows = self.session.execute(
            select(Invoice.currency, customer, total)
            .where(
                when >= date_from,
                when <= date_to,
                *revenue_filter(),
                *invoice_legal_entity(legal_entity_id),
            )
            .group_by(Invoice.currency)
            .order_by(Invoice.currency)
        ).all()
        return [
            (str(row[0]), round_money(Decimal(row[1])), round_money(Decimal(row[2])))
            for row in rows
        ]

    def year_invoices(
        self, year: int, legal_entity_id: UUID
    ) -> list[tuple[Invoice, Organization | None]]:
        """Every invoice of the year's register, in register order: `year`, one legal entity,
        and `revenue_filter`'s definition of an invoice that is revenue, so the list and
        `annual_revenue` can never read a different universe. The customer is an outer
        join: a member scoped to this legal entity cannot read a customer who moved to another
        one, and an invoice they can read must not drop out of the register for it."""
        rows = self.session.execute(
            select(Invoice, Organization)
            .outerjoin(Organization, Organization.id == Invoice.customer_id)
            .where(Invoice.year == year, *revenue_filter(), *invoice_legal_entity(legal_entity_id))
            .order_by(Invoice.number)
        ).all()
        return [(invoice, customer) for invoice, customer in rows]

    def year_lines(self, invoice_ids: list[UUID]) -> list[InvoiceLine]:
        """The lines of the invoices `year_invoices` returned, by line number. Asked by id
        rather than by year: the lines of an issued invoice never change, so reading them
        for exactly the invoices the register lists keeps the two in step even when
        another invoice is issued between the two statements."""
        if not invoice_ids:
            return []
        return list(
            self.session.scalars(
                select(InvoiceLine)
                .where(InvoiceLine.invoice_id.in_(invoice_ids))
                .order_by(InvoiceLine.invoice_id, InvoiceLine.line_number)
            )
        )

    def year_annulled(self, year: int, legal_entity_id: UUID) -> int:
        """How many invoices the year's register struck through: numbered, then annulled."""
        return int(
            self.session.execute(
                select(func.count(Invoice.id)).where(
                    Invoice.year == year,
                    Invoice.type == "invoice",
                    Invoice.status == "cancelled",
                    Invoice.deleted_at.is_(None),
                    *invoice_legal_entity(legal_entity_id),
                )
            ).scalar_one()
        )


__all__ = ["AnalyticsRepository"]
