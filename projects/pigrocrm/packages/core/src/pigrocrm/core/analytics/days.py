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
- **`revocato` and `rifiutato` are not charted.** Both mean the day never happened, so
  stacking them beside worked days would count days nobody worked.
- **A line is shared out by days.** One invoice line can bill several days and carries the
  total of all of them: each day takes its share of the line (`quantita` over the line's
  days), so a line enters a figure once however many days it bills, and a period that cuts
  a month in two takes only the share of the days inside it. A day no rate card covers is
  counted apart (`giorni_senza_listino`), never priced at zero.
"""

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.analytics.schemas import DaysMonth, DaysRate, DaysStateShare
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.money import ZERO_MONEY, round_money
from pigrocrm.core.work_units.models import WorkUnit, WorkUnitTransition

# The eight states a bar stacks, in the order the legend reads them.
STATI_GRAFICO: tuple[str, ...] = (
    "proposto",
    "approvato",
    "lavorato",
    "fatturato",
    "pagato",
    "lavorato_senza_approvazione",
    "contestato",
    "non_fatturabile",
)
# What counts as a day recorded: the work happened, whatever became of it afterwards (a day
# written off as unbillable was still worked).
STATI_REGISTRATI: frozenset[str] = frozenset(
    {
        "lavorato",
        "lavorato_senza_approvazione",
        "fatturato",
        "pagato",
        "contestato",
        "non_fatturabile",
    }
)
STATI_MAI_ACCADUTI: tuple[str, ...] = ("rifiutato", "revocato")
FINESTRA_MEDIA = 3
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


def _share(value: Decimal, tallest: Decimal) -> float:
    return 0.0 if tallest <= 0 else float(value / tallest)


def monthly_figures(
    session: Session,
    da: date,
    a: date,
    azienda_id: UUID | None,
    today: date,
    timezone: str,
) -> tuple[list[DaysMonth], str | None, Decimal, Decimal, Decimal]:
    """`(mesi, ultimo_mese_completo, giorni_nel_periodo, senza_approvazione_nel_periodo,
    senza_approvazione_sempre)`."""
    window = months_between(da, a)
    first_year, first_month = window[0]
    lookback_year, lookback_month = shift_month(first_year, first_month, -FINESTRA_MEDIA)
    fetch_from = date(lookback_year, lookback_month, 1)
    fetch_to = date(a.year, a.month, calendar.monthrange(a.year, a.month)[1])

    month_of = func.to_char(WorkUnit.data, "YYYY-MM")
    stmt = (
        select(month_of, WorkUnit.stato, func.sum(WorkUnit.quantita))
        .join(Contract, Contract.id == WorkUnit.contract_id)
        .where(
            WorkUnit.data >= fetch_from,
            WorkUnit.data <= fetch_to,
            WorkUnit.stato.not_in(STATI_MAI_ACCADUTI),
        )
        .group_by(month_of, WorkUnit.stato)
    )
    if azienda_id is not None:
        stmt = stmt.where(Contract.azienda_id == azienda_id)
    per_month: dict[str, dict[str, Decimal]] = defaultdict(dict)
    for key, stato, total in session.execute(stmt).all():
        per_month[key][stato] = Decimal(total)

    slipped_at = func.to_char(func.timezone(timezone, WorkUnitTransition.created_at), "YYYY-MM")
    risk_stmt = (
        select(slipped_at, func.sum(WorkUnit.quantita))
        .join(WorkUnit, WorkUnit.id == WorkUnitTransition.work_unit_id)
        .join(Contract, Contract.id == WorkUnit.contract_id)
        .where(WorkUnitTransition.stato_nuovo == "lavorato_senza_approvazione")
        .group_by(slipped_at)
    )
    if azienda_id is not None:
        risk_stmt = risk_stmt.where(Contract.azienda_id == azienda_id)
    risk_by_month = {key: Decimal(days) for key, days in session.execute(risk_stmt).all()}

    def recorded(key: str) -> Decimal:
        return sum(
            (days for stato, days in per_month.get(key, {}).items() if stato in STATI_REGISTRATI),
            start=ZERO_DAYS,
        )

    rows = []
    for year, month in window:
        key = month_key(year, month)
        # Three whole months before this one, a quiet month counting as zero: the
        # lookback the query fetched exists only to give the window's first bar a baseline.
        prior = [
            recorded(month_key(*shift_month(year, month, -step)))
            for step in range(1, FINESTRA_MEDIA + 1)
        ]
        average = round_money(sum(prior, start=ZERO_DAYS) / FINESTRA_MEDIA)
        stack = {s: per_month.get(key, {}).get(s, ZERO_DAYS) for s in STATI_GRAFICO}
        rows.append(
            (year, month, key, stack, recorded(key), average, risk_by_month.get(key, ZERO_DAYS))
        )

    tallest = max((sum(row[3].values(), start=ZERO_DAYS) for row in rows), default=ZERO_DAYS)
    tallest_recorded = max((row[4] for row in rows), default=ZERO_DAYS)
    tallest_risk = max((row[6] for row in rows), default=ZERO_DAYS)

    months: list[DaysMonth] = []
    for year, month, key, stack, registrati, average, risk in rows:
        total = sum(stack.values(), start=ZERO_DAYS)
        end = date(year, month, calendar.monthrange(year, month)[1])
        months.append(
            DaysMonth(
                mese=key,
                completo=end < today,
                per_stato=[
                    DaysStateShare(stato=s, giorni=stack[s], quota=_share(stack[s], tallest))
                    for s in STATI_GRAFICO
                ],
                totale=total,
                quota_totale=_share(total, tallest),
                registrati=registrati,
                quota_registrati=_share(registrati, tallest_recorded),
                media_precedente=average,
                senza_approvazione=risk,
                quota_senza_approvazione=_share(risk, tallest_risk),
            )
        )
    last_complete = next((m.mese for m in reversed(months) if m.completo), None)
    in_window = {m.mese for m in months}
    period_total = sum((m.totale for m in months), start=ZERO_DAYS)
    return (
        months,
        last_complete,
        period_total,
        sum((days for key, days in risk_by_month.items() if key in in_window), start=ZERO_DAYS),
        sum(risk_by_month.values(), start=ZERO_DAYS),
    )


@dataclass
class _Rate:
    etichetta: str
    giorni: Decimal = ZERO_DAYS
    importo: Decimal = ZERO_MONEY
    nominale: Decimal = ZERO_MONEY
    giorni_con_listino: Decimal = ZERO_DAYS
    giorni_senza_listino: Decimal = ZERO_DAYS
    lines: set[UUID] = field(default_factory=set)

    def read(self, key: UUID) -> DaysRate:
        return DaysRate(
            id=key,
            etichetta=self.etichetta,
            giorni_fatturati=self.giorni,
            importo_fatturato=round_money(self.importo),
            tariffa_reale=None if self.giorni <= 0 else round_money(self.importo / self.giorni),
            giorni_senza_listino=self.giorni_senza_listino,
            tariffa_nominale=(
                None
                if self.giorni_con_listino <= 0
                else round_money(self.nominale / self.giorni_con_listino)
            ),
        )


def realised_rates(
    session: Session, da: date, a: date, azienda_id: UUID | None
) -> tuple[list[DaysRate], list[DaysRate], Decimal, Decimal | None]:
    """`(per_cliente, per_contratto, giorni_fatturati, tariffa_reale)` over the days dated
    `da`..`a` that sit on a line of an issued invoice."""
    stmt = (
        select(
            WorkUnit.data,
            WorkUnit.quantita,
            Contract.id,
            Contract.titolo,
            Customer.id,
            Customer.ragione_sociale,
            InvoiceLine.id,
            InvoiceLine.prezzo_totale,
        )
        .join(Contract, Contract.id == WorkUnit.contract_id)
        .join(Customer, Customer.id == Contract.customer_id)
        .join(InvoiceLine, InvoiceLine.id == WorkUnit.invoice_line_id)
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .where(
            WorkUnit.data >= da,
            WorkUnit.data <= a,
            WorkUnit.stato.not_in(STATI_MAI_ACCADUTI),
            Invoice.tipo == "fattura",
            Invoice.stato == "emessa",
            Invoice.deleted_at.is_(None),
        )
        .order_by(WorkUnit.data, WorkUnit.id)
    )
    if azienda_id is not None:
        stmt = stmt.where(Contract.azienda_id == azienda_id)
    rows = session.execute(stmt).all()

    line_ids = {row[6] for row in rows}
    line_days: dict[UUID, Decimal] = {}
    if line_ids:
        for line_id, billed in session.execute(
            select(WorkUnit.invoice_line_id, func.sum(WorkUnit.quantita))
            .where(
                WorkUnit.invoice_line_id.in_(line_ids),
                WorkUnit.stato.not_in(STATI_MAI_ACCADUTI),
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
    for giorno, quantita, contract_id, titolo, customer_id, nome, line_id, line_total in rows:
        quantita = Decimal(quantita)
        card = next(
            (
                c
                for c in cards[contract_id]
                if c.valido_da <= giorno and (c.valido_a is None or giorno <= c.valido_a)
            ),
            None,
        )
        share = line_total * quantita / line_days[line_id]
        for key, label, bucket in (
            (customer_id, nome, by_customer),
            (contract_id, titolo, by_contract),
        ):
            rate = bucket.setdefault(key, _Rate(label))
            rate.giorni += quantita
            if card is None:
                rate.giorni_senza_listino += quantita
            else:
                rate.nominale += round_money(card.importo * quantita)
                rate.giorni_con_listino += quantita
            rate.importo += share
        total_days += quantita
        total_amount += share

    def ordered(bucket: dict[UUID, _Rate]) -> list[DaysRate]:
        read = [rate.read(key) for key, rate in bucket.items()]
        return sorted(read, key=lambda r: (-r.importo_fatturato, r.etichetta))

    return (
        ordered(by_customer),
        ordered(by_contract),
        total_days,
        None if total_days <= 0 else round_money(total_amount / total_days),
    )
