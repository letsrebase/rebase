"""The days analysis (REB-756): the days of a period by month and state, the realised day
rate against the rate card, and the trend of days that slipped into «lavorato senza
approvazione». Mastro's `/analysis/days`, on this schema's `work_units`.

Reads only, and every figure arrives computed: the browser draws what it is sent. The
functions here take a session and return plain values; `AnalyticsService.days_analysis`
turns them into the response.

Three decisions, each the one mastro made:

- **The monthly figures are whole calendar months.** A period `da`..`a` is rounded out to
  the months that contain it, because a bar is a month; the realised rate reads the exact
  dates, so a day invoiced the year before a chosen year never enters that year's rate.
- **`revoked` and `rejected` are not charted.** Both mean the day never happened, so
  stacking them beside worked days would count days nobody worked.
- **A line is shared out by days.** One invoice line can bill several days and carries the
  total of all of them: each day takes its share of the line (`quantity` over the line's
  days), so a line enters a figure once however many days it bills, and a period that cuts
  a month in two takes only the share of the days inside it. A day no rate card covers is
  counted apart (`days_without_list_price`), never priced at zero.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.analytics.filters import revenue_filter
from pigrocrm.core.analytics.schemas import DaysMonth, DaysRate, DaysStateShare
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db.clock import month_bounds
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.money import ZERO_MONEY, round_money, share
from pigrocrm.core.work_units.models import WorkUnit, WorkUnitTransition

# The eight states a bar stacks, in the order the legend reads them.
STATUSES_GRAFICO: tuple[str, ...] = (
    "proposed",
    "approved",
    "worked",
    "invoiced",
    "paid",
    "worked_without_approval",
    "disputed",
    "non_billable",
)
# What counts as a day recorded: the work happened, whatever became of it afterwards (a day
# written off as unbillable was still worked).
STATUSES_REGISTER: frozenset[str] = frozenset(
    {
        "worked",
        "worked_without_approval",
        "invoiced",
        "paid",
        "disputed",
        "non_billable",
    }
)
STATUSES_NEVER_ACCADUTI: tuple[str, ...] = ("rejected", "revoked")
WINDOW_MEDIA = 3
ZERO_DAYS = Decimal("0.00")


def month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def months_between(da: date, a: date) -> list[tuple[int, int]]:
    """Every `(year, month)` from `da`'s month to `a`'s, inclusive."""
    months: list[tuple[int, int]] = []
    year, month = da.year, da.month
    while (year, month) <= (a.year, a.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def monthly_figures(
    session: Session,
    da: date,
    a: date,
    legal_entity_id: UUID | None,
    today: date,
    timezone: str,
) -> tuple[list[DaysMonth], str | None, Decimal, Decimal, Decimal]:
    """`(mesi, ultimo_mese_completo, giorni_nel_periodo, senza_approvazione_nel_periodo,
    senza_approvazione_sempre)`."""
    window = months_between(da, a)
    first_year, first_month = window[0]
    lookback_year, lookback_month = shift_month(first_year, first_month, -WINDOW_MEDIA)
    fetch_from = date(lookback_year, lookback_month, 1)
    fetch_to = month_bounds(a.year, a.month)[1]

    month_of = func.to_char(WorkUnit.data, "YYYY-MM")
    stmt = (
        select(month_of, WorkUnit.status, func.sum(WorkUnit.quantity))
        .join(Contract, Contract.id == WorkUnit.contract_id)
        .where(
            WorkUnit.data >= fetch_from,
            WorkUnit.data <= fetch_to,
            WorkUnit.status.not_in(STATUSES_NEVER_ACCADUTI),
        )
        .group_by(month_of, WorkUnit.status)
    )
    if legal_entity_id is not None:
        stmt = stmt.where(Contract.legal_entity_id == legal_entity_id)
    per_month: dict[str, dict[str, Decimal]] = defaultdict(dict)
    for key, status, total in session.execute(stmt).all():
        per_month[key][status] = Decimal(total)

    # The first slip of each day, and only that: a day that was contested and worked again
    # without approval slipped twice, and one day is one day.
    first_slip = (
        select(
            WorkUnitTransition.work_unit_id.label("work_unit_id"),
            func.min(WorkUnitTransition.created_at).label("at"),
        )
        .where(WorkUnitTransition.new_status == "worked_without_approval")
        .group_by(WorkUnitTransition.work_unit_id)
        .subquery()
    )
    slipped_at = func.to_char(func.timezone(timezone, first_slip.c.at), "YYYY-MM")
    risk_stmt = (
        select(slipped_at, func.sum(WorkUnit.quantity))
        .select_from(first_slip)
        .join(WorkUnit, WorkUnit.id == first_slip.c.work_unit_id)
        .join(Contract, Contract.id == WorkUnit.contract_id)
        .group_by(slipped_at)
    )
    if legal_entity_id is not None:
        risk_stmt = risk_stmt.where(Contract.legal_entity_id == legal_entity_id)
    risk_by_month = {key: Decimal(days) for key, days in session.execute(risk_stmt).all()}

    def recorded(key: str) -> Decimal:
        return sum(
            (
                days
                for status, days in per_month.get(key, {}).items()
                if status in STATUSES_REGISTER
            ),
            start=ZERO_DAYS,
        )

    rows = []
    for year, month in window:
        key = month_key(year, month)
        # Three whole months before this one, a quiet month counting as zero: the
        # lookback the query fetched exists only to give the window's first bar a baseline.
        prior = [
            recorded(month_key(*shift_month(year, month, -step)))
            for step in range(1, WINDOW_MEDIA + 1)
        ]
        average = round_money(sum(prior, start=ZERO_DAYS) / WINDOW_MEDIA)
        stack = {s: per_month.get(key, {}).get(s, ZERO_DAYS) for s in STATUSES_GRAFICO}
        rows.append(
            (year, month, key, stack, recorded(key), average, risk_by_month.get(key, ZERO_DAYS))
        )

    tallest = max((sum(row[3].values(), start=ZERO_DAYS) for row in rows), default=ZERO_DAYS)
    tallest_recorded = max((row[4] for row in rows), default=ZERO_DAYS)
    tallest_risk = max((row[6] for row in rows), default=ZERO_DAYS)

    months: list[DaysMonth] = []
    for year, month, key, stack, register, average, risk in rows:
        total = sum(stack.values(), start=ZERO_DAYS)
        end = month_bounds(year, month)[1]
        months.append(
            DaysMonth(
                month=key,
                complete=end < today,
                by_status=[
                    DaysStateShare(status=s, days=stack[s], share=share(stack[s], tallest))
                    for s in STATUSES_GRAFICO
                ],
                total=total,
                total_share=share(total, tallest),
                register=register,
                register_share=share(register, tallest_recorded),
                media_previous=average,
                without_approval=risk,
                share_without_approval=share(risk, tallest_risk),
            )
        )
    last_complete = next((m.month for m in reversed(months) if m.complete), None)
    in_window = {m.month for m in months}
    period_total = sum((m.total for m in months), start=ZERO_DAYS)
    return (
        months,
        last_complete,
        period_total,
        sum((days for key, days in risk_by_month.items() if key in in_window), start=ZERO_DAYS),
        sum(risk_by_month.values(), start=ZERO_DAYS),
    )


@dataclass
class _Rate:
    label: str
    days: Decimal = ZERO_DAYS
    amount: Decimal = ZERO_MONEY
    nominal: Decimal = ZERO_MONEY
    days_with_list_price: Decimal = ZERO_DAYS
    days_without_list_price: Decimal = ZERO_DAYS
    lines: set[UUID] = field(default_factory=set)

    def read(self, key: UUID) -> DaysRate:
        return DaysRate(
            id=key,
            label=self.label,
            invoiced_days=self.days,
            invoiced_amount=round_money(self.amount),
            actual_rate=None if self.days <= 0 else round_money(self.amount / self.days),
            days_without_list_price=self.days_without_list_price,
            nominal_rate=(
                None
                if self.days_with_list_price <= 0
                else round_money(self.nominal / self.days_with_list_price)
            ),
        )


def realised_rates(
    session: Session, da: date, a: date, legal_entity_id: UUID | None
) -> tuple[list[DaysRate], list[DaysRate], Decimal, Decimal | None]:
    """`(per_cliente, per_contratto, giorni_fatturati, tariffa_reale)` over the days dated
    `da`..`a` that sit on a line of an issued invoice."""
    stmt = (
        select(
            WorkUnit.data,
            WorkUnit.quantity,
            Contract.id,
            Contract.title,
            Customer.id,
            Customer.legal_name,
            InvoiceLine.id,
            InvoiceLine.total_price,
        )
        .join(Contract, Contract.id == WorkUnit.contract_id)
        .join(Customer, Customer.id == Contract.customer_id)
        .join(InvoiceLine, InvoiceLine.id == WorkUnit.invoice_line_id)
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .where(
            WorkUnit.data >= da,
            WorkUnit.data <= a,
            WorkUnit.status.not_in(STATUSES_NEVER_ACCADUTI),
            *revenue_filter(),
        )
        .order_by(WorkUnit.data, WorkUnit.id)
    )
    if legal_entity_id is not None:
        stmt = stmt.where(Contract.legal_entity_id == legal_entity_id)
    rows = session.execute(stmt).all()

    line_ids = {row[6] for row in rows}
    line_days: dict[UUID, Decimal] = {}
    if line_ids:
        for line_id, billed in session.execute(
            select(WorkUnit.invoice_line_id, func.sum(WorkUnit.quantity))
            .where(
                WorkUnit.invoice_line_id.in_(line_ids),
                WorkUnit.status.not_in(STATUSES_NEVER_ACCADUTI),
            )
            .group_by(WorkUnit.invoice_line_id)
        ):
            line_days[line_id] = Decimal(billed)

    cards: dict[UUID, list[RateCard]] = defaultdict(list)
    contract_ids = {row[2] for row in rows}
    if contract_ids:
        for listed in session.scalars(
            select(RateCard).where(RateCard.contract_id.in_(contract_ids))
        ):
            cards[listed.contract_id].append(listed)

    by_customer: dict[UUID, _Rate] = {}
    by_contract: dict[UUID, _Rate] = {}
    total_days = ZERO_DAYS
    total_amount = ZERO_MONEY
    for day, quantity, contract_id, title, customer_id, nome, line_id, line_total in rows:
        quantity = Decimal(quantity)
        card = next(
            (
                c
                for c in cards[contract_id]
                # Only a daily card prices a day: an hourly or a fixed-fee card has no
                # per-day figure to set the realised rate against.
                if c.type == "daily"
                and c.valid_from <= day
                and (c.valid_to is None or day <= c.valid_to)
            ),
            None,
        )
        portion = line_total * quantity / line_days[line_id]
        for key, label, bucket in (
            (customer_id, nome, by_customer),
            (contract_id, title, by_contract),
        ):
            rate = bucket.setdefault(key, _Rate(label))
            rate.days += quantity
            if card is None:
                rate.days_without_list_price += quantity
            else:
                rate.nominal += round_money(card.amount * quantity)
                rate.days_with_list_price += quantity
            rate.amount += portion
        total_days += quantity
        total_amount += portion

    def ordered(bucket: dict[UUID, _Rate]) -> list[DaysRate]:
        read = [rate.read(key) for key, rate in bucket.items()]
        return sorted(read, key=lambda r: (-r.invoiced_amount, r.label))

    return (
        ordered(by_customer),
        ordered(by_contract),
        total_days,
        None if total_days <= 0 else round_money(total_amount / total_days),
    )
