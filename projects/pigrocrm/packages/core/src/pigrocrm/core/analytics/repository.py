"""Every aggregate query of this slice, in one file.

Deliberately one module rather than a query beside each caller: "where does `ricavi`
come from" must have exactly one answer to read. The previous system's defect was a *dispersed*
aggregation -- `hoursByOfferKey.get(offer.id) || hoursByOfferKey.get(offer.fileName) ||
hoursByOfferKey.get(offer.offerName) || 0` took the **first non-empty bucket instead of
their sum**, so hours logged against an offer's name vanished if a single hour had been
logged against its id, and `linkedExpenseMap` lost costs the same way, silently. With one
required foreign key there are no buckets to merge and the sum is a `GROUP BY deal_id`;
this file is the structural counterpart of that.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, NamedTuple
from uuid import UUID

from sqlalchemy import ColumnElement, Select, SQLColumnExpression, and_, case, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from pigrocrm.core.analytics.schemas import CashBase, RevenueBase
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.emitter.models import LegalEntity
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.money import ZERO_MONEY, line_value, round_money, sum_hours, sum_money
from pigrocrm.core.timetracking.models import Cost, TimeEntry
from pigrocrm.core.work_units.models import WORK_UNIT_COMMITTED_STATI, WorkUnit


def _revenue_filter() -> tuple[ColumnElement[bool], ...]:
    """The one definition of "an invoice that is revenue" (§7.1).

    `fattura`, never `proforma`: a proforma does not touch the register (slice 3 §5).
    `emessa`, never `annullata`: an annulled invoice keeps its number but not its revenue
    -- the struck-through page of a paper register. And `deleted_at IS NULL`, which on an
    issued invoice is guaranteed by `ck_invoices_no_delete_once_consumed` anyway: the
    filter is here so that the query and the constraint cannot drift apart.

    A function rather than a module-level tuple only because a shared expression object
    reused across statements is a subtlety nobody should have to think about at the call
    site; the three comparisons cost nothing to rebuild.
    """
    return (
        Invoice.tipo == "fattura",
        Invoice.stato == "emessa",
        Invoice.deleted_at.is_(None),
    )


def _invoice_azienda(azienda_id: UUID | None) -> tuple[ColumnElement[bool], ...]:
    """One azienda's invoices, or every azienda's (REB-630, spec 2026-10-03 §1.9):
    the one extra predicate the sums below take, so an azienda's figure reads that
    azienda's register alone and «tutte» stays the unfiltered sum."""
    return () if azienda_id is None else (Invoice.azienda_id == azienda_id,)


def _accrual_date() -> SQLColumnExpression[date | None]:
    """The one definition of "the month a document's period names":
    `coalesce(competenza_da, data_emissione)`, so a document that never declared a
    period is read by the one date it has and a register with no periods reads the same
    under either base. Shared by the period P&L (ORB-61) and the cash view (ORB-133), so
    the two screens cannot put the same invoice in two different months."""
    return func.coalesce(Invoice.competenza_da, Invoice.data_emissione)


def _revenue_date(base: RevenueBase) -> SQLColumnExpression[date | None]:
    """The date that puts an invoice's revenue in a period, for the two readings the
    period P&L offers (ORB-61). `emissione` is the recorded default (§7.1);
    `competenza` is `_accrual_date`. The fiscal figures take no base and stay by
    emission; the cash view has a base of its own (`CashBase`, `_cash_date`)."""
    if base == "competenza":
        return _accrual_date()
    return Invoice.data_emissione


def _cash_date(
    base: CashBase, by_money: SQLColumnExpression[date | None]
) -> SQLColumnExpression[date | None]:
    """The date that puts a document's money in a month of the cash view (ORB-133).
    Under `competenza` it is `_accrual_date` for every series; under `incasso` it is
    whatever the money's own reading is for that series, which the caller names."""
    if base == "competenza":
        return _accrual_date()
    return by_money


