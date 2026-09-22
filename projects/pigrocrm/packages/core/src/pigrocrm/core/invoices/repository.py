"""Queries only. A repository never commits (project rule): every method reads or
flushes, and the surrounding `InvoiceService` method is the one transaction.

From slice 6 this file also owns the invoice-side *aggregates* the dashboards read. They
live here rather than on `InvoiceService` for the reason §3 rule 2 gives -- a single-table
`COUNT` or `SUM` belongs to the repository of that table, even when the dashboard asking
for it belongs to another slice -- and for a concrete second reason: slice 3 §11 fixes its
MCP exclusion list at exactly four names, so a new public method on `InvoiceService` would
force either a new tool or an edit to another slice's declared list.
"""

from collections.abc import Collection
from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import ColumnElement, delete, distinct, func, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.invoices.models import (
    PROFORMA_SEQUENCE_NAME,
    Invoice,
    InvoiceCounter,
    InvoiceLine,
    InvoiceRegisterGap,
)
from pigrocrm.core.invoices.schemas import InvoiceListQuery
from pigrocrm.core.money import round_money
from pigrocrm.core.pipeline.models import PipelineStage


def _issued_filter() -> tuple[ColumnElement[bool], ...]:
    """The one definition of "an invoice that exists as a fiscal document".

    Identical to `AnalyticsRepository._revenue_filter` on purpose and not imported from it:
    that function answers "which invoices are revenue" and this one answers "which invoices
    are documents somebody owes on". They coincide today because both mean "a `fattura`
    that was issued and not annulled", and the day they stop coinciding a shared alias
    would silently pick a side -- the same reasoning `analytics/schemas.py` records for
    `DealStato`.
    """
    return (
        Invoice.deleted_at.is_(None),
        Invoice.tipo == "fattura",
        Invoice.stato == "emessa",
    )


def _receivable_filter() -> tuple[ColumnElement[bool], ...]:
    """An issued invoice nobody has paid. The base of both "Da incassare" and "Scaduto",
    which is what makes the second a subset of the first rather than a figure of its own
    (§5.2)."""
    return (*_issued_filter(), Invoice.stato_pagamento == "da_incassare")


def _overdue_predicate() -> tuple[ColumnElement[bool], ...]:
    """A receivable past its due date, strictly.

    Due today is due today, so `<` and not `<=`. A null `data_scadenza` is never overdue,
    which Postgres's three-valued logic gives for free -- the explicit `IS NOT NULL` is
    there because relying on that silently is how the opposite gets implemented by
    accident.

    `today_local()` and not `CURRENT_DATE`: `CURRENT_DATE` is the *database server's* day,
    and every date in this product is a day in the emitter's zone (`db/clock.py`). Read at
    call time, never bound once at import, so a process that outlives midnight in Rome does
    not keep answering yesterday.
    """
    return (
        *_receivable_filter(),
        Invoice.data_scadenza.is_not(None),
        Invoice.data_scadenza < today_local(),
    )


def invoiced_not_won_predicate() -> tuple[ColumnElement[bool], ...]:
    """A deal with an issued invoice that is still sitting on an `open` stage (§6.2).

    `tipo = 'open'`, not `tipo <> 'won'`. The drill-through is a list of deals to go and
    win -- almost always the stage somebody forgot to move -- and an invoiced deal parked
    on `perso` is a different problem with a different remedy, which this signal must not
    silently absorb.

    Written as a predicate rather than inlined because slice 6 §6.2's card and its
    drill-through must be the *same* predicate: `DealRepository.list` filters on this same
    tuple, so the count on the card and the length of the list behind it cannot drift.

    Public, unlike `_issued_filter` and `_overdue_predicate` beside it, because its second
    caller lives in another module: `deals/repository.py` imports it for the
    `fatturato_non_vinto` drill-through. A leading underscore reached across a package
    boundary is a worse signal than a public name.
    """
    return (
        *_issued_filter(),
        Deal.deleted_at.is_(None),
        PipelineStage.tipo == "open",
    )


class InvoiceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, invoice_id: UUID, *, include_deleted: bool = False) -> Invoice | None:
        invoice = self.session.get(Invoice, invoice_id)
        if invoice is None:
            return None
        if invoice.deleted_at is not None and not include_deleted:
            return None
        return invoice

    def add(self, invoice: Invoice) -> Invoice:
        self.session.add(invoice)
        self.session.flush()
        return invoice

    def lines(self, invoice_id: UUID) -> list[InvoiceLine]:
        stmt = (
            select(InvoiceLine)
            .where(InvoiceLine.invoice_id == invoice_id)
            .order_by(InvoiceLine.numero_linea)
        )
        return list(self.session.execute(stmt).scalars())

    def clear_lines(self, invoice_id: UUID) -> None:
        """A real `DELETE`, then a fresh insert of the whole list.

        Bulk replacement rather than a per-line diff, for the reason spec 11 gives: it
        is the natural shape of a line editor. It was also, until task 4B-1 closed A14,
        the only way to clear an optional numeric column at all -- there was no spelling
        of `InvoiceLineIn` that meant "set `sconto_importo` back to nothing". That is no
        longer the reason it exists, and lines still have no `Update` schema of their
        own: a line's totals are recomputed from the whole list, so patching one line in
        place would leave the invoice's totals to be reconciled separately.
        """
        self.session.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id))
        self.session.flush()

    def add_line(self, line: InvoiceLine) -> InvoiceLine:
        self.session.add(line)
        self.session.flush()
        return line

    def next_proforma_sequence(self) -> int:
        """`nextval` on the one proforma sequence.

        A `SEQUENCE` is the right tool here and the wrong one for the fiscal number,
        for the same property: it does not roll back. A gap in a proforma reference
        means nothing -- a proforma is not a register -- and in exchange this counter
        serialises nobody, which is exactly what the fiscal counter cannot afford to
        do and must do anyway.
        """
        return int(
            self.session.execute(text(f"SELECT nextval('{PROFORMA_SEQUENCE_NAME}')")).scalar_one()
        )

    def lock_counter(self, anno: int) -> InvoiceCounter:
        """The year's counter row, locked for the rest of this transaction.

        Two statements, in this order and for these reasons:

        1. `INSERT ... ON CONFLICT (anno) DO NOTHING` -- two concurrent
           first-invoices-of-the-year: one inserts, the other does nothing, both carry
           on. Without `ON CONFLICT` the loser would take a `UniqueViolation` and have
           to be retried by the caller.
        2. `SELECT ... FOR UPDATE` -- from here on every other emission for the same
           year waits. This is deliberately **the first row lock the emission
           transaction takes**, and no later statement in that transaction takes a lock
           a concurrent emission could already hold, so two emissions cannot deadlock
           against each other.

        Not a `SEQUENCE`, and that is the whole design: `nextval()` is
        non-transactional by design and does not roll back, so a sequence guarantees
        uniqueness while prohibiting exactly the property required here -- the absence
        of gaps. Every aborted transaction would leave a permanent hole in the
        register.

        The `SELECT` is `.one()`, not `.first()`: after step 1 the row must exist, and
        a `None` here would mean the insert silently did nothing for a reason worth
        crashing over rather than working around.
        """
        self.session.execute(
            text(
                "INSERT INTO invoice_counters (anno, ultimo_numero) "
                "VALUES (:anno, 0) ON CONFLICT (anno) DO NOTHING"
            ),
            {"anno": anno},
        )
        stmt = select(InvoiceCounter).where(InvoiceCounter.anno == anno).with_for_update()
        return self.session.execute(stmt).scalars().one()

    def last_issued_date(self, anno: int) -> date | None:
        """The `data_emissione` of the highest-numbered invoice of `anno`.

        Safe to read only *after* `lock_counter` has run, which is the one place it is
        called from: without the lock, a concurrent emission could commit a later
        number between this read and the write that depends on it. Includes
        `annullata` rows on purpose -- an annulled invoice keeps its number and its
        place in the chronological order, which is what makes the register monotonic
        rather than merely gap-free.
        """
        stmt = (
            select(Invoice.data_emissione)
            .where(Invoice.anno == anno, Invoice.numero.is_not(None))
            .order_by(Invoice.numero.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    # --- slice 9's import queries -------------------------------------------------

    def neighbour_dates(self, anno: int, numero: int) -> tuple[date | None, date | None]:
        """The dates on either side of `numero` in the register of `anno`.

        `last_issued_date` answers "what came last"; an import inserts *between*
        existing numbers, so monotonicity has to be checked against the nearest lower
        and the nearest higher number, whatever their states -- an annulled row keeps
        its place in the order exactly as it does for `last_issued_date`.
        """
        before = (
            select(Invoice.data_emissione)
            .where(Invoice.anno == anno, Invoice.numero.is_not(None), Invoice.numero < numero)
            .order_by(Invoice.numero.desc())
            .limit(1)
        )
        after = (
            select(Invoice.data_emissione)
            .where(Invoice.anno == anno, Invoice.numero.is_not(None), Invoice.numero > numero)
            .order_by(Invoice.numero.asc())
            .limit(1)
        )
        return (
            self.session.execute(before).scalars().first(),
            self.session.execute(after).scalars().first(),
        )

    def numbers_present(self, anno: int) -> set[int]:
        stmt = select(Invoice.numero).where(Invoice.anno == anno, Invoice.numero.is_not(None))
        # `Invoice.numero` is `Mapped[int | None]` at the column-type level; the
        # `is_not(None)` clause above is what the database enforces, and this cast is
        # what tells mypy the same thing rather than re-filtering `None` at runtime.
        return cast(set[int], set(self.session.execute(stmt).scalars().all()))

    def first_native_number(self, anno: int) -> int | None:
        """The lowest number this CRM itself issued in `anno` (§3.2 rule 6)."""
        stmt = select(func.min(Invoice.numero)).where(
            Invoice.anno == anno, Invoice.numero.is_not(None), Invoice.importata_da.is_(None)
        )
        return self.session.execute(stmt).scalar_one()

    def declared_gaps(self, anno: int) -> set[int]:
        stmt = select(InvoiceRegisterGap.numero).where(InvoiceRegisterGap.anno == anno)
        return set(self.session.execute(stmt).scalars().all())

    def gaps(self, anno: int) -> list[InvoiceRegisterGap]:
        stmt = (
            select(InvoiceRegisterGap)
            .where(InvoiceRegisterGap.anno == anno)
            .order_by(InvoiceRegisterGap.numero)
        )
        return list(self.session.execute(stmt).scalars().all())

    def add_gap(self, gap: InvoiceRegisterGap) -> InvoiceRegisterGap:
        self.session.add(gap)
        self.session.flush()
        return gap

    # --- slice 6's dashboard aggregates ------------------------------------------

    def sum_da_incassare(self) -> Decimal:
        """`Σ totale` over issued, unpaid, non-deleted invoices.

        **`totale`, not `imponibile`, and that is not an inconsistency with the revenue
        figure.** Revenue is `Σ imponibile` (slice 4 §7.1, and this slice introduces no
        third meaning); a receivable is what must arrive in the bank, which includes VAT --
        money collected on the State's behalf. Under the forfettario regime the two
        coincide and the difference is unobservable, which is exactly why it is written
        down now: the same condition, and the same answer, as slice 4 §7.1.

        This figure enters **no** margin and never shares a total row with revenue (§5.2).

        `round_money` rather than a bare `Decimal(...)`: `coalesce(sum(...), 0)` returns the
        *integer literal* on an empty register, and `Decimal("0")` reaches the wire as `"0"`
        while every other money field reaches it as `"0.00"`.
        """
        total = self.session.execute(
            select(func.coalesce(func.sum(Invoice.totale), 0)).where(*_receivable_filter())
        ).scalar_one()
        return round_money(Decimal(total))

    def sum_scaduto(self) -> Decimal:
        """The subset of `sum_da_incassare` past its due date.

        A **subset**, and rendered as one -- indented beneath it, never as a second addable
        line (§5.2). The predicate is literally `_receivable_filter()` plus two clauses, so
        the containment is structural rather than a property somebody has to remember.
        """
        total = self.session.execute(
            select(func.coalesce(func.sum(Invoice.totale), 0)).where(*_overdue_predicate())
        ).scalar_one()
        return round_money(Decimal(total))

    def count_emesse_in_periodo(self, da: date, a: date) -> int:
        """A `COUNT` on the same predicate the revenue figure uses, so the two cannot
        describe different sets -- "6 fatture emesse" beside a revenue total that included
        a seventh, or a proforma, is the shape of that defect.

        Attributed to the period by `data_emissione`, its own date, exactly as revenue is
        (slice 4 §7.4).
        """
        return int(
            self.session.execute(
                select(func.count(Invoice.id)).where(
                    *_issued_filter(),
                    Invoice.data_emissione.is_not(None),
                    Invoice.data_emissione >= da,
                    Invoice.data_emissione <= a,
                )
            ).scalar_one()
        )

    def list_emesse_in_periodo(self, da: date, a: date) -> list[Invoice]:
        """Issued invoices whose own date falls in the window, oldest first: the rows the
        weekly report lists under «Emesse questa settimana» (spec 2026-09-16 §3.1, §3.2).

        Shares `_issued_filter()` with `count_emesse_in_periodo`, whose count this list's
        length must always equal -- a report whose number and rows describe different
        sets is exactly the defect `test_count_emesse_in_periodo_counts_the_same_set_revenue_sums`
        already guards against for the count, and this list must not reopen it.
        """
        stmt = (
            select(Invoice)
            .where(*_issued_filter(), Invoice.data_emissione >= da, Invoice.data_emissione <= a)
            .order_by(Invoice.data_emissione, Invoice.numero)
        )
        return list(self.session.execute(stmt).scalars())

    def list_incassate_in_periodo(self, da: date, a: date) -> list[Invoice]:
        """Issued, collected invoices whose `data_incasso` falls in the window, in payment
        order: the rows behind «Incassate questa settimana» (spec 2026-09-16 §3.1, §3.2).

        Attributed to `data_incasso`, not `data_emissione` -- an invoice issued weeks ago
        and paid this week belongs here and not in `list_emesse_in_periodo`, which is the
        whole reason the report carries the two sections separately.
        """
        stmt = (
            select(Invoice)
            .where(
                *_issued_filter(),
                Invoice.stato_pagamento == "incassato",
                Invoice.data_incasso.is_not(None),
                Invoice.data_incasso >= da,
                Invoice.data_incasso <= a,
            )
            .order_by(Invoice.data_incasso)
        )
        return list(self.session.execute(stmt).scalars())

    def list_scadute(self, oggi: date) -> list[Invoice]:
        """Receivables due on or before `oggi`, worst first: the «scadute» half of §3.1's
        «Da incassare» (spec 2026-09-16).

        Not `SollecitiService.candidates`, which the weekly report used to read: that list
        answers «which invoices may I legitimately chase», so it drops anything inside the
        grace period, anything chased in the last few days and anything already at the
        reminder ceiling. Those are properties of a reminder, not of a debt, and a report
        that took them for the debt left out money that is owed today.

        `oggi` is a parameter rather than a `today_local()` read here, unlike
        `_overdue_predicate()` above: the report resolves the space's own settings and
        therefore its own timezone, and a repository reading the platform's default would
        answer a different day for exactly the installation whose zone differs.

        `<=` and not `_overdue_predicate()`'s `<`, which is the second reason this is its
        own predicate: an invoice due today is money the report names -- it prints «scade
        oggi» -- while «scaduto» on the dashboard means strictly past its date, so on a
        Monday with a debt due that day the list holds one row more than the «scaduto e
        non incassato» signal counts, and a shared helper would have to pick one of the
        two meanings for both.

        Ascending `data_scadenza`: the oldest debt first, which is the order somebody works
        the list in and the order `list_in_scadenza` beside it already answers in.
        """
        stmt = (
            select(Invoice)
            .where(
                *_receivable_filter(),
                Invoice.data_scadenza.is_not(None),
                Invoice.data_scadenza <= oggi,
            )
            .order_by(Invoice.data_scadenza, Invoice.numero)
        )
        return list(self.session.execute(stmt).scalars())

    def list_in_scadenza(self, da: date, a: date) -> list[Invoice]:
        """Receivables due within the window, soonest first: the «in scadenza nei prossimi
        sette giorni» half of §3.1's «Da incassare» section, once the caller passes that
        window instead of the register's whole horizon.

        `_receivable_filter()`, not `_overdue_predicate()`: a due date inside the window can
        be in the future, which `_overdue_predicate()`'s `< today_local()` clause would
        exclude. The already-overdue half of the same section comes from `sum_scaduto` and
        `count_scadute_non_incassate` instead, which this method leaves untouched.
        """
        stmt = (
            select(Invoice)
            .where(
                *_receivable_filter(),
                Invoice.data_scadenza.is_not(None),
                Invoice.data_scadenza >= da,
                Invoice.data_scadenza <= a,
            )
            .order_by(Invoice.data_scadenza)
        )
        return list(self.session.execute(stmt).scalars())

    def sum_emesse_in_periodo(self, da: date, a: date) -> Decimal:
        """`Σ totale` over `list_emesse_in_periodo`'s own predicate: the total the weekly
        report prints beside «Emesse questa settimana», and the same call `DigestService`
        reuses unchanged for the running month and the one before it (spec 2026-09-16
        §3.2), only with a different window.
        """
        total = self.session.execute(
            select(func.coalesce(func.sum(Invoice.totale), 0)).where(
                *_issued_filter(), Invoice.data_emissione >= da, Invoice.data_emissione <= a
            )
        ).scalar_one()
        return round_money(Decimal(total))

    def count_deals_invoiced_not_won(self) -> int:
        """§6.2's second signal: how many deals have an issued invoice and an open stage.

        Counts **deals**, not invoices: the drill-through lists deals, so two invoices on
        one open deal is one signal, not two. `distinct` is what makes that true.

        A `COUNT` across a join, which §3 permits explicitly; a `SUM` across one it does
        not, and this produces no money figure. The inner join on `deal_id` is also what
        keeps the register's unlinked invoices out -- a left join added later would count
        every one of them.
        """
        return int(
            self.session.execute(
                select(func.count(distinct(Deal.id)))
                .select_from(Invoice)
                .join(Deal, Deal.id == Invoice.deal_id)
                .join(PipelineStage, PipelineStage.id == Deal.pipeline_stage_id)
                .where(*invoiced_not_won_predicate())
            ).scalar_one()
        )

    def count_scadute_non_incassate(self) -> int:
        """§6.2's fourth signal: the candidate list of slice 5 §7.1's payment reminders,
        counted. The count **sends nothing** -- and saying so here is the point, because a
        count next to a list of overdue customers is exactly the place someone later adds a
        "send all" button.

        The same `_overdue_predicate()` `sum_scaduto` uses, so the count and the sum can
        never describe different rows.
        """
        return int(
            self.session.execute(
                select(func.count(Invoice.id)).where(*_overdue_predicate())
            ).scalar_one()
        )

    def unpaid_for_customer(self, customer_id: UUID, limit: int) -> list[Invoice]:
        """One customer's issued, unpaid invoices, oldest deadline first.

        The rows behind `sum_da_incassare` restricted to one customer, sharing
        `_receivable_filter()` literally rather than restating it: the total on the economic
        dashboard and the list in the `stato-cliente` briefing describe the same set, and a
        briefing that listed a different set from the figure beside it is the divergence
        §7.2 exists to make impossible.

        Nulls last, for the reason `pending_offers` gives: an invoice with no deadline is
        not the most urgent one, and a list meant to be read from the top must not open with
        the least informative row. Spelled out rather than relied upon, because Postgres's
        default flips with the sort direction.

        `limit` is required and has no default. This list goes into an MCP prompt, whose
        cost is paid in the model's context window on every render, so the caller has to
        say how much of it that window is worth -- a default here is a budget nobody
        decided.
        """
        return list(
            self.session.execute(
                select(Invoice)
                .where(*_receivable_filter(), Invoice.customer_id == customer_id)
                .order_by(Invoice.data_scadenza.asc().nulls_last(), Invoice.id.asc())
                .limit(limit)
            ).scalars()
        )

    def customer_names(self, customer_ids: Collection[UUID]) -> dict[UUID, str]:
        """The `ragione_sociale` of each given customer, in one query.

        The same method, for the same reason, as `DealRepository.customer_names`: one
        statement for a whole page rather than one per row, so a 50-row invoice list costs
        two queries and never fifty-one. A label lookup for rows already chosen, run after
        the limit.

        No `deleted_at` filter, deliberately, and here it matters more than for deals: an
        issued invoice outlives the customer relationship, and `CustomerService.soft_delete`
        may archive a customer whose invoices stay in the register. An archived customer's
        name is still the name on those invoices; filtering it out would print a dash on
        rows that have a perfectly good one.

        An empty input short-circuits: `IN ()` is a query with no possible rows.
        """
        if not customer_ids:
            return {}
        rows = self.session.execute(
            select(Customer.id, Customer.ragione_sociale).where(Customer.id.in_(customer_ids))
        ).all()
        return {row[0]: row[1] for row in rows}

    def payment_terms(self, customer_ids: Collection[UUID]) -> dict[UUID, tuple[int | None, bool]]:
        """`(giorni_pagamento, pagamento_fine_mese)` of each given customer, in one query
        (REB-326): what `issue` derives a due date from and what `get` forecasts one
        with. Shaped like `customer_names` so a caller with a page of rows can ask once.
        No `deleted_at` filter either: a draft for an archived customer still has terms."""
        if not customer_ids:
            return {}
        rows = self.session.execute(
            select(Customer.id, Customer.giorni_pagamento, Customer.pagamento_fine_mese).where(
                Customer.id.in_(customer_ids)
            )
        ).all()
        return {row[0]: (row[1], row[2]) for row in rows}

    # `list` must stay the last method defined in this class -- an unconditional
    # project rule (`test_module_imports.py`). Defining a method named `list` rebinds
    # that name in the *class* namespace, so any later method whose own return
    # annotation is a bare `list[...]` would resolve `list` to this method instead of
    # the builtin and fail at import time on Python 3.13.
    def list(self, query: InvoiceListQuery) -> list[Invoice]:
        stmt = select(Invoice).where(Invoice.deleted_at.is_(None))
        if query.customer_id:
            stmt = stmt.where(Invoice.customer_id == query.customer_id)
        if query.deal_id:
            stmt = stmt.where(Invoice.deal_id == query.deal_id)
        if query.tipo:
            stmt = stmt.where(Invoice.tipo == query.tipo)
        if query.stato:
            stmt = stmt.where(Invoice.stato == query.stato)
        if query.anno:
            stmt = stmt.where(Invoice.anno == query.anno)
        if query.stato_pagamento == "da_incassare":
            # «Da incassare» is the dashboard's word, so it is the dashboard's predicate
            # (`_receivable_filter`, §5.2): an issued fattura nobody has paid. A plain
            # equality on the column listed every draft and every proforma as unpaid,
            # since a row is born `da_incassare` (REB-325). `incassato` below stays an
            # equality: `set_payment_state` refuses any row that is not an issued fattura
            # and the table's check constraint agrees, so the two spellings coincide.
            stmt = stmt.where(*_receivable_filter())
        elif query.stato_pagamento:
            stmt = stmt.where(Invoice.stato_pagamento == query.stato_pagamento)
        if query.origine_proforma_id:
            stmt = stmt.where(Invoice.origine_proforma_id == query.origine_proforma_id)
        if query.escludi_consumate:
            stmt = stmt.where(Invoice.stato != "consumata")
        if query.scadute:
            # The drill-through of §6.2's "scaduto e non incassato" card, sharing its
            # predicate literally rather than restating it -- see `_overdue_predicate`,
            # which `count_scadute_non_incassate` also calls. An **additional** predicate on
            # the statement built above, never a replacement for it: a drill-through that
            # silently dropped the caller's own `stato` or `customer_id` would be answering
            # a different question from the one asked.
            stmt = stmt.where(*_overdue_predicate())
        if query.cursor:
            stmt = stmt.where(Invoice.id < query.cursor)
        # Keyset pagination on a UUIDv7 id: ordered by creation, stable under inserts.
        # Newest first since 2026-09-09 (Ivan's request): a register is read from the
        # last number back, and the row a person has just created or issued is the one
        # they came to look at. The cursor walks the same way, so a page never repeats
        # a row and a fresh insert lands before the first page, not inside a later one.
        # No caller-supplied sort -- R9 is open and this adds no half-feature.
        return list(
            self.session.execute(stmt.order_by(Invoice.id.desc()).limit(query.limit + 1)).scalars()
        )
