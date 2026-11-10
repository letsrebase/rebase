"""The forecasts of money in (spec 2026-11-09, due items, § 1.2, the first three rows), and of
the recurring expenses going out (the fourth, REB-1022, `refresh_recurring_expense`: one forecast
out per due date of a `RecurringExpense` rule, replaced by the supplier's purchase of that
period).

A forecast is a **stored** due item in the state `forecast`: nothing says what is about to be
invoiced until the contract, its worked days and the drafts are read as due items too. Three
sources, each with its rule (`source_rule`):

* `contract_recurring_fee`: one forecast for each occurrence of a `fixed_recurring` rate card
  of an **active** contract, dated on the occurrence plus the customer's terms. Replaced by
  the invoice (draft, proforma or issued) whose line carries that rate card and a period that
  contains the occurrence.
* `contract_worked_days`: one forecast for each billing period of a contract that has `worked`
  days on no invoice line, priced as the draft's lines would be (`work_units/gathering.py`) and
  dated on the contract's next billing date plus the customer's terms. Replaced by the draft
  line the days are bound to.
* `draft_invoice`: one forecast for each draft invoice and each draft or confirmed proforma, as
  `CashOverview` already counts drafts. Replaced by the issued invoice.

**Replaced, never added to.** A forecast is never summed with the document that replaces it:
the contract side reads which periods an invoice line already bills (`InvoiceLine.rate_card_id`
and `period_start`, written when the line is created from a contract), and issuing, creating,
editing or deleting a draft calls this service in the same transaction, so a draft's own
forecast and the contract forecast it covers are never live together. The daily job and the
hooks on contracts, rate cards and work units materialise the rest.

**One item whatever the race.** `forecast_key` is a uuid5 of the source kind, the rate card or
contract and the period; a partial unique index lets one live forecast hold it. Every writer
that decides on a contract's forecasts first locks the contract row, and the issue, which locks
the invoice first and the contract second, queues behind the job (the job locks the contract
and never an invoice meanwhile), so a job and an issue that meet end with one item.

**What is dated how.** A forecast's due date is the one its invoice will carry:
`due_date_for_customer`, the arithmetic `InvoiceContext.due_date_from_terms` calls (the
customer's days, else the profile's `due_days`), never `Contract.payment_days`, which nothing
reads. A forecast whose date has passed with no invoice is dropped (the existing unbilled
reminders say so). Only active contracts are forecast; the renewal half of
`contracts/projection.py` is kept apart on purpose and is not used, and deals are not forecast.

The amount is the invoice's gross total, computed by the same regime as the draft. Contracts
and drafts in another currency than the invoice currency, and legal entities with no fiscal
profile, are not forecast: no invoice could be issued for them.

`refresh_contract` and `sync_invoice` flush and never commit, like the writer's half of
`DueItemService`; `refresh_all` is the daily job's and commits once for each contract and
each draft, so no lock is held for long.
"""

import datetime as dt
from collections import defaultdict
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import partial
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.cadence import period_end, period_months, period_start
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.contracts.projection import (
    PERIOD_STEP_MONTHS,
    RECURRING_FEE_TYPE,
    generate_recurring_fee_occurrences,
)
from pigrocrm.core.contracts.repository import RateCardRepository
from pigrocrm.core.db.clock import add_months, stepped_dates, today_local
from pigrocrm.core.due_items.models import CADENCE_MONTHS, DueItem, RecurringExpense
from pigrocrm.core.due_items.repository import DueItemRepository
from pigrocrm.core.due_items.schemas import Direction, DueItemCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.fiscal.regime import RegimeStrategy, resolve_regime
from pigrocrm.core.fiscal.schemas import FiscalSnapshot
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.due_date import due_date_for_customer
from pigrocrm.core.invoices.lines import compute_lines
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.invoices.schemas import CURRENCY, InvoiceLineIn
from pigrocrm.core.invoices.totals import build_recap, sum_totals
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.purchases.models import Purchase
from pigrocrm.core.work_units.gathering import gather, line_in, ordered, quantity_of

CONTRACT_OCCURRENCE = "contract_occurrence"
SALES_DRAFT = "sales_draft"

