"""The money that slips, part two: what a contract has not billed (spec 2026-10-26 §3.3,
REB-767): `billable_period_closed`, `fixed_fee_period_unbilled`, `on_completion_unbilled`
and `invoice_days_unlinked`. The invoice and hours types are in `billing.py`.

**What the model records, and what these types read of it.** `Contract.cadenza_fatturazione`
is free text (no value set is pinned, `contracts/models.py`), so the three periodic
cadences are the words the rate cards already use for a period (`mensile`, `trimestrale`,
`annuale`, any case) and **every other value is read as billing on completion**. A period
is the calendar month, quarter or year, as in Mastro (`currentPeriodStart`), not one
counted from the contract's `inizio`. A contract bills «from worked days» when it has days
(`work_units`) in `lavorato` with no invoice line: the days the invoicing step gathers
(`work_units/invoicing.py`). A contract has a «fixed fee» when it has a `ricorrente_fisso`
rate card. Contracts are never `attivo`: their `stato` has no transition yet, so only
`deleted_at` decides which count.

An invoice carries no contract (it hangs on a customer and a deal), so «the contract's
invoices» means the customer's: an issued fattura of the contract's customer.

- `billable_period_closed`: measure is the days past the end of the period the contract's
  **oldest** unbilled day falls in, raised once that period has ended. Days of the current
  period are not late.
- `fixed_fee_period_unbilled`: the period that ended before the current one, when the
  contract ran in it, a fixed-fee card was in force on its last day, and the customer has
  no issued invoice dated on or after that last day (Mastro's rule, by issue date). The
  subject is the contract **and** the period (a uuid5 of both), so acknowledging September
  does not silence October; the entity the web opens is the contract.
- `on_completion_unbilled`: a contract billing on completion whose `fine` has passed with
  days unbilled; the measure is the days since `fine`. A contract with no `fine` has not
  completed, and Mastro's «aged open-ended» branch is not ported: the spec counts from the
  completion date.
- `invoice_days_unlinked`: an issued fattura none of whose lines carries a day, for a
  customer whose contract still has unbilled days no later than its issue date (an invoice
  older than every unbilled day cannot be about them). The measure is the days since it
  was issued; the ladder has one rung, `serious` at 0, so it is never urgent.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid5

from sqlalchemy import ColumnElement, exists, func, select

from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.contracts.projection import RECURRING_FEE_TIPO
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.naming import numero_completo
from pigrocrm.core.mail import giorno_breve
from pigrocrm.core.reminders.catalogue import REMINDER_TYPES
from pigrocrm.core.reminders.detect import Detection, Detector, DetectorContext
from pigrocrm.core.reminders.detectors.links import LinkResolver
from pigrocrm.core.work_units.models import WorkUnit

PERIOD_CLOSED = REMINDER_TYPES["billable_period_closed"]
FIXED_FEE = REMINDER_TYPES["fixed_fee_period_unbilled"]
ON_COMPLETION = REMINDER_TYPES["on_completion_unbilled"]
UNLINKED = REMINDER_TYPES["invoice_days_unlinked"]

# The months of a period, by the words a contract's cadence uses for it.
_PERIOD_MONTHS = {"mensile": 1, "trimestrale": 3, "annuale": 12}
_BILLABLE_DAY = "lavorato"


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
    return date(day.year, last, calendar.monthrange(day.year, last)[1])


def _periodic(contract: Contract) -> int | None:
    return period_months(contract.cadenza_fatturazione)


def _unbilled_days() -> tuple[ColumnElement[bool], ...]:
    return (WorkUnit.stato == _BILLABLE_DAY, WorkUnit.invoice_line_id.is_(None))


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
                    RateCard.tipo == RECURRING_FEE_TIPO,
                )
            )
        )
    )


def _has_completion_contract(ctx: DetectorContext) -> bool:
    return any(
        _periodic(contract) is None
        for contract in ctx.session.scalars(select(Contract).where(Contract.deleted_at.is_(None)))
    )


def _unbilled_by_contract(
    ctx: DetectorContext, contract_ids: set[UUID]
) -> dict[UUID, list[tuple[date, Decimal]]]:
    days: dict[UUID, list[tuple[date, Decimal]]] = defaultdict(list)
    if not contract_ids:
        return days
    for contract_id, day, quantity in ctx.session.execute(
        select(WorkUnit.contract_id, WorkUnit.data, WorkUnit.quantita)
        .where(WorkUnit.contract_id.in_(contract_ids), *_unbilled_days())
        .order_by(WorkUnit.data)
    ):
        days[contract_id].append((day, Decimal(quantity)))
    return days


def _live_contracts(ctx: DetectorContext) -> list[Contract]:
    return list(
        ctx.session.scalars(
            select(Contract).where(Contract.deleted_at.is_(None)).order_by(Contract.id)
        )
    )


def _days_text(total: Decimal) -> str:
    return f"{total.normalize():f}"


def _recipients(
    ctx: DetectorContext, resolver: LinkResolver, contract: Contract
) -> tuple[frozenset[UUID], str]:
    links = resolver.resolve(customer_id=contract.customer_id)
    name = f" · {links.customer_name}" if links.customer_name else ""
    return ctx.directory.recipients(links.owners, contract.azienda_id), name


def detect_period_closed(ctx: DetectorContext) -> list[Detection]:
    """Periodic contracts with unbilled days from a period that has ended, at least
    `bound` days ago and never fewer than one."""
    contracts = {contract.id: contract for contract in _live_contracts(ctx) if _periodic(contract)}
    unbilled = _unbilled_by_contract(ctx, set(contracts))
    resolver = LinkResolver(ctx.session)
    resolver.preload(customers=(contracts[cid].customer_id for cid in unbilled))
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
                title=f"Periodo chiuso da fatturare: {contract.titolo}",
                detail=(
                    f"{_days_text(total)} giornate lavorate non fatturate, "
                    f"la più vecchia del {giorno_breve(oldest)}: il suo periodo è finito "
                    f"{giorno_breve(end)}{customer}"
                ),
                entity="contract",
                entity_id=contract.id,
                azienda_id=contract.azienda_id,
                customer_id=contract.customer_id,
            )
        )
    return sorted(found, key=lambda item: (-item.measure, str(item.subject_id)))


def detect_fixed_fee(ctx: DetectorContext) -> list[Detection]:
    """Contracts with a fixed-fee card whose last closed period has no invoice dated on
    or after its last day, at least `bound` days after that day and never fewer than one."""
    contracts = [contract for contract in _live_contracts(ctx) if _periodic(contract) is not None]
    cards: dict[UUID, list[RateCard]] = defaultdict(list)
    if contracts:
        for card in ctx.session.scalars(
            select(RateCard).where(
                RateCard.contract_id.in_([contract.id for contract in contracts]),
                RateCard.tipo == RECURRING_FEE_TIPO,
            )
        ):
            cards[card.contract_id].append(card)
    latest: dict[UUID, date] = {
        customer_id: issued
        for customer_id, issued in ctx.session.execute(
            select(Invoice.customer_id, func.max(Invoice.data_emissione))
            .where(
                Invoice.deleted_at.is_(None),
                Invoice.tipo == "fattura",
                Invoice.stato == "emessa",
                Invoice.data_emissione.is_not(None),
            )
            .group_by(Invoice.customer_id)
        )
        if issued is not None
    }
    resolver = LinkResolver(ctx.session)
    resolver.preload(customers=(contract.customer_id for contract in contracts))
    found: list[Detection] = []
    for contract in contracts:
        months = _periodic(contract)
        assert months is not None
        first = period_start(months, ctx.today)
        end = first - timedelta(days=1)
        start = period_start(months, end)
        if contract.inizio > end or (contract.fine is not None and contract.fine < start):
            continue
        if not any(
            card.valido_da <= end and (card.valido_a is None or card.valido_a >= end)
            for card in cards.get(contract.id, ())
        ):
            continue
        last_invoice = latest.get(contract.customer_id)
        if last_invoice is not None and last_invoice >= end:
            continue
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
                title=f"Canone da fatturare: {contract.titolo}",
                detail=(
                    f"Il periodo dal {giorno_breve(start)} al {giorno_breve(end)} è chiuso "
                    f"e nessuna fattura lo copre{customer}"
                ),
                entity="contract",
                entity_id=contract.id,
                azienda_id=contract.azienda_id,
                customer_id=contract.customer_id,
            )
        )
    return found


def detect_on_completion(ctx: DetectorContext) -> list[Detection]:
    """Contracts billed on completion, ended at least `bound` days ago (never fewer than
    one), with days still unbilled."""
    contracts = {
        contract.id: contract
        for contract in _live_contracts(ctx)
        if _periodic(contract) is None and contract.fine is not None and contract.fine < ctx.today
    }
    unbilled = _unbilled_by_contract(ctx, set(contracts))
    resolver = LinkResolver(ctx.session)
    resolver.preload(customers=(contracts[cid].customer_id for cid in unbilled))
    found: list[Detection] = []
    for contract_id, days in unbilled.items():
        contract = contracts[contract_id]
        assert contract.fine is not None
        late = (ctx.today - contract.fine).days
        if late < max(ctx.bound(ON_COMPLETION), 1):
            continue
        total = sum((quantity for _, quantity in days), start=Decimal(0))
        recipients, customer = _recipients(ctx, resolver, contract)
        found.append(
            Detection(
                type="on_completion_unbilled",
                subject_id=contract.id,
                measure=float(late),
                recipients=recipients,
                title=f"Contratto concluso da fatturare: {contract.titolo}",
                detail=(
                    f"Concluso {giorno_breve(contract.fine)}, {_days_text(total)} giornate "
                    f"lavorate non sono ancora in una fattura{customer}"
                ),
                entity="contract",
                entity_id=contract.id,
                azienda_id=contract.azienda_id,
                customer_id=contract.customer_id,
            )
        )
    return sorted(found, key=lambda item: (-item.measure, str(item.subject_id)))


def detect_days_unlinked(ctx: DetectorContext) -> list[Detection]:
    """Issued invoices of a customer with unbilled days, none of whose lines carries a day."""
    earliest: dict[UUID, date] = {
        customer_id: day
        for customer_id, day in ctx.session.execute(
            select(Contract.customer_id, func.min(WorkUnit.data))
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
    rows = list(
        ctx.session.scalars(
            select(Invoice)
            .where(
                Invoice.deleted_at.is_(None),
                Invoice.tipo == "fattura",
                Invoice.stato == "emessa",
                Invoice.data_emissione.is_not(None),
                Invoice.data_emissione <= ctx.today - timedelta(days=max(ctx.bound(UNLINKED), 0)),
                Invoice.customer_id.in_(earliest),
                ~linked,
            )
            .order_by(Invoice.data_emissione, Invoice.id)
        )
    )
    resolver = LinkResolver(ctx.session)
    resolver.preload(invoices=(row.id for row in rows))
    found: list[Detection] = []
    for invoice in rows:
        assert invoice.data_emissione is not None
        first = earliest[invoice.customer_id]
        if invoice.data_emissione < first:
            continue
        links = resolver.resolve(invoice_id=invoice.id)
        number = (
            f" {numero_completo(invoice.anno, invoice.numero)}"
            if invoice.anno is not None and invoice.numero is not None
            else ""
        )
        customer = f" · {links.customer_name}" if links.customer_name else ""
        found.append(
            Detection(
                type="invoice_days_unlinked",
                subject_id=invoice.id,
                measure=float((ctx.today - invoice.data_emissione).days),
                recipients=ctx.directory.recipients(links.owners, invoice.azienda_id),
                title=f"Fattura senza giornate collegate: fattura{number}",
                detail=(
                    "Nessuna riga porta le giornate lavorate, e ce ne sono ancora da "
                    f"fatturare dal {giorno_breve(first)}{customer}"
                ),
                entity="invoice",
                entity_id=invoice.id,
                azienda_id=invoice.azienda_id,
                customer_id=invoice.customer_id,
            )
        )
    return found


DETECTORS = (
    Detector(PERIOD_CLOSED, detect_period_closed, applies=_has_days),
    Detector(FIXED_FEE, detect_fixed_fee, applies=_has_fixed_fee),
    Detector(ON_COMPLETION, detect_on_completion, applies=_has_completion_contract),
    Detector(UNLINKED, detect_days_unlinked, applies=_has_days),
)