class RevenueByCustomerRow(NamedTuple):
    customer_id: UUID
    ragione_sociale: str
    ricavi: Decimal
    fatture: int
    # This row's own share of the *year's whole* revenue, in [0, 1] -- never of the
    # largest row here, which is what `InvoiceRepository._quota` scales against for a
    # different question ("who owes the most"). A concentration figure caps a share of
    # the total, so the denominator is `annual_revenue(anno)` itself.
    quota: float
    # The azienda whose `annual_revenue` is the denominator (REB-630): with one azienda
    # it is the same id on every row, in «tutte» it is what groups them.
    azienda_id: UUID


class AnalyticsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def deal_revenue(self, deal_id: UUID) -> tuple[Decimal, int]:
        """`(Σ imponibile, count)` for one deal.

        `imponibile`, never `totale`: the total includes VAT, which is not revenue but
        money collected on the State's behalf. Under the forfettario the two coincide,
        which is exactly why the choice is made here and tested against a synthetic
        `RF01` profile rather than left to be noticed.

        The count is returned alongside because a revenue of `0.00` with three invoices
        behind it and one with none are different situations, and the UI says which.
        """
        total, count = self.session.execute(
            select(func.coalesce(func.sum(Invoice.imponibile), 0), func.count()).where(
                Invoice.deal_id == deal_id, *_revenue_filter()
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
                    select(func.coalesce(func.sum(Cost.importo), 0)).where(
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

    def _azienda_scope[*Ts](
        self, stmt: Select[*Ts], azienda_id: UUID | None, column: InstrumentedAttribute[Any]
    ) -> Select[*Ts]:
        """A deal-keyed table narrowed to one azienda through its deal (REB-630): an
        hour's azienda is the deal's, so the predicate is the same subquery
        `TimeEntryRepository.list` uses rather than a column that table would copy. A
        cost has its own `azienda_id` (equal to its deal's by `CostService`, indexed)
        and every cost read in this file uses that column instead."""
        if azienda_id is None:
            return stmt
        return stmt.where(column.in_(select(Deal.id).where(Deal.azienda_id == azienda_id)))

    def revenue_in_range(
        self,
        da: date,
        a: date,
        customer_id: UUID | None,
        base: RevenueBase = "emissione",
        azienda_id: UUID | None = None,
    ) -> dict[UUID, Decimal]:
        """Revenue is attributed to the period by **its own** date -- `data_emissione` --
        not by the deal's date, which does not exist, and not by one common date, which
        none of the three quantities has (§7.4). `base="competenza"` is the second
        reading (ORB-61): the accrual period the document declares, falling back to the
        emission date for a document that declared none. Same filter, same figure,
        different month."""
        when = _revenue_date(base)
        stmt = (
            select(Invoice.deal_id, func.coalesce(func.sum(Invoice.imponibile), 0))
            .where(Invoice.deal_id.isnot(None), when >= da, when <= a, *_revenue_filter())
            .group_by(Invoice.deal_id)
        )
        stmt = self._customer_scope(stmt, customer_id, Invoice.deal_id)
        # Through the deal, not the invoice's own column: the period P&L is a report
        # per deal, and `deals_in_range`, the costs and the hours all read the deal's
        # azienda, so a deal and its revenue can never land on two sides of the line
        # (an import may put an invoice on another azienda than its deal's).
        stmt = self._azienda_scope(stmt, azienda_id, Invoice.deal_id)
        # `deal_id is not None` restates the `WHERE` for the type checker: the column is
        # nullable on the model and the statement already keeps only the rows that have one.
        return {
            deal_id: Decimal(total)
            for deal_id, total in self.session.execute(stmt).all()
            if deal_id is not None
        }

    def costs_in_range(
        self, da: date, a: date, customer_id: UUID | None, azienda_id: UUID | None = None
    ) -> tuple[dict[UUID, Decimal], Decimal]:
        """`({deal_id: total}, general_expenses)`. Costs are attributed by `costs.data`.

        General expenses -- `deal_id IS NULL` -- come back separately and are never
        distributed: any apportionment key would make a deal's margin move when a
        different deal was invoiced (§7.4). A customer filter excludes them entirely,
        because a general expense belongs to no customer by definition. An azienda
        filter keeps the ones written for that azienda and drops the shared ones
        (`azienda_id IS NULL`), which appear only in «tutte» (REB-630, spec §1.7).
        """
        per_deal: dict[UUID, Decimal] = {}
        stmt = (
            select(Cost.deal_id, func.coalesce(func.sum(Cost.importo), 0))
            .where(
                Cost.deal_id.isnot(None),
                Cost.data >= da,
                Cost.data <= a,
                Cost.deleted_at.is_(None),
            )
            .group_by(Cost.deal_id)
        )
        stmt = self._customer_scope(stmt, customer_id, Cost.deal_id)
        if azienda_id is not None:
            stmt = stmt.where(Cost.azienda_id == azienda_id)
        for deal_id, total in self.session.execute(stmt).all():
            if deal_id is not None:  # restates the `WHERE` for the type checker, as above
                per_deal[deal_id] = Decimal(total)

        if customer_id is not None:
            return per_deal, ZERO_MONEY
        generali = select(func.coalesce(func.sum(Cost.importo), 0)).where(
            Cost.deal_id.is_(None),
            Cost.data >= da,
            Cost.data <= a,
            Cost.deleted_at.is_(None),
        )
        if azienda_id is not None:
            generali = generali.where(Cost.azienda_id == azienda_id)
        general = round_money(Decimal(self.session.execute(generali).scalar_one()))
        return per_deal, general

    def labour_cost_in_range(
        self, da: date, a: date, customer_id: UUID | None, azienda_id: UUID | None = None
    ) -> dict[UUID, Decimal]:
        """`Σ ROUND(ore × costo_applicato, 2)`, summed **per row** and then added -- never
        `ROUND(Σ ore × costo, 2)`.

        Computed in Python rather than in SQL on purpose: Postgres would round the
        product with its own rule, and §6.2 fixes `ROUND_HALF_UP` per row in
        `money.py` as the single authority. Two rounding implementations is how the
        printed column and the total come to disagree.
        """
        stmt = select(TimeEntry.deal_id, TimeEntry.ore, TimeEntry.costo_applicato).where(
            TimeEntry.data >= da,
            TimeEntry.data <= a,
            TimeEntry.deleted_at.is_(None),
            TimeEntry.costo_applicato.isnot(None),
        )
        stmt = self._customer_scope(stmt, customer_id, TimeEntry.deal_id)
        stmt = self._azienda_scope(stmt, azienda_id, TimeEntry.deal_id)
        grouped: dict[UUID, list[Decimal | None]] = defaultdict(list)
        for deal_id, ore, costo in self.session.execute(stmt).all():
            grouped[deal_id].append(line_value(ore, costo))
        return {deal_id: sum_money(values) for deal_id, values in grouped.items()}

    def hours_in_range(
        self, da: date, a: date, customer_id: UUID | None, azienda_id: UUID | None = None
    ) -> dict[UUID, Decimal]:
        stmt = (
            select(TimeEntry.deal_id, func.coalesce(func.sum(TimeEntry.ore), 0))
            .where(TimeEntry.data >= da, TimeEntry.data <= a, TimeEntry.deleted_at.is_(None))
            .group_by(TimeEntry.deal_id)
        )
        stmt = self._customer_scope(stmt, customer_id, TimeEntry.deal_id)
        stmt = self._azienda_scope(stmt, azienda_id, TimeEntry.deal_id)
        return {row[0]: Decimal(row[1]) for row in self.session.execute(stmt).all()}

    def _committed_work_units_backlog(
        self,
        da: date | None,
        a: date | None,
        customer_id: UUID | None,
        azienda_id: UUID | None = None,
    ) -> tuple[Decimal, int, int]:
        """`(valore, voci, voci_senza_tariffa)` -- `unbilled_backlog`'s REB-372
        contribution from `work_units`: every day in an approved-or-later state
        (`WORK_UNIT_COMMITTED_STATI`) with no invoice line yet, priced against
        whichever of its own contract's rate cards covers its date.

        Contributes to `valore`/`voci` only, never to `ore_fatturabili_non_fatturate`:
        `WorkUnit.quantita` is priced in whatever unit its rate card names (a day, a
        month, a lump sum -- `RATE_CARD_UNITA`, not always an hour), so folding it into
        an hours total would silently misreport a day as an hour. A day whose date has
        no rate card in force prices at nothing and is counted in `voci_senza_tariffa`
        instead -- the same "a missing rate is not a rate of zero" refusal `line_value`
        gives `TimeEntry`, not the harder `ValidationFailed` `unbilled_work_unit_lines`
        gives an actual invoicing attempt: a read-only aggregate must survive a coverage
        gap, not raise because of one.
        """
        stmt = select(WorkUnit.contract_id, WorkUnit.data, WorkUnit.quantita).where(
            WorkUnit.stato.in_(WORK_UNIT_COMMITTED_STATI),
            WorkUnit.invoice_line_id.is_(None),
        )
        if da is not None:
            stmt = stmt.where(WorkUnit.data >= da)
        if a is not None:
            stmt = stmt.where(WorkUnit.data <= a)
        if customer_id is not None or azienda_id is not None:
            # A day's azienda is its contract's (REB-630), as its customer is.
            stmt = stmt.join(Contract, Contract.id == WorkUnit.contract_id)
        if customer_id is not None:
            stmt = stmt.where(Contract.customer_id == customer_id)
        if azienda_id is not None:
            stmt = stmt.where(Contract.azienda_id == azienda_id)
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
        for contract_id, giorno, quantita in rows:
            card = next(
                (
                    c
                    for c in cards_by_contract.get(contract_id, [])
                    if c.valido_da <= giorno and (c.valido_a is None or giorno <= c.valido_a)
                ),
                None,
            )
            values.append(round_money(Decimal(quantita) * card.importo) if card else None)
        return (
            sum_money(values),
            len(rows),
            sum(1 for value in values if value is None),
        )

    def unbilled_backlog(
        self,
        da: date | None = None,
        a: date | None = None,
        customer_id: UUID | None = None,
        azienda_id: UUID | None = None,
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

        `tariffa_applicata IS NULL` rows are counted in `ore` and in `voci_senza_tariffa`
        and contribute nothing to `valore`: a missing rate is not a rate of zero, and
        `line_value` is what refuses to conflate them.

        **REB-372: `work_units` narrows this figure's own contribution, it does not
        replace it.** `TimeEntry` has no state machine at all (REB-352 §1.2's own
        citation) and every one of its billable, unbilled hours has always counted
        here unconditionally -- that half is unchanged by this method. `work_units`
        is the one source that *does* carry an approval state (REB-358/REB-359), so
        its own contribution is narrowed to `WORK_UNIT_COMMITTED_STATI` --
        approved-or-later days only, never a merely `proposto` one and never a
        `lavorato_senza_approvazione` day the "propose, human confirms" invariant has
        not cleared -- and folded into `valore`/`voci` alongside the unchanged
        `TimeEntry` figures (`_committed_work_units_backlog`).
        """
        billed = (
            select(InvoiceLine.id)
            .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .where(InvoiceLine.id == TimeEntry.invoice_line_id, *_revenue_filter())
        )
        # An entry whose line this session cannot see at all is not backlog either: an
        # import may bind a deal's hours to an invoice of another azienda, and under the
        # policies «no issued invoice found» must not read as «not yet invoiced»
        # (REB-634; the freeze guard of `billed_entry_ids` reads it the same way).
        visible_line = select(InvoiceLine.id).where(InvoiceLine.id == TimeEntry.invoice_line_id)
        stmt = select(TimeEntry.ore, TimeEntry.tariffa_applicata).where(
            TimeEntry.deleted_at.is_(None),
            TimeEntry.fatturabile.is_(True),
            or_(
                TimeEntry.invoice_line_id.is_(None),
                and_(visible_line.exists(), ~billed.exists()),
            ),
        )
        if da is not None:
            stmt = stmt.where(TimeEntry.data >= da)
        if a is not None:
            stmt = stmt.where(TimeEntry.data <= a)
        stmt = self._customer_scope(stmt, customer_id, TimeEntry.deal_id)
        stmt = self._azienda_scope(stmt, azienda_id, TimeEntry.deal_id)

        rows = self.session.execute(stmt).all()
        wu_valore, wu_voci, wu_senza_tariffa = self._committed_work_units_backlog(
            da, a, customer_id, azienda_id
        )
        return (
            sum_hours([ore for ore, _ in rows]),
            sum_money([line_value(ore, tariffa) for ore, tariffa in rows]) + wu_valore,
            sum(1 for _, tariffa in rows if tariffa is None) + wu_senza_tariffa,
            len(rows) + wu_voci,
        )

    def late_entry_count(self, da: date, a: date, azienda_id: UUID | None = None) -> int:
        """How many rows dated inside the period were written **after** it ended
        (`created_at > a`). It is what tells a reader whether the figure can still move,
        and it is free: a COUNT over two columns that already exist (§6.4). Under an
        azienda the hours are its deals' and the costs the ones that carry its id
        (REB-630): a cost with a deal copied the deal's, a shared one stays out."""
        ore = (
            select(func.count())
            .select_from(TimeEntry)
            .where(
                TimeEntry.data >= da,
                TimeEntry.data <= a,
                TimeEntry.deleted_at.is_(None),
                func.date(TimeEntry.created_at) > a,
            )
        )
        ore = self._azienda_scope(ore, azienda_id, TimeEntry.deal_id)
        spese = (
            select(func.count())
            .select_from(Cost)
            .where(
                Cost.data >= da,
                Cost.data <= a,
                Cost.deleted_at.is_(None),
                func.date(Cost.created_at) > a,
            )
        )
        if azienda_id is not None:
            spese = spese.where(Cost.azienda_id == azienda_id)
        entries = self.session.execute(ore).scalar_one()
        costs = self.session.execute(spese).scalar_one()
        return int(entries) + int(costs)

    def monthly_incassato(
        self, anno: int, base: CashBase = "competenza", azienda_id: UUID | None = None
    ) -> dict[int, Decimal]:
        """`Σ totale` of revenue invoices paid, per month of `anno` -- money in the bank,
        so `totale`, as `sum_da_incassare` reasons. By the accrual period the invoice
        declares, or under `incasso` by `data_incasso`, where an invoice marked paid with
        no date falls back to its issue date rather than vanishing."""
        when = _cash_date(base, func.coalesce(Invoice.data_incasso, Invoice.data_emissione))
        month = func.extract("month", when)
        year = func.extract("year", when)
        rows = self.session.execute(
            select(month, func.sum(Invoice.totale))
            .where(
                *_revenue_filter(),
                *_invoice_azienda(azienda_id),
                Invoice.stato_pagamento == "incassato",
                year == anno,
            )
            .group_by(month)
        ).all()
        return {int(m): round_money(Decimal(total)) for m, total in rows}

    def monthly_da_incassare(
        self, anno: int, base: CashBase = "competenza", azienda_id: UUID | None = None
    ) -> dict[int, Decimal]:
        """`Σ totale` of revenue invoices still unpaid, per month. By the accrual period
        the invoice declares, or under `incasso` by the month it falls due (issue date
        when no due date was set): the month the money is expected."""
        when = _cash_date(base, func.coalesce(Invoice.data_scadenza, Invoice.data_emissione))
        month = func.extract("month", when)
        year = func.extract("year", when)
        rows = self.session.execute(
            select(month, func.sum(Invoice.totale))
            .where(
                *_revenue_filter(),
                *_invoice_azienda(azienda_id),
                Invoice.stato_pagamento == "da_incassare",
                year == anno,
            )
            .group_by(month)
        ).all()
        return {int(m): round_money(Decimal(total)) for m, total in rows}

    def monthly_bozze(
        self, anno: int, base: CashBase = "competenza", azienda_id: UUID | None = None
    ) -> dict[int, Decimal]:
        """`Σ totale` of what is written but not yet an issued invoice: draft invoices and
        live proformas (not the ones already turned into an invoice, which would count
        twice). By the accrual period the document declares; under `incasso`, by the
        document's own date. Either way, the day it was created when it has neither.

        Since ORB-63 a proforma always has a date: `data_emissione` is the one the sender
        put on the document, so under `incasso` a proforma dated 5 September for August's
        work is September's projected money, exactly as the fattura it becomes will be;
        under `competenza` it is August's, because that is the period it declares. A
        `fattura` draft still has no date until `issue` and stays bucketed by the day it
        was created when it declares no period; `created_at` is the fallback for it alone.
        """
        when = func.coalesce(
            _cash_date(base, Invoice.data_emissione), func.date(Invoice.created_at)
        )
        month = func.extract("month", when)
        year = func.extract("year", when)
        rows = self.session.execute(
            select(month, func.sum(Invoice.totale))
            .where(
                Invoice.deleted_at.is_(None),
                *_invoice_azienda(azienda_id),
                year == anno,
                (
                    ((Invoice.tipo == "fattura") & (Invoice.stato == "bozza"))
                    | (
                        (Invoice.tipo == "proforma")
                        & Invoice.stato.in_(("bozza", "emessa", "confermata"))
                    )
                ),
            )
            .group_by(month)
        ).all()
        return {int(m): round_money(Decimal(total)) for m, total in rows}

    def monthly_costi(self, anno: int, azienda_id: UUID | None = None) -> dict[int, Decimal]:
        """`Σ importo` of costs by the month they were incurred, deal or no deal. Under
        one azienda, the costs that carry its id: a cost with a deal copied the deal's,
        a shared one (`NULL`) is nobody's and shows only in «tutte» (REB-630, §1.7)."""
        month = func.extract("month", Cost.data)
        year = func.extract("year", Cost.data)
        stmt = (
            select(month, func.sum(Cost.importo))
            .where(Cost.deleted_at.is_(None), year == anno)
            .group_by(month)
        )
        if azienda_id is not None:
            stmt = stmt.where(Cost.azienda_id == azienda_id)
        rows = self.session.execute(stmt).all()
        return {int(m): round_money(Decimal(total)) for m, total in rows}

    def annual_revenue(self, anno: int, azienda_id: UUID | None = None) -> Decimal:
        """Every issued invoice of the year, deal or no deal: the fiscal estimate is
        about the person's income, so an invoice with no `deal_id` counts too. Of one
        azienda when named (REB-630): the estimate is one azienda's, and its caller
        always names one."""
        return round_money(
            Decimal(
                self.session.execute(
                    select(func.coalesce(func.sum(Invoice.imponibile), 0)).where(
                        Invoice.anno == anno, *_revenue_filter(), *_invoice_azienda(azienda_id)
                    )
                ).scalar_one()
            )
        )

    def annual_revenue_by_azienda(
        self, anno: int, azienda_id: UUID | None = None
    ) -> dict[UUID, Decimal]:
        """`annual_revenue`, grouped by the issuing azienda: the denominators of
        `revenue_by_customer` in «tutte» (REB-630), one per azienda, since a share of
        revenue is a share of one azienda's revenue."""
        rows = self.session.execute(
            select(Invoice.azienda_id, func.coalesce(func.sum(Invoice.imponibile), 0))
            .where(Invoice.anno == anno, *_revenue_filter(), *_invoice_azienda(azienda_id))
            .group_by(Invoice.azienda_id)
        ).all()
        return {azienda: round_money(Decimal(total)) for azienda, total in rows}

    def revenue_by_customer(
        self, anno: int, azienda_id: UUID | None = None
    ) -> list[RevenueByCustomerRow]:
        """Each customer's own share of the year's invoiced revenue (§1.5, §5 item 1 of
        `docs/superpowers/specs/2026-09-23-forecasting-and-analytics-from-mastro-design.md`):
        `Σ imponibile` grouped by `customer_id`, over the same `_revenue_filter()`
        `annual_revenue` applies, each row's own share of that same annual total --
        ranked, largest first.

        **Not** `InvoiceRepository.receivables_by_customer`'s `quota`: that scales a
        customer's outstanding receivable against the *largest* customer's own exposure
        and answers "who currently owes the most". A concentration figure caps a
        customer's share of *total* invoiced income instead, so the denominator here is
        the whole year's `annual_revenue`, never the top row -- the two are easy to
        conflate because both render as a per-customer percentage, and they are not the
        same figure.

        A customer with no invoice this year is simply absent, the same way
        `receivables_by_customer` omits one with nothing outstanding.

        Per azienda since REB-630 (spec 2026-10-03 §1.9): the denominator is the
        issuing azienda's own `annual_revenue`, never the space's, because a customer
        who is a third of the SRL and a tenth of the whole has to read as a third. With
        `azienda_id` the rows are that azienda's; without, every azienda's, grouped in
        the order the selector lists them (the default first) and ranked inside each
        group, so a one-azienda space reads exactly what it read before.
        """
        totali = self.annual_revenue_by_azienda(anno, azienda_id)
        ricavi = func.coalesce(func.sum(Invoice.imponibile), 0)
        stmt = (
            select(
                Invoice.azienda_id,
                Customer.id,
                Customer.ragione_sociale,
                ricavi,
                func.count(Invoice.id),
            )
            .join(Customer, Customer.id == Invoice.customer_id)
            .join(LegalEntity, LegalEntity.id == Invoice.azienda_id)
            .where(Invoice.anno == anno, *_revenue_filter(), *_invoice_azienda(azienda_id))
            .group_by(
                Invoice.azienda_id,
                LegalEntity.predefinita,
                LegalEntity.nome,
                Customer.id,
                Customer.ragione_sociale,
            )
            .order_by(
                LegalEntity.predefinita.desc(),
                LegalEntity.nome,
                Invoice.azienda_id,
                ricavi.desc(),
                Customer.ragione_sociale,
            )
        )
        righe: list[RevenueByCustomerRow] = []
        for azienda, customer_id, ragione_sociale, importo, numero in self.session.execute(
            stmt
        ).all():
            valore = round_money(Decimal(importo))
            totale_anno = totali.get(azienda, ZERO_MONEY)
            righe.append(
                RevenueByCustomerRow(
                    customer_id=customer_id,
                    ragione_sociale=ragione_sociale,
                    ricavi=valore,
                    fatture=int(numero),
                    quota=float(valore / totale_anno) if totale_anno > ZERO_MONEY else 0.0,
                    azienda_id=azienda,
                )
            )
        return righe

    def count_over_concentration_threshold(
        self, anno: int, soglia: float, azienda_id: UUID | None = None
    ) -> int:
        """How many customers cross `soglia`, a configured preferred-share threshold
        (`Settings.concentrazione_soglia_preferita`, §3 and §5 item 2 of
        `docs/superpowers/specs/2026-09-23-forecasting-and-analytics-from-mastro-design.md`):
        the COUNT `OperationalDashboard`'s fourth signal reads (REB-371).

        Strictly above, never at it: a customer sitting exactly on the configured share
        has not yet crossed it. Reuses `revenue_by_customer`'s own rows rather than a
        second query, so the signal and the concentration table its `collegamento`
        points to can never disagree on which customer this counts -- the same
        drill-through discipline `count_deals_invoiced_not_won` and its siblings apply
        with their own filtered lists.

        In «tutte» (REB-630) the count is of distinct customers over the threshold in
        any azienda, each share read against its own azienda's revenue: never a
        space-wide share, which would hide the customer who is a third of one azienda
        and a tenth of the whole, and never twice for a customer two aziende both
        depend on, since the signal counts people to talk to.
        """
        return len(
            {
                row.customer_id
                for row in self.revenue_by_customer(anno, azienda_id)
                if row.quota > soglia
            }
        )

    def deals_in_range(
        self,
        da: date,
        a: date,
        customer_id: UUID | None,
        base: RevenueBase = "emissione",
        azienda_id: UUID | None = None,
    ) -> list[Deal]:
        """Every deal with any activity in the window -- an issued invoice, a cost or an
        hour. Not "every deal": a period report listing deals with nothing in the period
        is the unbounded growth residual B3 describes, and the window is what bounds it.

        `base` has to be the one `revenue_in_range` was asked with: an invoice that the
        accrual reading puts in August makes its deal active in August, and a deal
        absent from this list is revenue the report silently drops.
        """
        when = _revenue_date(base)
        active = (
            select(Invoice.deal_id.label("deal_id"))
            .where(Invoice.deal_id.isnot(None), when >= da, when <= a, *_revenue_filter())
            .union(
                select(Cost.deal_id.label("deal_id")).where(
                    Cost.deal_id.isnot(None),
                    Cost.data >= da,
                    Cost.data <= a,
                    Cost.deleted_at.is_(None),
                ),
                select(TimeEntry.deal_id.label("deal_id")).where(
                    TimeEntry.data >= da, TimeEntry.data <= a, TimeEntry.deleted_at.is_(None)
                ),
            )
            .subquery()
        )
        stmt = select(Deal).where(Deal.id.in_(select(active.c.deal_id)), Deal.deleted_at.is_(None))
        if customer_id is not None:
            stmt = stmt.where(Deal.customer_id == customer_id)
        if azienda_id is not None:
            # The deal's own column, not the invoice's: a deal keeps its azienda when its
            # customer moves (spec §1.7), and this list is of deals.
            stmt = stmt.where(Deal.azienda_id == azienda_id)
        return list(self.session.execute(stmt.order_by(Deal.nome, Deal.id)).scalars())

    def revenue_for_customer_in_window(
        self,
        customer_id: UUID,
        da: date,
        a: date,
        base: RevenueBase = "emissione",
        azienda_id: UUID | None = None,
    ) -> tuple[Decimal, Decimal]:
        """`(ricavi del cliente, ricavi totali)` over `[da, a]` -- the two figures a
        concentration cap divides (REB-352 §1.5), summed in one statement so the two
        sides of the ratio can never read a different snapshot. Same revenue
        definition `annual_revenue` uses (`_revenue_filter`), windowed by `da`/`a`
        rather than by `Invoice.anno`: a contract's own anniversary year rarely lines
        up with the calendar one `anno` names. Within one azienda when named
        (REB-630): the cap is the contract's azienda's, so both sides of the ratio
        read that register alone.
        """
        when = _revenue_date(base)
        cliente = func.coalesce(
            func.sum(case((Invoice.customer_id == customer_id, Invoice.imponibile), else_=0)), 0
        )
        totale = func.coalesce(func.sum(Invoice.imponibile), 0)
        row = self.session.execute(
            select(cliente, totale).where(
                when >= da, when <= a, *_revenue_filter(), *_invoice_azienda(azienda_id)
            )
        ).one()
        return round_money(Decimal(row[0])), round_money(Decimal(row[1]))


__all__ = ["AnalyticsRepository"]