RULE_RECURRING_FEE = "contract_recurring_fee"
RULE_WORKED_DAYS = "contract_worked_days"
RULE_DRAFT_INVOICE = "draft_invoice"
RULE_RECURRING_EXPENSE = "recurring_expense"
RECURRING_EXPENSE = "recurring_expense"
CONTRACT_RULES: frozenset[str] = frozenset({RULE_RECURRING_FEE, RULE_WORKED_DAYS})

# How far ahead the occurrences of a fee are materialised: wider than the thirteen weeks the
# schedule draws, so the first week past it is already there when the window moves.
HORIZON_DAYS = 180

# Fixed once: a key changes if this does, and every live forecast would be dropped and born
# again under a new one.
KEY_NAMESPACE = UUID("6f0c2d58-3b8e-5a1c-9d47-2e7a41b0c9f3")

REPLACED = "sostituita da un documento"
NO_LONGER_FORECAST = "non piu' prevista"

# The statuses of an invoice that bills what its lines say: a draft and a confirmed proforma
# will, an issued invoice does. A cancelled one frees its periods again, and a consumed
# proforma bills them through the invoice it became.
BILLING_STATUSES = ("draft", "confirmed", "issued")
DRAFT_STATUSES = ("draft", "confirmed")
ACTIVE = "active"


@dataclass(frozen=True)
class ForecastReport:
    created: int = 0
    revised: int = 0
    dropped: int = 0
    failed: int = 0

    def __add__(self, other: "ForecastReport") -> "ForecastReport":
        return ForecastReport(
            self.created + other.created,
            self.revised + other.revised,
            self.dropped + other.dropped,
            self.failed + other.failed,
        )


@dataclass(frozen=True)
class _Want:
    """One forecast the rules produce now."""

    key: UUID
    rule: str
    amount: Decimal
    own_date: date | None
    description: str
    organization_id: UUID
    direction: Direction = "in"


class _Terms:
    """`InvoiceRepository.payment_terms`, remembered for one refresh: a card with years of
    occurrences asks the same customer's terms for each of them."""

    def __init__(self, repo: InvoiceRepository) -> None:
        self.repo = repo
        self.known: dict[UUID, tuple[int | None, bool]] = {}

    def payment_terms(self, customer_ids: Collection[UUID]) -> dict[UUID, tuple[int | None, bool]]:
        missing = {customer_id for customer_id in customer_ids if customer_id not in self.known}
        if missing:
            self.known.update({customer_id: (None, False) for customer_id in missing})
            self.known.update(self.repo.payment_terms(missing))
        return {customer_id: self.known[customer_id] for customer_id in customer_ids}


def forecast_key(*parts: object) -> UUID:
    """The identity of a forecast: a uuid5 of its source kind, the rate card or contract and the
    period (spec § 1.2)."""
    return uuid5(KEY_NAMESPACE, ":".join(str(part) for part in parts))


