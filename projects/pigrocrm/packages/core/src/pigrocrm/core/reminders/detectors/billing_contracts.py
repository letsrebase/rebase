"""The money that slips, part two: what a contract has not billed (spec 2026-10-26 §3.3,
REB-767): `billable_period_closed`, `fixed_fee_period_unbilled`, `on_completion_unbilled`
and `invoice_days_unlinked`. The invoice and hours types are in `billing.py`.

**What the model records, and what these types read of it.** `Contract.billing_cadence`
is free text (no value set is pinned, `contracts/models.py`), so the three periodic
cadences are the words the rate cards already use for a period (`monthly`, `quarterly`,
`annual`, any case) and **every other value is read as billing on completion**. A period
is the calendar month, quarter or year, as in Mastro (`currentPeriodStart`), not one
counted from the contract's `start`. A contract bills «from worked days» when it has days
(`work_units`) in `worked` with no invoice line: the days the invoicing step gathers
(`work_units/invoicing.py`). A contract has a «fixed fee» when it has a `fixed_recurring`
rate card. Whatever a contract's `status` is (`draft`, `active`, `terminated`, `expired`),
it counts: an ended contract still has a last period to bill, so only `deleted_at` decides.

An invoice carries no contract (it hangs on a customer and a deal), so «the contract's
invoices» means the customer's: an issued fattura of the contract's customer.

- `billable_period_closed`: measure is the days past the end of the period the contract's
  **oldest** unbilled day falls in, raised once that period has ended. Days of the current
  period are not late.
- `fixed_fee_period_unbilled`: the **oldest uncovered period**: the first closed period
  after the customer's last issued invoice (or after the contract's `start`) in which the
  contract ran and a fee card was in force on its last day. A period is covered by an
  invoice dated on or after its last day (Mastro's rule, by issue date); on a contract that
  also bills days, an invoice of days alone does not count (REB-937). The measure is the
  days since that period's last day, so a fee left unbilled for months climbs to `serious`
  and `critical`. The subject is the contract **and** that period (a uuid5 of both), stable
  until the period is covered; the entity the web opens is the contract.
- `on_completion_unbilled`: a contract billing on completion whose `end` has passed with
  something unbilled: worked days on no invoice line, and/or a one-off fee, an `one_off`
  rate card in force on `end`, that the customer has no issued invoice dated on or after
  `end` for (the fixed fee's covering rule). One reminder per contract whichever applies,
  counted from `end`, and the text names what is unbilled. A contract with no `end` has
  not completed, and Mastro's «aged open-ended» branch is not ported: the spec counts from
  the completion date.
- `invoice_days_unlinked`: an issued, still unpaid fattura of the last 90 days
  (`UNLINKED_LOOKBACK_DAYS`) none of whose lines carries a day, for a
  customer whose contract still has unbilled days no later than its issue date (an invoice
  older than every unbilled day cannot be about them). The measure is the days since it
  was issued; the ladder has one rung, `serious` at 0, so it is never urgent.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid5

from sqlalchemy import ColumnElement, exists, func, or_, select

from pigrocrm.core.contracts.models import RATE_CARD_DAY_HOUR_TYPES, Contract, RateCard
from pigrocrm.core.contracts.projection import RECURRING_FEE_TYPE
from pigrocrm.core.db.clock import month_bounds
from pigrocrm.core.due_items.sales import join_sales_item, still_owed
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.naming import number_complete
from pigrocrm.core.mail import day_short
from pigrocrm.core.reminders.catalogue import REMINDER_TYPES
from pigrocrm.core.reminders.detect import Detection, Detector, DetectorContext
from pigrocrm.core.reminders.detectors.links import LinkResolver
from pigrocrm.core.reminders.detectors.text import format_days, format_days_ago, work_units
from pigrocrm.core.work_units.models import WorkUnit

PERIOD_CLOSED = REMINDER_TYPES["billable_period_closed"]
FIXED_FEE = REMINDER_TYPES["fixed_fee_period_unbilled"]
ON_COMPLETION = REMINDER_TYPES["on_completion_unbilled"]
UNLINKED = REMINDER_TYPES["invoice_days_unlinked"]

# The months of a period, by the words a contract's cadence uses for it.
_PERIOD_MONTHS = {"monthly": 1, "quarterly": 3, "annual": 12}
_BILLABLE_DAY = "worked"
# The rate card of a fee billed once, on completion (`contracts/models.py` `RATE_CARD_TIPI`).
ONE_OFF_FEE_TYPE = "one_off"
# A contract's cadence as the periodic words read it, in SQL so each detector loads only its own.
_PERIODIC = func.lower(func.trim(Contract.billing_cadence)).in_(tuple(_PERIOD_MONTHS))

# How far back `invoice_days_unlinked` looks: an issued invoice older than this is history,
# and one still unlinked after three months is not going to be linked by being raised again.
UNLINKED_LOOKBACK_DAYS = 90


def period_months(cadence: str) -> int | None:
    """The months in one period of `cadence`, or `None` for a cadence billed on completion."""
    return _PERIOD_MONTHS.get(cadence.strip().lower())


def period_start(months: int, day: date) -> date:
    """The first day of the calendar month, quarter or year `day` falls in."""
    first = ((day.month - 1) // months) * months + 1
    return date(day.year, first, 1)


def period_end(months: int, day: date) -> date:
    """The last day of the calendar month, quarter or year `day` falls in."""
    last = ((day.month - 1) // months) * months + months
    return month_bounds(day.year, last)[1]


def _periodic(contract: Contract) -> int | None:
    return period_months(contract.billing_cadence)


def _unbilled_days() -> tuple[ColumnElement[bool], ...]:
    return (WorkUnit.status == _BILLABLE_DAY, WorkUnit.invoice_line_id.is_(None))


def _has_days(ctx: DetectorContext) -> bool:
    return bool(
        ctx.session.scalar(
            select(
                exists().where(WorkUnit.contract_id == Contract.id, Contract.deleted_at.is_(None))
            )
        )
    )


def _has_fixed_fee(ctx: DetectorContext) -> bool:
    return bool(
        ctx.session.scalar(
            select(
                exists().where(
                    RateCard.contract_id == Contract.id,
                    Contract.deleted_at.is_(None),
                    RateCard.type == RECURRING_FEE_TYPE,
                )
            )
        )
    )


def _has_completion_contract(ctx: DetectorContext) -> bool:
    return bool(
        ctx.session.scalar(select(exists().where(Contract.deleted_at.is_(None), ~_PERIODIC)))
    )


def _unbilled_by_contract(
    ctx: DetectorContext, *where: ColumnElement[bool]
) -> dict[UUID, list[tuple[date, Decimal]]]:
    """The unbilled days of the live contracts `where` selects, oldest first."""
    days: dict[UUID, list[tuple[date, Decimal]]] = defaultdict(list)
    for contract_id, day, quantity in ctx.session.execute(
        select(WorkUnit.contract_id, WorkUnit.date, WorkUnit.quantity)
        .join(Contract, Contract.id == WorkUnit.contract_id)
        .where(Contract.deleted_at.is_(None), *where, *_unbilled_days())
        .order_by(WorkUnit.date)
    ):
        days[contract_id].append((day, Decimal(quantity)))
    return days


def _live_contracts(ctx: DetectorContext, *where: ColumnElement[bool]) -> dict[UUID, Contract]:
    """The live contracts `where` selects. Each detector asks only for its own."""
    return {
        contract.id: contract
        for contract in ctx.session.scalars(
            select(Contract).where(Contract.deleted_at.is_(None), *where).order_by(Contract.id)
        )
    }


def _has_unbilled_days() -> ColumnElement[bool]:
    return exists().where(WorkUnit.contract_id == Contract.id, *_unbilled_days())


def _recipients(
    ctx: DetectorContext, resolver: LinkResolver, contract: Contract
) -> tuple[frozenset[UUID], str]:
    links = resolver.resolve(customer_id=contract.customer_id)
    name = f" · {links.customer_name}" if links.customer_name else ""
    return ctx.directory.recipients((links.customer_owner_id,), contract.legal_entity_id), name


def detect_period_closed(ctx: DetectorContext) -> list[Detection]:
    """Periodic contracts with unbilled days from a period that has ended, at least
    `bound` days ago and never fewer than one."""
    contracts = _live_contracts(ctx, _PERIODIC, _has_unbilled_days())
    unbilled = _unbilled_by_contract(ctx, _PERIODIC)
    resolver = LinkResolver(ctx.session)
    resolver.preload(organizations=(contract.customer_id for contract in contracts.values()))
    found: list[Detection] = []
    for contract_id, days in unbilled.items():
        contract = contracts[contract_id]
        months = _periodic(contract)
        assert months is not None
        current = period_start(months, ctx.today)
        closed = [(day, quantity) for day, quantity in days if day < current]
        if not closed:
            continue
        oldest = closed[0][0]
        end = period_end(months, oldest)
        late = (ctx.today - end).days
        if late < max(ctx.bound(PERIOD_CLOSED), 1):
            continue
        total = sum((quantity for _, quantity in closed), start=Decimal(0))
        recipients, customer = _recipients(ctx, resolver, contract)
        found.append(
            Detection(
                type="billable_period_closed",
                subject_id=contract.id,
                measure=float(late),
                recipients=recipients,
                title=f"Periodo chiuso da fatturare: {contract.title}",
                detail=(
                    f"{work_units(total)} di lavoro ancora da fatturare "
                    f"(la più vecchia del {day_short(oldest)}): il suo periodo è finito "
                    f"{day_short(end)}, {format_days_ago(late)}{customer}"
                ),
                entity="contract",
                entity_id=contract.id,
                legal_entity_id=contract.legal_entity_id,
                customer_id=contract.customer_id,
            )
        )
    return sorted(found, key=lambda item: (-item.measure, str(item.subject_id)))


def _next_period_end(months: int, end: date) -> date:
    return period_end(months, end + timedelta(days=1))


def _not_days_only() -> ColumnElement[bool]:
    """An invoice that is not just worked days: it has a line no work unit is bound to, or
    no line at all (an imported one, whose content cannot be shown to be days)."""
    free_line = (
        select(InvoiceLine.id)
        .where(
            InvoiceLine.invoice_id == Invoice.id,
            ~exists().where(WorkUnit.invoice_line_id == InvoiceLine.id),
        )
        .exists()
    )
    no_line = ~exists().where(InvoiceLine.invoice_id == Invoice.id)
    return or_(free_line, no_line)


def _latest_invoice_by_customer(
    ctx: DetectorContext, organizations: Collection[UUID], *, beyond_days: bool = False
) -> dict[UUID, date]:
    """The date of the most recent issued fattura of each of these organizations; with
    `beyond_days`, only those that are not made of worked days alone."""
    if not organizations:
        return {}
    extra = (_not_days_only(),) if beyond_days else ()
    return {
        customer_id: issued
        for customer_id, issued in ctx.session.execute(
            select(Invoice.customer_id, func.max(Invoice.issue_date))
            .where(
                Invoice.deleted_at.is_(None),
                Invoice.type == "invoice",
                Invoice.status == "issued",
                Invoice.issue_date.is_not(None),
                Invoice.customer_id.in_(organizations),
                *extra,
            )
            .group_by(Invoice.customer_id)
        )
        if issued is not None
    }


def _oldest_uncovered(
    months: int, first_day: date, last_closed: date, contract: Contract, cards: list[RateCard]
) -> tuple[date, date] | None:
    """`(start, end)` of the first closed period from `first_day` in which the contract ran
    and a fee card was in force on the last day, or `None`."""
    end = period_end(months, first_day)
    while end <= last_closed:
        start = period_start(months, end)
        if contract.end is not None and contract.end < start:
            return None
        if any(
            card.valid_from <= end and (card.valid_to is None or card.valid_to >= end)
            for card in cards
        ):
            return start, end
        end = _next_period_end(months, end)
    return None


def _bills_days(ctx: DetectorContext, contract_ids: list[UUID]) -> set[UUID]:
    """The contracts among `contract_ids` that also bill days: a daily or hourly card, or
    a day on record."""
    if not contract_ids:
        return set()
    carded = select(RateCard.contract_id).where(
        RateCard.contract_id.in_(contract_ids), RateCard.type.in_(RATE_CARD_DAY_HOUR_TYPES)
    )
    worked = select(WorkUnit.contract_id).where(WorkUnit.contract_id.in_(contract_ids))
    return set(ctx.session.scalars(carded.union(worked)))


def detect_fixed_fee(ctx: DetectorContext) -> list[Detection]:
    """Contracts with a fixed-fee card whose **oldest uncovered period** has closed, at
    least `bound` days ago and never fewer than one.

    A period is covered by an issued invoice of the customer dated on or after its last
    day. An invoice carries no contract or rate card (only a customer, a deal and an
    accrual period), so the strongest link the data has is its lines: a line bound to a
    work unit is a billed day (`work_units/invoicing.py` builds days-only invoices that
    way), and a fee is a line no work unit is bound to. For a contract that also bills
    days (a daily or hourly card, or a day on record) only an invoice with such a line, or
    with no line at all, can cover the fee: an invoice of days alone does not. A contract
    with only a fee keeps the plain rule, any issued invoice covers. The oldest uncovered
    period is the first one ending after the last covering invoice (or after the
    contract's `start` when that is later) in which the contract ran and a fee card was
    in force on the last day; the measure is the days since that day, so a fee left
    unbilled for months climbs to `serious` and `critical` instead of resetting every
    period. The subject is the contract and that period's first day."""
    has_fee = exists().where(
        RateCard.contract_id == Contract.id, RateCard.type == RECURRING_FEE_TYPE
    )
    contracts = _live_contracts(ctx, _PERIODIC, has_fee)
    cards: dict[UUID, list[RateCard]] = defaultdict(list)
    if contracts:
        for card in ctx.session.scalars(
            select(RateCard).where(
                RateCard.contract_id.in_(list(contracts)),
                RateCard.type == RECURRING_FEE_TYPE,
            )
        ):
            cards[card.contract_id].append(card)
    latest = _latest_invoice_by_customer(ctx, {c.customer_id for c in contracts.values()})
    resolver = LinkResolver(ctx.session)
    resolver.preload(organizations=(contract.customer_id for contract in contracts.values()))
    day_billers = _bills_days(ctx, list(contracts))
    organizations = {c.customer_id for c in contracts.values()}
    latest_fee = _latest_invoice_by_customer(ctx, organizations, beyond_days=True)
    found: list[Detection] = []
    for contract in contracts.values():
        months = _periodic(contract)
        assert months is not None
        last_closed = period_start(months, ctx.today) - timedelta(days=1)
        last_invoice = (latest_fee if contract.id in day_billers else latest).get(
            contract.customer_id
        )
        first_day = contract.start
        if last_invoice is not None:
            first_day = max(first_day, last_invoice + timedelta(days=1))
        period = _oldest_uncovered(months, first_day, last_closed, contract, cards[contract.id])
        if period is None:
            continue
        start, end = period
        late = (ctx.today - end).days
        if late < max(ctx.bound(FIXED_FEE), 1):
            continue
        recipients, customer = _recipients(ctx, resolver, contract)
        found.append(
            Detection(
                type="fixed_fee_period_unbilled",
                subject_id=uuid5(contract.id, start.isoformat()),
                measure=float(late),
                recipients=recipients,
                title=f"Canone da fatturare: {contract.title}",
                detail=(
                    f"Il periodo dal {day_short(start)} al {day_short(end)} è chiuso "
                    f"da {format_days(late)} e nessuna fattura lo copre{customer}"
                ),
                entity="contract",
                entity_id=contract.id,
                legal_entity_id=contract.legal_entity_id,
                customer_id=contract.customer_id,
            )
        )
    return found


def detect_on_completion(ctx: DetectorContext) -> list[Detection]:
    """Contracts billed on completion, ended at least `bound` days ago (never fewer than
    one), with something still unbilled: worked days on no invoice line, and/or a one-off
    fee (an `one_off` rate card in force on the contract's `end`) that no issued
    invoice of the customer dated on or after `end` covers; on a contract that also bills
    days, an invoice of days alone does not (the rule of `detect_fixed_fee`, REB-937). A
    contract with both is raised once, counted from `end` either way, and the text names both."""
    ended = (~_PERIODIC, Contract.end.is_not(None), Contract.end < ctx.today)
    has_fee = exists().where(RateCard.contract_id == Contract.id, RateCard.type == ONE_OFF_FEE_TYPE)
    contracts = _live_contracts(ctx, *ended, or_(_has_unbilled_days(), has_fee))
    unbilled = _unbilled_by_contract(ctx, *ended)
    cards: dict[UUID, list[RateCard]] = defaultdict(list)
    if contracts:
        for card in ctx.session.scalars(
            select(RateCard).where(
                RateCard.contract_id.in_(list(contracts)), RateCard.type == ONE_OFF_FEE_TYPE
            )
        ):
            cards[card.contract_id].append(card)
    organizations = {c.customer_id for c in contracts.values()}
    latest = _latest_invoice_by_customer(ctx, organizations)
    latest_fee = _latest_invoice_by_customer(ctx, organizations, beyond_days=True)
    day_billers = _bills_days(ctx, list(contracts))
    resolver = LinkResolver(ctx.session)
    resolver.preload(organizations=(contract.customer_id for contract in contracts.values()))
    found: list[Detection] = []
    for contract in contracts.values():
        assert contract.end is not None
        late = (ctx.today - contract.end).days
        if late < max(ctx.bound(ON_COMPLETION), 1):
            continue
        end = contract.end
        covered = (latest_fee if contract.id in day_billers else latest).get(contract.customer_id)
        fee_unbilled = (covered is None or covered < end) and any(
            card.valid_from <= end and (card.valid_to is None or card.valid_to >= end)
            for card in cards.get(contract.id, ())
        )
        days = unbilled.get(contract.id, [])
        if not days and not fee_unbilled:
            continue
        what: list[str] = []
        if days:
            total = sum((quantity for _, quantity in days), start=Decimal(0))
            what.append(f"{work_units(total)} di lavoro non sono ancora in una fattura")
        if fee_unbilled:
            what.append("il compenso a completamento non è stato fatturato")
        recipients, customer = _recipients(ctx, resolver, contract)
        found.append(
            Detection(
                type="on_completion_unbilled",
                subject_id=contract.id,
                measure=float(late),
                recipients=recipients,
                title=f"Contratto concluso da fatturare: {contract.title}",
                detail=(
                    f"Concluso {day_short(end)} ({format_days_ago(late)}): "
                    f"{' e '.join(what)}{customer}"
                ),
                entity="contract",
                entity_id=contract.id,
                legal_entity_id=contract.legal_entity_id,
                customer_id=contract.customer_id,
            )
        )
    return sorted(found, key=lambda item: (-item.measure, str(item.subject_id)))


def detect_days_unlinked(ctx: DetectorContext) -> list[Detection]:
    """Issued, still unpaid invoices of the last `UNLINKED_LOOKBACK_DAYS` days, of a customer
    with unbilled days, none of whose lines carries a day."""
    earliest: dict[UUID, date] = {
        customer_id: day
        for customer_id, day in ctx.session.execute(
            select(Contract.customer_id, func.min(WorkUnit.date))
            .join(Contract, Contract.id == WorkUnit.contract_id)
            .where(Contract.deleted_at.is_(None), *_unbilled_days())
            .group_by(Contract.customer_id)
        )
    }
    if not earliest:
        return []
    linked = (
        select(WorkUnit.id)
        .join(InvoiceLine, InvoiceLine.id == WorkUnit.invoice_line_id)
        .where(InvoiceLine.invoice_id == Invoice.id)
        .exists()
    )
    rows = ctx.session.execute(
        join_sales_item(
            select(
                Invoice.id,
                Invoice.year,
                Invoice.number,
                Invoice.legal_entity_id,
                Invoice.customer_id,
                Invoice.issue_date,
            )
        )
        .where(
            Invoice.deleted_at.is_(None),
            Invoice.type == "invoice",
            Invoice.status == "issued",
            still_owed(),
            Invoice.issue_date.is_not(None),
            Invoice.issue_date <= ctx.today - timedelta(days=max(ctx.bound(UNLINKED), 0)),
            Invoice.issue_date >= ctx.today - timedelta(days=UNLINKED_LOOKBACK_DAYS),
            Invoice.customer_id.in_(earliest),
            ~linked,
        )
        .order_by(Invoice.issue_date, Invoice.id)
    ).all()
    resolver = LinkResolver(ctx.session)
    resolver.preload(invoices=(row.id for row in rows))
    found: list[Detection] = []
    for row in rows:
        assert row.issue_date is not None
        first = earliest[row.customer_id]
        if row.issue_date < first:
            continue
        links = resolver.resolve(invoice_id=row.id)
        number = (
            f" {number_complete(row.year, row.number)}"
            if row.year is not None and row.number is not None
            else ""
        )
        customer = f" · {links.customer_name}" if links.customer_name else ""
        found.append(
            Detection(
                type="invoice_days_unlinked",
                subject_id=row.id,
                measure=float((ctx.today - row.issue_date).days),
                recipients=ctx.directory.recipients(links.owners, row.legal_entity_id),
                title=f"Fattura senza giornate collegate: fattura{number}",
                detail=(
                    "Nessuna riga porta le giornate lavorate, e ce ne sono ancora da "
                    f"fatturare dal {day_short(first)}{customer}"
                ),
                entity="invoice",
                entity_id=row.id,
                legal_entity_id=row.legal_entity_id,
                customer_id=row.customer_id,
            )
        )
    return found


DETECTORS = (
    Detector(PERIOD_CLOSED, detect_period_closed, applies=_has_days),
    Detector(FIXED_FEE, detect_fixed_fee, applies=_has_fixed_fee),
    Detector(ON_COMPLETION, detect_on_completion, applies=_has_completion_contract),
    Detector(UNLINKED, detect_days_unlinked, applies=_has_days),
)
