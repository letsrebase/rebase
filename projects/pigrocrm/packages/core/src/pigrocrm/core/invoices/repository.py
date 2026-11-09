"""Queries only. A repository never commits (project rule): every method reads or
flushes, and the surrounding `InvoiceService` method is the one transaction.

From slice 6 this file also owns the invoice-side *aggregates* the dashboards read. They
live here rather than on `InvoiceService` for the reason §3 rule 2 gives -- a single-table
`COUNT` or `SUM` belongs to the repository of that table, even when the dashboard asking
for it belongs to another slice -- and for a concrete second reason: slice 3 §11 fixes its
MCP exclusion list at exactly four names, so a new public method on `InvoiceService` would
force either a new tool or an edit to another slice's declared list.
"""

from collections.abc import Collection, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import ColumnElement, Row, case, delete, distinct, func, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.deals.models import Deal
from pigrocrm.core.due_items.models import DueItem, DueItemSettlement
from pigrocrm.core.due_items.sales import join_sales_item, remaining_amount
from pigrocrm.core.gmail.models import PaymentReminder
from pigrocrm.core.invoices.models import (
    PROFORMA_SEQUENCE_NAME,
    Invoice,
    InvoiceCounter,
    InvoiceLine,
    InvoiceRegisterGap,
)
from pigrocrm.core.invoices.naming import number_complete
from pigrocrm.core.invoices.receivables import (
    BANDS_RECEIVABLES,
    BandRow,
    MonthRow,
    OpenReceivable,
    OrganizationRow,
    OverdueRow,
    Payment,
    _issued_filter,
    _legal_entity_filter,
    _overdue_predicate,
    _receivable_filter,
    invoiced_not_won_predicate,
)
from pigrocrm.core.invoices.schemas import InvoiceListQuery
from pigrocrm.core.money import round_money, share
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.pipeline.models import PipelineStage