class ForecastService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.items = DueItemService(session)
        self.repo = DueItemRepository(session)
        self.invoices = InvoiceRepository(session)
        self.terms = _Terms(self.invoices)

    # ---- the daily job ----------------------------------------------------------------------

    def refresh_all(self, *, today: date | None = None, commit: bool = True) -> ForecastReport:
        """Every contract that is active or still has a forecast, then every draft that is live
        or still has one. Commits after each, so one that fails leaves the others' work: a
        contract or a draft that cannot be read is counted in `failed` and the pass goes on."""
        day = today if today is not None else today_local()
        report = ForecastReport()
        contract_ids = set(
            self.session.scalars(
                select(Contract.id).where(Contract.status == ACTIVE, Contract.deleted_at.is_(None))
            )
        ) | set(self.repo.source_ids_with_live_forecasts(CONTRACT_OCCURRENCE))
        for contract_id in sorted(contract_ids, key=str):
            report += self._isolated(partial(self.refresh_contract, contract_id, today=day))
            if commit:
                self.session.commit()
        invoice_ids = set(
            self.session.scalars(
                select(Invoice.id).where(
                    Invoice.status.in_(DRAFT_STATUSES), Invoice.deleted_at.is_(None)
                )
            )
        ) | set(self.repo.source_ids_with_live_forecasts(SALES_DRAFT))
        for invoice_id in sorted(invoice_ids, key=str):
            report += self._isolated(partial(self._lock_and_sync_draft, invoice_id, day))
            if commit:
                self.session.commit()
        # The recurring expenses: money going out, from a rule of due items' own (§ 1.2).
        rule_ids = set(
            self.session.scalars(
                select(RecurringExpense.id).where(RecurringExpense.stopped_on.is_(None))
            )
        ) | set(self.repo.source_ids_with_live_forecasts(RECURRING_EXPENSE))
        for rule_id in sorted(rule_ids, key=str):
            report += self._isolated(partial(self.refresh_recurring_expense, rule_id, today=day))
            if commit:
                self.session.commit()
        return report

    def _lock_and_sync_draft(self, invoice_id: UUID, today: date) -> ForecastReport:
        # The invoice row first, like every writer of an invoice (`require_locked`).
        self.session.execute(
            select(Invoice.id).where(Invoice.id == invoice_id).with_for_update()
        ).first()
        self.terms = _Terms(self.invoices)
        return self._sync_draft(invoice_id, today)

    def _isolated(self, step: Callable[[], ForecastReport]) -> ForecastReport:
        """One step of the job in its own SAVEPOINT: a failure undoes that step alone and is
        counted, so a contract nobody can price does not keep every later one from its
        forecast."""
        try:
            with self.session.begin_nested():
                return step()
        except Exception:  # noqa: BLE001 - one contract must not stop the others
            return ForecastReport(failed=1)

    # ---- contracts --------------------------------------------------------------------------

    def refresh_contract(self, contract_id: UUID, *, today: date | None = None) -> ForecastReport:
        """Brings the live forecasts of one contract to what its rules produce now: the fee
        occurrences of its rate cards, and its worked days on no line. Called by the daily job
        and by every change to the contract, its rate cards or its days, in that change's own
        transaction. The contract row is locked first, so a job and an issue serialise."""
        day = today if today is not None else today_local()
        self.terms = _Terms(self.invoices)
        contract = self.session.execute(
            select(Contract).where(Contract.id == contract_id).with_for_update(),
            execution_options={"populate_existing": True},
        ).scalar_one_or_none()
        wants: dict[UUID, _Want] = {}
        replaced: set[UUID] = set()
        if contract is not None:
            wants, replaced = self._contract_wants(contract, day)
        live = [
            item
            for item in self.repo.live_forecasts(CONTRACT_OCCURRENCE, contract_id)
            if item.source_rule in CONTRACT_RULES
        ]
        legal_entity_id = contract.legal_entity_id if contract is not None else None
        return self._apply(
            live,
            wants,
            replaced,
            source_kind=CONTRACT_OCCURRENCE,
            source_id=contract_id,
            legal_entity_id=legal_entity_id,
        )

    def _contract_wants(
        self, contract: Contract, today: date
    ) -> tuple[dict[UUID, _Want], set[UUID]]:
        if (
            contract.deleted_at is not None
            or contract.status != ACTIVE
            or contract.currency != CURRENCY
        ):
            return {}, set()
        pricing = self._pricing(contract.legal_entity_id, contract.customer_id)
        if pricing is None:
            return {}, set()
        profile, strategy, customer = pricing
        wants: dict[UUID, _Want] = {}
        replaced: set[UUID] = set()
        self._fee_wants(contract, profile, strategy, customer, today, wants, replaced)
        self._day_wants(contract, profile, strategy, customer, today, wants)
        return wants, replaced

    def _fee_wants(
        self,
        contract: Contract,
        profile: FiscalSnapshot,
        strategy: RegimeStrategy,
        customer: Organization,
        today: date,
        wants: dict[UUID, _Want],
        replaced: set[UUID],
    ) -> None:
        cards = [
            card
            for card in RateCardRepository(self.session).list_for_contract(contract.id)
            if card.type == RECURRING_FEE_TYPE and card.delivery_period is not None
        ]
        if not cards:
            return
        billed = self._billed_periods([card.id for card in cards])
        horizon = today + dt.timedelta(days=HORIZON_DAYS)
        for card in cards:
            for occurrence_date, following in occurrence_spans(contract, card, horizon):
                key = forecast_key(CONTRACT_OCCURRENCE, card.id, occurrence_date)
                if period_covered(occurrence_date, following, billed.get(card.id, [])):
                    replaced.add(key)
                    continue
                due = self._due_on(occurrence_date, customer.id, profile)
                if due < today:
                    continue
                total = self._total(
                    [
                        InvoiceLineIn(
                            description=contract.title[:200],
                            unit_price=card.amount,
                            rate_card_id=card.id,
                            period_start=occurrence_date,
                        )
                    ],
                    profile,
                    strategy,
                    customer,
                )
                if total is None:
                    continue
                wants[key] = _Want(
                    key,
                    RULE_RECURRING_FEE,
                    total,
                    due,
                    f"Canone {contract.title} del {occurrence_date.strftime('%d/%m/%Y')}",
                    customer.id,
                )

    def _day_wants(
        self,
        contract: Contract,
        profile: FiscalSnapshot,
        strategy: RegimeStrategy,
        customer: Organization,
        today: date,
        wants: dict[UUID, _Want],
    ) -> None:
        groups, _uncovered, cards = gather(self.session, contract.id, None, None)
        if not groups:
            return
        months = period_months(contract.billing_cadence)
        # One forecast for each billing period, with the lines its days become on a draft.
        periods: dict[date, list[InvoiceLineIn]] = defaultdict(list)
        ends: dict[date, date | None] = {}
        for (card_id, year, month), members in ordered(groups):
            first = date(year, month, 1)
            if months is None:
                # Billed on completion: one period, the contract's, dated on its end.
                start, end = contract.start, contract.end
            else:
                start, end = period_start(months, first), period_end(months, first)
            periods[start].append(line_in(cards[card_id], year, month, quantity_of(members)))
            ends[start] = end
        for start, lines in periods.items():
            end = ends[start]
            total = self._total(lines, profile, strategy, customer)
            if total is None:
                continue
            # Nothing is billed before today, and a contract with no end has no date to bill on:
            # its forecast is undated («senza scadenza»), never a date made up.
            due = self._due_on(max(end, today), customer.id, profile) if end is not None else None
            key = forecast_key(CONTRACT_OCCURRENCE, contract.id, start)
            wants[key] = _Want(
                key,
                RULE_WORKED_DAYS,
                total,
                due,
                f"Giornate lavorate da fatturare: {contract.title}",
                customer.id,
            )

    def _billed_periods(self, card_ids: list[UUID]) -> dict[UUID, list[date]]:
        """The periods the lines of draft, confirmed and issued invoices already bill, by rate
        card."""
        rows = self.session.execute(
            select(InvoiceLine.rate_card_id, InvoiceLine.period_start)
            .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .where(
                InvoiceLine.rate_card_id.in_(card_ids),
                Invoice.deleted_at.is_(None),
                Invoice.status.in_(BILLING_STATUSES),
            )
        )
        billed: dict[UUID, list[date]] = defaultdict(list)
        for card_id, period in rows:
            if card_id is not None and period is not None:
                billed[card_id].append(period)
        return billed

    # ---- drafts and proformas ---------------------------------------------------------------

    def billed_contracts(self, invoice_id: UUID) -> set[UUID]:
        """The contracts whose rate cards the lines of an invoice bill. A writer that replaces
        the lines reads this first and hands it to `sync_invoice`, since the lines it leaves
        behind no longer say which contracts they released."""
        return set(
            self.session.scalars(
                select(RateCard.contract_id)
                .join(InvoiceLine, InvoiceLine.rate_card_id == RateCard.id)
                .where(InvoiceLine.invoice_id == invoice_id)
            )
        )

    def lock_contracts(self, contract_ids: Collection[UUID]) -> None:
        """Takes the contract rows, in id order. Every writer of a worked day takes its contract
        before the day (and an invoice before its contracts), so a draft that binds days and the
        transition of one of them queue instead of waiting for each other."""
        for contract_id in sorted(contract_ids, key=str):
            self.session.execute(
                select(Contract.id).where(Contract.id == contract_id).with_for_update()
            ).first()

    def sync_invoice(
        self,
        invoice_id: UUID,
        *,
        also_contracts: Collection[UUID] = (),
        today: date | None = None,
    ) -> ForecastReport:
        """The forecast of one draft or proforma, and of the contracts its lines bill: called
        in the transaction that creates, edits, confirms, deletes, issues or annuls it, with the
        invoice row locked by the caller. A document that is no longer a draft has no forecast
        of its own; the contracts it bills are read again, because its lines now bill (or no
        longer bill) their periods and its days are bound (or freed). `also_contracts` are the
        ones its lines billed before they were replaced."""
        day = today if today is not None else today_local()
        self.terms = _Terms(self.invoices)
        report = self._sync_draft(invoice_id, day)
        contract_ids = self.billed_contracts(invoice_id) | set(also_contracts)
        for contract_id in sorted(contract_ids, key=str):
            report += self.refresh_contract(contract_id, today=day)
        return report

    def _sync_draft(self, invoice_id: UUID, today: date) -> ForecastReport:
        invoice = self.session.execute(
            select(Invoice).where(Invoice.id == invoice_id),
            execution_options={"populate_existing": True},
        ).scalar_one_or_none()
        wants: dict[UUID, _Want] = {}
        if invoice is not None:
            want = self._draft_want(invoice, today)
            if want is not None:
                wants[want.key] = want
        live = [
            item
            for item in self.repo.live_forecasts(SALES_DRAFT, invoice_id)
            if item.source_rule == RULE_DRAFT_INVOICE
        ]
        replaced = (
            set()
            if invoice is None or invoice.status in DRAFT_STATUSES
            else {item.forecast_key for item in live if item.forecast_key is not None}
        )
        return self._apply(
            live,
            wants,
            replaced,
            source_kind=SALES_DRAFT,
            source_id=invoice_id,
            legal_entity_id=invoice.legal_entity_id if invoice is not None else None,
        )

    def _draft_want(self, invoice: Invoice, today: date) -> _Want | None:
        if (
            invoice.deleted_at is not None
            or invoice.status not in DRAFT_STATUSES
            or invoice.currency != CURRENCY
            or invoice.total <= 0
        ):
            return None
        pricing = self._pricing(invoice.legal_entity_id, invoice.customer_id)
        if pricing is None:
            return None
        profile, _strategy, customer = pricing
        # The date `issue` would print: the one written on the draft, else the customer's terms
        # from today (`InvoiceContext.expected_due_date`).
        due = invoice.due_date or self._due_on(today, customer.id, profile)
        if due < today:
            return None
        proforma = invoice.type == "proforma"
        return _Want(
            forecast_key(SALES_DRAFT, invoice.id),
            RULE_DRAFT_INVOICE,
            invoice.total,
            due,
            ("Proforma" if proforma else "Bozza di fattura")
            + (f" {invoice.reference}" if invoice.reference else ""),
            customer.id,
        )

    # ---- recurring expenses -----------------------------------------------------------------

    def refresh_recurring_expense(
        self, rule_id: UUID, *, today: date | None = None
    ) -> ForecastReport:
        """Brings the live forecasts of one recurrence rule to what it produces now: one forecast
        out for each due date inside the horizon that no purchase of its period has replaced.
        Called by the daily job, by the rule's own writes and by every purchase of its supplier,
        in that change's transaction; the rule row is locked first, so they serialise."""
        day = today if today is not None else today_local()
        rule = self.session.execute(
            select(RecurringExpense).where(RecurringExpense.id == rule_id).with_for_update(),
            execution_options={"populate_existing": True},
        ).scalar_one_or_none()
        wants: dict[UUID, _Want] = {}
        replaced: set[UUID] = set()
        if rule is not None:
            wants, replaced = self._recurring_wants(rule, day)
        return self._apply(
            self.repo.live_forecasts(RECURRING_EXPENSE, rule_id),
            wants,
            replaced,
            source_kind=RECURRING_EXPENSE,
            source_id=rule_id,
            legal_entity_id=rule.legal_entity_id if rule is not None else None,
        )

    def _recurring_wants(
        self, rule: RecurringExpense, today: date
    ) -> tuple[dict[UUID, _Want], set[UUID]]:
        if rule.stopped_on is not None:
            return {}, set()
        legal_entity = self.session.get(LegalEntity, rule.legal_entity_id)
        supplier = self.session.get(Organization, rule.supplier_id)
        if (
            legal_entity is None
            or not legal_entity.active
            or supplier is None
            or supplier.deleted_at is not None
        ):
            return {}, set()
        months = CADENCE_MONTHS[rule.cadence]
        last = today + dt.timedelta(days=HORIZON_DAYS)
        if rule.ends_on is not None:
            last = min(last, rule.ends_on)
        bought = self._purchase_dates(rule)
        wants: dict[UUID, _Want] = {}
        replaced: set[UUID] = set()
        for occurrence in stepped_dates(rule.first_due_date, last, months):
            key = forecast_key(RECURRING_EXPENSE, rule.id, occurrence)
            # The purchase of the period: the calendar month, bimonth, quarter, half-year or
            # year the due date falls in, as a contract's billing periods are cut.
            start, end = period_start(months, occurrence), period_end(months, occurrence)
            if any(start <= day <= end for day in bought):
                replaced.add(key)
            elif occurrence >= today:
                wants[key] = _Want(
                    key,
                    RULE_RECURRING_EXPENSE,
                    rule.amount,
                    occurrence,
                    f"{rule.description}, scadenza {occurrence.strftime('%d/%m/%Y')}",
                    rule.supplier_id,
                    direction="out",
                )
        return wants, replaced

    def _purchase_dates(self, rule: RecurringExpense) -> list[date]:
        """The dates of the registered purchases that can replace this rule's forecasts: the
        supplier's own, in euro or not, of the rule's company (or shared across them), leaving
        out a cancelled or archived one and a credit note or refund, which pay nothing."""
        return list(
            self.session.scalars(
                select(Purchase.date).where(
                    Purchase.supplier_id == rule.supplier_id,
                    Purchase.status == "registered",
                    Purchase.deleted_at.is_(None),
                    Purchase.amount > 0,
                    (Purchase.legal_entity_id == rule.legal_entity_id)
                    | Purchase.legal_entity_id.is_(None),
                )
            )
        )

    def refresh_supplier_rules(self, supplier_id: UUID, *, today: date | None = None) -> None:
        """Every rule of a supplier read again: a purchase of that supplier was registered,
        changed, cancelled or archived, and with it the periods it covers."""
        rule_ids = self.session.scalars(
            select(RecurringExpense.id).where(
                RecurringExpense.supplier_id == supplier_id, RecurringExpense.stopped_on.is_(None)
            )
        ).all()
        for rule_id in sorted(rule_ids, key=str):
            self.refresh_recurring_expense(rule_id, today=today)

    # ---- the shared half --------------------------------------------------------------------

    def _pricing(
        self, legal_entity_id: UUID, customer_id: UUID
    ) -> tuple[FiscalSnapshot, RegimeStrategy, Organization] | None:
        """What the amount and the date of a forecast need, or `None` when no invoice could be
        issued: the legal entity is switched off or has no fiscal profile, or the customer is
        archived."""
        legal_entity = self.session.get(LegalEntity, legal_entity_id)
        customer = self.session.get(Organization, customer_id)
        if (
            legal_entity is None
            or not legal_entity.active
            or customer is None
            or customer.deleted_at is not None
        ):
            return None
        try:
            profile = FiscalProfileService(self.session).snapshot(legal_entity_id)
            strategy = resolve_regime(profile.regime_code, profile.pack_id)
        except (NotFound, ValidationFailed):
            return None
        return profile, strategy, customer

    def _due_on(self, issue_date: date, customer_id: UUID, profile: FiscalSnapshot) -> date:
        return due_date_for_customer(self.terms, issue_date, customer_id, profile.due_days)

    def _total(
        self,
        lines: list[InvoiceLineIn],
        profile: FiscalSnapshot,
        strategy: RegimeStrategy,
        customer: Organization,
    ) -> Decimal | None:
        """The total the invoice for these lines would carry, or `None` when no invoice could
        be written for them (the regime refuses a line) or it owes nothing."""
        try:
            computed = compute_lines(lines, profile, strategy, country_customer=customer.country)
        except ValidationFailed:
            return None
        total = sum_totals(build_recap(computed))[2]
        return total if total > 0 else None

    def _apply(
        self,
        live: list[DueItem],
        wants: dict[UUID, _Want],
        replaced: set[UUID],
        *,
        source_kind: str,
        source_id: UUID,
        legal_entity_id: UUID | None,
    ) -> ForecastReport:
        """Drops what is no longer forecast, revises what changed and creates what is new."""
        by_key = {item.forecast_key: item for item in live}
        created = revised = dropped = 0
        for key, item in by_key.items():
            if key in wants:
                continue
            self.items.drop(item.id, REPLACED if key in replaced else NO_LONGER_FORECAST)
            dropped += 1
        for key, want in wants.items():
            existing = by_key.get(key)
            if existing is None:
                assert legal_entity_id is not None  # a want comes from a row that has one
                if self._create(want, source_kind, source_id, legal_entity_id):
                    created += 1
            else:
                before = (
                    existing.amount,
                    existing.own_date,
                    existing.description,
                    existing.organization_id,
                )
                self.items.revise_forecast(
                    existing.id,
                    amount=want.amount,
                    own_date=want.own_date,
                    description=want.description,
                    organization_id=want.organization_id,
                )
                revised += before != (
                    existing.amount,
                    existing.own_date,
                    existing.description,
                    existing.organization_id,
                )
        return ForecastReport(created, revised, dropped)

    def _create(
        self, want: _Want, source_kind: str, source_id: UUID, legal_entity_id: UUID
    ) -> bool:
        """The forecast, unless another writer holds the key: the unique index is the net
        under the contract's lock, and its loser is the same answer, not an error."""
        try:
            with self.session.begin_nested():
                self.items.create(
                    DueItemCreate(
                        legal_entity_id=legal_entity_id,
                        direction=want.direction,
                        organization_id=want.organization_id,
                        amount=want.amount,
                        description=want.description[:255],
                        state="forecast",
                        source_kind=source_kind,
                        source_id=source_id,
                        source_rule=want.rule,
                        forecast_key=want.key,
                        own_date=want.own_date,
                    ),
                    check_organization=False,
                )
        except IntegrityError:
            return False
        return True