def _open_receivables(
    rows: Sequence[Row[Invoice, Decimal, date | None]],
) -> list[OpenReceivable]:
    """The statements behind it all `WHERE` a due date, so it is never `None` here."""
    return [
        OpenReceivable(invoice, Decimal(remaining), cast(date, due))
        for invoice, remaining, due in rows
    ]


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

    def get_for_update(self, invoice_id: UUID, *, include_deleted: bool = False) -> Invoice | None:
        """`get`, with the row locked until this transaction ends and re-read from the
        database whatever the identity map held.

        `populate_existing` is the half that matters: a plain `SELECT ... FOR UPDATE`
        returns the already-loaded object untouched, so the state check that follows
        would still run against a read taken before the lock and miss the commit the lock
        just waited for. Lock order is counter first, then the row (see `lock_counter`):
        only `issue` takes both, and it takes them in that order.
        """
        stmt = select(Invoice).where(Invoice.id == invoice_id).with_for_update()
        invoice = self.session.execute(
            stmt.execution_options(populate_existing=True)
        ).scalar_one_or_none()
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
            .order_by(InvoiceLine.line_number)
        )
        return list(self.session.execute(stmt).scalars())

    def clear_lines(self, invoice_id: UUID) -> None:
        """A real `DELETE`, then a fresh insert of the whole list.

        Bulk replacement rather than a per-line diff, for the reason spec 11 gives: it
        is the natural shape of a line editor. It was also, until task 4B-1 closed A14,
        the only way to clear an optional numeric column at all -- there was no spelling
        of `InvoiceLineIn` that meant "set `discount_amount` back to nothing". That is no
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

    def lock_counter(self, legal_entity_id: UUID, year: int) -> InvoiceCounter:
        """The legal entity's counter row for the year, locked for the rest of this
        transaction.

        Two statements, in this order and for these reasons:

        1. `INSERT ... ON CONFLICT (legal_entity_id, year) DO NOTHING` -- two concurrent
           first-invoices-of-the-year: one inserts, the other does nothing, both carry
           on. Without `ON CONFLICT` the loser would take a `UniqueViolation` and have
           to be retried by the caller.
        2. `SELECT ... FOR UPDATE` -- from here on every other emission for the same
           legal entity and year waits, and no other legal entity's does (REB-619): the row is
           the lock, and there is one per register. This is deliberately **the first
           row lock the emission transaction takes**, and no later statement in that
           transaction takes a lock a concurrent emission could already hold, so two
           emissions cannot deadlock against each other.

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
                "INSERT INTO invoice_counters (legal_entity_id, year, last_number) "
                "VALUES (:legal_entity_id, :year, 0) ON CONFLICT (legal_entity_id, year) DO NOTHING"
            ),
            {"legal_entity_id": legal_entity_id, "year": year},
        )
        stmt = (
            select(InvoiceCounter)
            .where(InvoiceCounter.legal_entity_id == legal_entity_id, InvoiceCounter.year == year)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return self.session.execute(stmt).scalars().one()

    def last_issued_date(self, legal_entity_id: UUID, year: int) -> date | None:
        """The `issue_date` of the legal entity's highest-numbered invoice of `year`.

        Safe to read only *after* `lock_counter` has run, which is the one place it is
        called from: without the lock, a concurrent emission could commit a later
        number between this read and the write that depends on it. Includes
        `cancelled` rows on purpose -- an annulled invoice keeps its number and its
        place in the chronological order, which is what makes the register monotonic
        rather than merely gap-free.
        """
        stmt = (
            select(Invoice.issue_date)
            .where(*self._register(legal_entity_id, year))
            .order_by(Invoice.number.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    @staticmethod
    def _register(legal_entity_id: UUID, year: int) -> tuple[ColumnElement[bool], ...]:
        """The numbered rows of one register: one legal entity, one year (REB-619). Every
        read below starts from this, so none can forget the legal entity and answer with
        another register's numbers."""
        return (
            Invoice.legal_entity_id == legal_entity_id,
            Invoice.year == year,
            Invoice.number.is_not(None),
        )

    # --- slice 9's import queries -------------------------------------------------

    def neighbour_dates(
        self, legal_entity_id: UUID, year: int, number: int
    ) -> tuple[date | None, date | None]:
        """The dates on either side of `number` in the legal entity's register of `year`.

        `last_issued_date` answers "what came last"; an import inserts *between*
        existing numbers, so monotonicity has to be checked against the nearest lower
        and the nearest higher number, whatever their states -- an annulled row keeps
        its place in the order exactly as it does for `last_issued_date`.
        """
        register = self._register(legal_entity_id, year)
        before = (
            select(Invoice.issue_date)
            .where(*register, Invoice.number < number)
            .order_by(Invoice.number.desc())
            .limit(1)
        )
        after = (
            select(Invoice.issue_date)
            .where(*register, Invoice.number > number)
            .order_by(Invoice.number.asc())
            .limit(1)
        )
        return (
            self.session.execute(before).scalars().first(),
            self.session.execute(after).scalars().first(),
        )

    def numbers_present(self, legal_entity_id: UUID, year: int) -> set[int]:
        stmt = select(Invoice.number).where(*self._register(legal_entity_id, year))
        # `Invoice.number` is `Mapped[int | None]` at the column-type level; the
        # `is_not(None)` clause above is what the database enforces, and this cast is
        # what tells mypy the same thing rather than re-filtering `None` at runtime.
        return cast(set[int], set(self.session.execute(stmt).scalars().all()))

    def first_native_number(self, legal_entity_id: UUID, year: int) -> int | None:
        """The lowest number this CRM itself issued for the legal entity in `year` (§3.2
        rule 6)."""
        stmt = select(func.min(Invoice.number)).where(
            *self._register(legal_entity_id, year), Invoice.imported_from.is_(None)
        )
        return self.session.execute(stmt).scalar_one()

    def declared_gaps(self, legal_entity_id: UUID, year: int) -> set[int]:
        stmt = select(InvoiceRegisterGap.number).where(
            InvoiceRegisterGap.legal_entity_id == legal_entity_id, InvoiceRegisterGap.year == year
        )
        return set(self.session.execute(stmt).scalars().all())

    def existing_by_number(self, legal_entity_id: UUID, year: int, number: int) -> Invoice | None:
        """The invoice already on the legal entity's register at `(anno, numero)`, if any
        -- the same set `numbers_present` answers, read one row at a time for
        REB-364's duplicate check, which needs the row itself (specifically its
        `xml_hash_sha256`), not merely whether the number is taken.

        Not scoped by `imported_from`: `uq_invoices_legal_entity_year_number` allows exactly
        one row per `(azienda_id, anno, numero)` regardless of provenance, so a
        re-import of a natively-issued number is exactly as much a duplicate as a
        re-import of a previously-imported one.
        """
        stmt = select(Invoice).where(
            Invoice.legal_entity_id == legal_entity_id,
            Invoice.year == year,
            Invoice.number == number,
        )
        return self.session.execute(stmt).scalars().first()

    def gaps(self, legal_entity_id: UUID, year: int) -> list[InvoiceRegisterGap]:
        stmt = (
            select(InvoiceRegisterGap)
            .where(
                InvoiceRegisterGap.legal_entity_id == legal_entity_id,
                InvoiceRegisterGap.year == year,
            )
            .order_by(InvoiceRegisterGap.number)
        )
        return list(self.session.execute(stmt).scalars().all())

    def add_gap(self, gap: InvoiceRegisterGap) -> InvoiceRegisterGap:
        self.session.add(gap)
        self.session.flush()
        return gap

    # --- slice 6's dashboard aggregates ------------------------------------------

    def sum_unpaid(self, legal_entity_id: UUID | None = None) -> Decimal:
        """What is still owed on issued, non-deleted invoices: `Σ` of their due items'
        remaining amounts (spec 2026-11-09, § 1.1), so a partly paid invoice counts for what
        is left and the six buckets, which read the same rows, add up to this figure.

        **The gross amount, not `taxable_amount`, and that is not an inconsistency with the
        revenue figure.** Revenue is `Σ imponibile` (slice 4 §7.1, and this slice introduces no
        third meaning); a receivable is what must arrive in the bank, which includes VAT --
        money collected on the State's behalf. Under the flat-rate regime regime the two
        coincide and the difference is unobservable, which is exactly why it is written
        down now: the same condition, and the same answer, as slice 4 §7.1.

        This figure enters **no** margin and never shares a total row with revenue (§5.2).

        `round_money` rather than a bare `Decimal(...)`: `coalesce(sum(...), 0)` returns the
        *integer literal* on an empty register, and `Decimal("0")` reaches the wire as `"0"`
        while every other money field reaches it as `"0.00"`.
        """
        total = self.session.execute(
            join_sales_item(select(func.coalesce(func.sum(remaining_amount()), 0))).where(
                *_receivable_filter(), *_legal_entity_filter(legal_entity_id)
            )
        ).scalar_one()
        return round_money(Decimal(total))

    def sum_expired(self, legal_entity_id: UUID | None = None) -> Decimal:
        """The subset of `sum_unpaid` past its due date.

        A **subset**, and rendered as one -- indented beneath it, never as a second addable
        line (§5.2). The predicate is literally `_receivable_filter()` plus two clauses, so
        the containment is structural rather than a property somebody has to remember.
        """
        total = self.session.execute(
            join_sales_item(select(func.coalesce(func.sum(remaining_amount()), 0))).where(
                *_overdue_predicate(), *_legal_entity_filter(legal_entity_id)
            )
        ).scalar_one()
        return round_money(Decimal(total))

    def count_issued_in_period(
        self, date_from: date, date_to: date, legal_entity_id: UUID | None = None
    ) -> int:
        """A `COUNT` on the same predicate the revenue figure uses, so the two cannot
        describe different sets -- "6 fatture emesse" beside a revenue total that included
        a seventh, or a proforma, is the shape of that defect.

        Attributed to the period by `issue_date`, its own date, exactly as revenue is
        (slice 4 §7.4).
        """
        return int(
            self.session.execute(
                select(func.count(Invoice.id)).where(
                    *_issued_filter(),
                    *_legal_entity_filter(legal_entity_id),
                    Invoice.issue_date.is_not(None),
                    Invoice.issue_date >= date_from,
                    Invoice.issue_date <= date_to,
                )
            ).scalar_one()
        )

    def list_issued_in_period(self, date_from: date, date_to: date) -> list[Invoice]:
        """Issued invoices whose own date falls in the window, oldest first: the rows the
        weekly report lists under «Emesse questa settimana» (spec 2026-09-16 §3.1, §3.2).

        Shares `_issued_filter()` with `count_issued_in_period`, whose count this list's
        length must always equal -- a report whose number and rows describe different
        sets is exactly the defect `test_count_emesse_in_periodo_counts_the_same_set_revenue_sums`
        already guards against for the count, and this list must not reopen it.
        """
        stmt = (
            select(Invoice)
            .where(
                *_issued_filter(), Invoice.issue_date >= date_from, Invoice.issue_date <= date_to
            )
            .order_by(Invoice.issue_date, Invoice.number)
        )
        return list(self.session.execute(stmt).scalars())

    def list_paid_in_period(self, date_from: date, date_to: date) -> list[Payment]:
        """The payments received in the window, one row per settlement, in settlement order:
        the rows behind «Incassate questa settimana» (spec 2026-09-16 §3.1, §3.2).

        Attributed to the settlement's own date and amount (spec 2026-11-09, § 1.1), not to
        the invoice's `payment_date` and `total` -- an invoice issued weeks ago and paid this
        week belongs here and not in `list_issued_in_period`, which is the whole reason the
        report carries the two sections separately, and an invoice paid in two instalments is
        two lines, each for what arrived.
        """
        rows = self.session.execute(
            join_sales_item(select(Invoice, DueItemSettlement.amount, DueItemSettlement.settled_on))
            .join(DueItemSettlement, DueItemSettlement.due_item_id == DueItem.id)
            .where(
                *_issued_filter(),
                DueItemSettlement.reversed_at.is_(None),
                DueItemSettlement.settled_on >= date_from,
                DueItemSettlement.settled_on <= date_to,
            )
            .order_by(DueItemSettlement.settled_on, Invoice.number, DueItemSettlement.id)
        ).all()
        return [Payment(invoice, Decimal(amount), on) for invoice, amount, on in rows]

    def list_overdue(self, today: date) -> list[OpenReceivable]:
        """Receivables due on or before `today`, worst first: the «scadute» half of §3.1's
        «Da incassare» (spec 2026-09-16).

        Not `PaymentRemindersService.candidates`, which the weekly report used to read: that list
        answers «which invoices may I legitimately chase», so it drops anything inside the
        grace period, anything chased in the last few days and anything already at the
        reminder ceiling. Those are properties of a reminder, not of a debt, and a report
        that took them for the debt left out money that is owed today.

        `today` is a parameter rather than a `today_local()` read here, unlike
        `_overdue_predicate()` above: the report resolves the space's own settings and
        therefore its own timezone, and a repository reading the platform's default would
        answer a different day for exactly the installation whose zone differs.

        `<=` and not `_overdue_predicate()`'s `<`, which is the second reason this is its
        own predicate: an invoice due today is money the report names -- it prints «scade
        oggi» -- while «scaduto» on the dashboard means strictly past its date, so on a
        Monday with a debt due that day the list holds one row more than the «scaduto e
        non incassato» signal counts, and a shared helper would have to pick one of the
        two meanings for both.

        Ascending `due_date`: the oldest debt first, which is the order somebody works
        the list in and the order `list_in_due_date` beside it already answers in.
        """
        stmt = (
            join_sales_item(select(Invoice, remaining_amount(), DueItem.due_date))
            .where(
                *_receivable_filter(),
                DueItem.due_date.is_not(None),
                DueItem.due_date <= today,
            )
            .order_by(DueItem.due_date, Invoice.number)
        )
        return _open_receivables(self.session.execute(stmt).all())

    def list_in_due_date(self, date_from: date, date_to: date) -> list[OpenReceivable]:
        """Receivables due within the window, soonest first: the «in scadenza nei prossimi
        sette giorni» half of §3.1's «Da incassare» section, once the caller passes that
        window instead of the register's whole horizon.

        `_receivable_filter()`, not `_overdue_predicate()`: a due date inside the window can
        be in the future, which `_overdue_predicate()`'s `< today_local()` clause would
        exclude. The already-overdue half of the same section comes from `sum_expired` and
        `count_overdue_not_paid` instead, which this method leaves untouched.
        """
        stmt = (
            join_sales_item(select(Invoice, remaining_amount(), DueItem.due_date))
            .where(
                *_receivable_filter(),
                DueItem.due_date.is_not(None),
                DueItem.due_date >= date_from,
                DueItem.due_date <= date_to,
            )
            .order_by(DueItem.due_date, Invoice.number)
        )
        return _open_receivables(self.session.execute(stmt).all())

    def sum_issued_in_period(self, date_from: date, date_to: date) -> Decimal:
        """`Σ totale` over `list_issued_in_period`'s own predicate: the total the weekly
        report prints beside «Emesse questa settimana», and the same call `DigestService`
        reuses unchanged for the running month and the one before it (spec 2026-09-16
        §3.2), only with a different window.
        """
        total = self.session.execute(
            select(func.coalesce(func.sum(Invoice.total), 0)).where(
                *_issued_filter(), Invoice.issue_date >= date_from, Invoice.issue_date <= date_to
            )
        ).scalar_one()
        return round_money(Decimal(total))

    def count_deals_invoiced_not_won(self, legal_entity_id: UUID | None = None) -> int:
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
                # The deal's legal entity, not the invoice's: this counts deals and its
                # drill-through (`DealRepository.list`, `?invoiced_unwon=true`)
                # narrows by `Deal.legal_entity_id`, so the card and the list must read the
                # same column or an invoice imported onto another legal entity than its
                # deal's would make them disagree (criterion 2).
                .where(
                    *invoiced_not_won_predicate(),
                    *(
                        ()
                        if legal_entity_id is None
                        else (Deal.legal_entity_id == legal_entity_id,)
                    ),
                )
            ).scalar_one()
        )

    def count_overdue_not_paid(self, legal_entity_id: UUID | None = None) -> int:
        """§6.2's fourth signal: the candidate list of slice 5 §7.1's payment reminders,
        counted. The count **sends nothing** -- and saying so here is the point, because a
        count next to a list of overdue organizations is exactly the place someone later adds a
        "send all" button.

        The same `_overdue_predicate()` `sum_expired` uses, so the count and the sum can
        never describe different rows.
        """
        return int(
            self.session.execute(
                join_sales_item(select(func.count(Invoice.id))).where(
                    *_overdue_predicate(), *_legal_entity_filter(legal_entity_id)
                )
            ).scalar_one()
        )

    def unpaid_for_customer(self, customer_id: UUID, limit: int) -> list[Invoice]:
        """One customer's issued, unpaid invoices, oldest deadline first.

        The rows behind `sum_unpaid` restricted to one customer, sharing
        `_receivable_filter()` literally rather than restating it: the total on the economic
        dashboard and the list in the `organization-status` briefing describe the same set, and a
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
                join_sales_item(select(Invoice))
                .where(*_receivable_filter(), Invoice.customer_id == customer_id)
                .order_by(DueItem.due_date.asc().nulls_last(), Invoice.id.asc())
                .limit(limit)
            ).scalars()
        )

    def ageing_receivables(self, today: date, legal_entity_id: UUID | None = None) -> list[BandRow]:
        """The receivables in the six buckets of slice 8 §2.1, each with its sum and count,
        in the page's order and always all six -- an empty bucket is a row at zero, since a
        bucket that vanishes when empty is a chart that changes shape.

        One statement: a `CASE` on `due_date` against the four bounds, grouped, over
        `_receivable_filter()` imported and not restated (§2.2 and criterion 3), so the
        six sums add up to `sum_unpaid` by construction and not by care. `today` is a
        parameter for the reason `list_overdue` gives; «scaduto» is strictly before it, as
        `_overdue_predicate` reads it, and «entro 30» starts on it.
        """
        bounds = {
            "entro_30": (today, today + timedelta(days=30)),
            "da_31_a_60": (today + timedelta(days=31), today + timedelta(days=60)),
            "da_61_a_90": (today + timedelta(days=61), today + timedelta(days=90)),
        }
        band = case(
            (DueItem.due_date.is_(None), "without_due_date"),
            (DueItem.due_date < today, "expired"),
            (DueItem.due_date <= bounds["entro_30"][1], "entro_30"),
            (DueItem.due_date <= bounds["da_31_a_60"][1], "da_31_a_60"),
            (DueItem.due_date <= bounds["da_61_a_90"][1], "da_61_a_90"),
            else_="oltre_90",
        ).label("fascia")
        rows = self.session.execute(
            join_sales_item(
                select(band, func.coalesce(func.sum(remaining_amount()), 0), func.count(Invoice.id))
            )
            .where(*_receivable_filter(), *_legal_entity_filter(legal_entity_id))
            .group_by(band)
        ).all()
        found = {row[0]: (round_money(Decimal(row[1])), int(row[2])) for row in rows}
        zero = (Decimal("0.00"), 0)
        largest = max((found.get(c, zero)[0] for c in BANDS_RECEIVABLES), default=Decimal("0"))
        out: list[BandRow] = []
        for code in BANDS_RECEIVABLES:
            amount, number = found.get(code, zero)
            date_from, date_to = bounds.get(code, (None, None))
            if code == "oltre_90":
                date_from = bounds["da_61_a_90"][1] + timedelta(days=1)
            out.append(BandRow(code, date_from, date_to, amount, number, share(amount, largest)))
        return out

    def receivables_by_due_month(self, legal_entity_id: UUID | None = None) -> list[MonthRow]:
        """What is owed, month by month of `due_date`, oldest first: the cash a person
        can expect, told by when it is due rather than by when the work was done (the
        economic charts' reading). Rows without a due date are not here: they are the sixth
        bucket of `ageing_receivables`, and a month cannot hold a date that does not exist.
        """
        month = func.date_trunc("month", DueItem.due_date).label("month")
        rows = self.session.execute(
            join_sales_item(
                select(
                    month, func.coalesce(func.sum(remaining_amount()), 0), func.count(Invoice.id)
                )
            )
            .where(
                *_receivable_filter(),
                *_legal_entity_filter(legal_entity_id),
                DueItem.due_date.is_not(None),
            )
            .group_by(month)
            .order_by(month)
        ).all()
        amounts = [round_money(Decimal(row[1])) for row in rows]
        largest = max(amounts, default=Decimal("0"))
        return [
            MonthRow(
                month=row[0].date() if hasattr(row[0], "date") else row[0],
                amount=amount,
                number=int(row[2]),
                share=share(amount, largest),
            )
            for row, amount in zip(rows, amounts, strict=True)
        ]

    def receivables_by_customer(
        self, today: date, limit: int, legal_entity_id: UUID | None = None
    ) -> list[OrganizationRow]:
        """Who owes what, largest exposure first, with the overdue part of each: the
        `unpaid_for_customer` reading turned around, over the same predicate. `today`
        splits the overdue share the way `_overdue_predicate` does, strictly before."""
        expired = func.coalesce(
            func.sum(case((DueItem.due_date < today, remaining_amount()), else_=0)), 0
        )
        total = func.coalesce(func.sum(remaining_amount()), 0)
        rows = self.session.execute(
            join_sales_item(
                select(
                    Organization.id, Organization.legal_name, total, func.count(Invoice.id), expired
                )
            )
            .join(Organization, Organization.id == Invoice.customer_id)
            .where(*_receivable_filter(), *_legal_entity_filter(legal_entity_id))
            .group_by(Organization.id, Organization.legal_name)
            .order_by(total.desc(), Organization.legal_name)
            .limit(limit)
        ).all()
        amounts = [round_money(Decimal(row[2])) for row in rows]
        largest = max(amounts, default=Decimal("0"))
        return [
            OrganizationRow(
                customer_id=row[0],
                legal_name=row[1],
                amount=amount,
                number=int(row[3]),
                expired=round_money(Decimal(row[4])),
                share=share(amount, largest),
            )
            for row, amount in zip(rows, amounts, strict=True)
        ]

    def overdue_with_reminders(
        self, today: date, limit: int, legal_entity_id: UUID | None = None
    ) -> list[OverdueRow]:
        """The overdue rows worst first, each with how many reminders actually left and
        when the last one did. Sent reminders are rows with `sent_at` (a prepared draft
        is not a letter the customer received, `PaymentReminder`'s own docstring), counted
        in a correlated subquery so the invoice row stays one row. Strictly before `today`,
        as the «scaduto» bucket and `_overdue_predicate` read it."""
        sent = (
            select(func.count(PaymentReminder.id))
            .where(PaymentReminder.invoice_id == Invoice.id, PaymentReminder.sent_at.is_not(None))
            .scalar_subquery()
        )
        last_sent = (
            select(func.max(PaymentReminder.sent_at))
            .where(PaymentReminder.invoice_id == Invoice.id, PaymentReminder.sent_at.is_not(None))
            .scalar_subquery()
        )
        rows = self.session.execute(
            join_sales_item(
                select(
                    Invoice,
                    Organization.legal_name,
                    sent,
                    last_sent,
                    remaining_amount(),
                    DueItem.due_date,
                )
            )
            .join(Organization, Organization.id == Invoice.customer_id)
            .where(
                *_receivable_filter(),
                *_legal_entity_filter(legal_entity_id),
                DueItem.due_date.is_not(None),
                DueItem.due_date < today,
            )
            .order_by(DueItem.due_date, Invoice.number)
            .limit(limit)
        ).all()
        out: list[OverdueRow] = []
        for invoice, customer, count, last, remaining, item_due_date in rows:
            due_date = cast(date, item_due_date)
            out.append(
                OverdueRow(
                    invoice_id=invoice.id,
                    number=number_complete(cast(int, invoice.year), cast(int, invoice.number)),
                    customer_id=invoice.customer_id,
                    customer=customer,
                    due_date=due_date,
                    days_of_delay=(today - due_date).days,
                    amount=round_money(Decimal(remaining)),
                    sent_payment_reminders=int(count or 0),
                    last_payment_reminder_at=last.date() if last is not None else None,
                )
            )
        return out

    def customer_names(self, customer_ids: Collection[UUID]) -> dict[UUID, str]:
        """The `legal_name` of each given customer, in one query.

        The same method, for the same reason, as `DealRepository.customer_names`: one
        statement for a whole page rather than one per row, so a 50-row invoice list costs
        two queries and never fifty-one. A label lookup for rows already chosen, run after
        the limit.

        No `deleted_at` filter, deliberately, and here it matters more than for deals: an
        issued invoice outlives the customer relationship, and `OrganizationService.soft_delete`
        may archive a customer whose invoices stay in the register. An archived customer's
        name is still the name on those invoices; filtering it out would print a dash on
        rows that have a perfectly good one.

        An empty input short-circuits: `IN ()` is a query with no possible rows.
        """
        if not customer_ids:
            return {}
        rows = self.session.execute(
            select(Organization.id, Organization.legal_name).where(
                Organization.id.in_(customer_ids)
            )
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
            select(
                Organization.id, Organization.payment_days, Organization.payment_end_of_month
            ).where(Organization.id.in_(customer_ids))
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
        if query.legal_entity_id:
            stmt = stmt.where(Invoice.legal_entity_id == query.legal_entity_id)
        if query.type:
            stmt = stmt.where(Invoice.type == query.type)
        if query.status:
            stmt = stmt.where(Invoice.status == query.status)
        if query.year:
            stmt = stmt.where(Invoice.year == query.year)
        if query.payment_status == "unpaid":
            # «Da incassare» is the dashboard's word: an issued invoice nobody has paid. A
            # plain equality on the column listed every draft and every proforma as unpaid,
            # since a row is born `unpaid` (REB-325). The filter reads the projected column
            # (spec 2026-11-09, § 1.1); `paid` below stays an equality: `set_payment_state`
            # refuses any row that is not an issued fattura and the table's check constraint
            # agrees, so the two spellings coincide.
            stmt = stmt.where(*_issued_filter(), Invoice.payment_status == "unpaid")
        elif query.payment_status:
            stmt = stmt.where(Invoice.payment_status == query.payment_status)
        if query.origin_proforma_id:
            stmt = stmt.where(Invoice.origin_proforma_id == query.origin_proforma_id)
        if query.exclude_consumed:
            stmt = stmt.where(Invoice.status != "consumed")
        if query.overdue:
            # The drill-through of §6.2's "scaduto e non incassato" card, sharing its
            # predicate literally rather than restating it -- see `_overdue_predicate`,
            # which `count_overdue_not_paid` also calls. An **additional** predicate on
            # the statement built above, never a replacement for it: a drill-through that
            # silently dropped the caller's own `status` or `customer_id` would be answering
            # a different question from the one asked.
            stmt = join_sales_item(stmt).where(*_overdue_predicate())
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