def occurrence_spans(
    contract: Contract, card: RateCard, through: date
) -> list[tuple[date, date | None]]:
    """The occurrences of a recurring fee card up to `through`, each with the day the next one
    falls on: the span an occurrence bills runs to there, and a one-off has no next. The one
    place the span is cut, for the forecasts and for the action that bills an occurrence."""
    return [
        (
            occurrence.date,
            add_months(card.valid_from, (index + 1) * PERIOD_STEP_MONTHS[card.delivery_period])
            if card.delivery_period in PERIOD_STEP_MONTHS
            else None,
        )
        for index, occurrence in enumerate(
            generate_recurring_fee_occurrences(contract, [card], through=through)
        )
    ]


def period_covered(occurrence: date, following: date | None, billed: list[date]) -> bool:
    """An invoice line of the card bills this occurrence when its period starts in the span that
    runs from the occurrence to the next one; a one-off has no next, so any later period."""
    return any(
        period >= occurrence and (following is None or period < following) for period in billed
    )


def refresh_forecasts(
    session: Session, *, today: date | None = None, dry_run: bool = False
) -> ForecastReport:
    """The daily job (`pigrocrm automations`): every forecast brought to what the rules produce
    today. `dry_run` does the same reads and writes inside a SAVEPOINT and rolls it back, so a
    rehearsal's counters are the real ones and it leaves nothing behind."""
    service = ForecastService(session)
    if not dry_run:
        return service.refresh_all(today=today)
    rehearsal = session.begin_nested()
    try:
        return service.refresh_all(today=today, commit=False)
    finally:
        rehearsal.rollback()


__all__ = [
    "CONTRACT_OCCURRENCE",
    "HORIZON_DAYS",
    "RECURRING_EXPENSE",
    "SALES_DRAFT",
    "ForecastReport",
    "ForecastService",
    "forecast_key",
    "occurrence_spans",
    "period_covered",
    "refresh_forecasts",
]
