"""The hours the CRM reports on a match's deal, read as `MatchReport`'s figures (REB-498).

`EngagementService.report` asks the CRM's door for the period in windows of at most
`REPORT_MAX_DAYS` days (`report_windows`), joins the answers (`join_windows`) and groups
the rows by day, ISO week and month (`group`, or `group_report` for a plain body).
Everything here is pure: no session, no call.
"""

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from rebase_core.contract_schemas import (
    ReportDay,
    ReportDayInvoice,
    ReportInvoice,
    ReportMonth,
    ReportWeek,
)
from rebase_core.contracts.fields import CENT

HOURS_PER_DAY = Decimal(8)
# The CRM's own cap on a report's period, `a - da` in days: a longer span it refuses, so
# a longer engagement is read in consecutive windows of this size.
REPORT_MAX_DAYS = 800


def _hours(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def report_windows(da: date, a: date) -> list[tuple[date, date]]:
    """`[da, a]` in consecutive windows the CRM accepts: `[da, da + 800]`, then from the
    next day on, the last one ending on `a`."""
    windows = []
    start = da
    while True:
        end = min(start + timedelta(days=REPORT_MAX_DAYS), a)
        windows.append((start, end))
        if end >= a:
            return windows
        start = end + timedelta(days=1)


class _CrmInvoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID | None = None
    tipo: str
    anno: int | None = None
    numero: int | None = None
    stato: str
    stato_pagamento: str
    data: date | None = None
    ore: Decimal | None = None


class _CrmEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: date
    ore: Decimal
    descrizione: str | None = None
    fattura: _CrmInvoice | None = None


class CrmReport(BaseModel):
    """The door's report, as much of it as the hub reads: its rows, the CRM's own split
    of billed hours, the invoices, and the deal as it stands."""

    model_config = ConfigDict(extra="ignore")

    deal: dict[str, Any] | None = None
    giorni: list[_CrmEntry]
    ore_fatturate: Decimal
    ore_non_fatturate: Decimal
    fatture: list[_CrmInvoice] = Field(default_factory=list)


def join_windows(windows: list[CrmReport]) -> CrmReport:
    """The windows of one period as one report: the rows one after the other, the billed
    hours summed, an invoice that holds hours of two windows once with both, newest
    first as the CRM lists them, and the deal as the last window found it."""
    invoices: dict[UUID | int, _CrmInvoice] = {}
    for window in reversed(windows):
        for invoice in window.fatture:
            key: UUID | int = invoice.id if invoice.id is not None else id(invoice)
            seen = invoices.get(key)
            if seen is None:
                invoices[key] = invoice
            else:
                total = (seen.ore or Decimal(0)) + (invoice.ore or Decimal(0))
                invoices[key] = seen.model_copy(update={"ore": total})
    return CrmReport(
        deal=windows[-1].deal,
        giorni=[entry for window in windows for entry in window.giorni],
        ore_fatturate=sum((window.ore_fatturate for window in windows), Decimal(0)),
        ore_non_fatturate=sum((window.ore_non_fatturate for window in windows), Decimal(0)),
        fatture=list(invoices.values()),
    )


def _invoice_number(invoice: _CrmInvoice) -> str:
    if invoice.numero is None:
        return "senza numero"
    return f"{invoice.numero}/{invoice.anno}" if invoice.anno is not None else str(invoice.numero)


def _invoice_label(invoice: _CrmInvoice) -> str:
    number = _invoice_number(invoice)
    return number if invoice.tipo == "fattura" else f"{invoice.tipo} {number}"


def _labels(entries: Iterable[_CrmEntry]) -> list[str]:
    """The invoices a day's hours sit on, each named once, in the order they came."""
    return list(dict.fromkeys(_invoice_label(e.fattura) for e in entries if e.fattura is not None))


def _hours_per_invoice(entries: Iterable[_CrmEntry]) -> list[ReportDayInvoice]:
    """A day's hours on each invoice they sit on, in `_labels`' order, each summed from
    that invoice's own entries: the hours on no invoice are left out, and a proforma
    and an invoice with the same number stay apart (REB-505)."""
    hours: dict[tuple[str, str], Decimal] = {}
    for entry in entries:
        if entry.fattura is not None:
            key = (_invoice_number(entry.fattura), entry.fattura.tipo)
            hours[key] = hours.get(key, Decimal(0)) + entry.ore
    return [
        ReportDayInvoice(numero=numero, tipo=tipo, ore=_hours(ore))
        for (numero, tipo), ore in hours.items()
    ]


def group_report(crm: dict[str, Any], giorni_previsti: int | None) -> dict[str, Any]:
    """The CRM's report (one row per time entry) as `MatchReport`'s figures: summed by
    day, by ISO week (Monday to Sunday, «2026-W53» running into January) and by month,
    the total and its share of `giorni_previsti` times eight. The billed hours are the
    CRM's own figures, since only the CRM knows which invoices count. Pure: a body that
    is not the report raises `ValidationError`."""
    return group(CrmReport.model_validate(crm), giorni_previsti)


def group(report: CrmReport, giorni_previsti: int | None) -> dict[str, Any]:
    by_day: dict[date, list[_CrmEntry]] = {}
    for entry in sorted(report.giorni, key=lambda row: row.data):
        by_day.setdefault(entry.data, []).append(entry)
    per_giorno = [
        ReportDay(
            data=day,
            ore=_hours(sum((e.ore for e in entries), Decimal(0))),
            descrizioni=[
                e.descrizione.strip()
                for e in entries
                if e.descrizione is not None and e.descrizione.strip()
            ],
            fatture=_labels(entries),
            ore_per_fattura=_hours_per_invoice(entries),
        )
        for day, entries in by_day.items()
    ]
    weeks: dict[tuple[int, int], Decimal] = {}
    months: dict[tuple[int, int], Decimal] = {}
    for day in per_giorno:
        iso = day.data.isocalendar()
        weeks[(iso.year, iso.week)] = weeks.get((iso.year, iso.week), Decimal(0)) + day.ore
        month = (day.data.year, day.data.month)
        months[month] = months.get(month, Decimal(0)) + day.ore
    totale = _hours(sum((day.ore for day in per_giorno), Decimal(0)))
    ore_previste = _hours(giorni_previsti * HOURS_PER_DAY) if giorni_previsti else None
    return {
        "giorni_previsti": giorni_previsti,
        "ore_previste": ore_previste,
        "totale_ore": totale,
        "giorni_equivalenti": _hours(totale / HOURS_PER_DAY),
        "avanzamento": _hours(totale / ore_previste * 100) if ore_previste else None,
        "ore_fatturate": _hours(report.ore_fatturate),
        "ore_non_fatturate": _hours(report.ore_non_fatturate),
        "per_giorno": per_giorno,
        "per_settimana": [
            ReportWeek(
                settimana=f"{year}-W{week:02d}",
                da=date.fromisocalendar(year, week, 1),
                a=date.fromisocalendar(year, week, 7),
                ore=_hours(hours),
            )
            for (year, week), hours in sorted(weeks.items())
        ],
        "per_mese": [
            ReportMonth(mese=f"{year}-{month:02d}", ore=_hours(hours))
            for (year, month), hours in sorted(months.items())
        ],
        "fatture": [
            ReportInvoice(
                numero=_invoice_number(invoice),
                tipo=invoice.tipo,
                data=invoice.data,
                stato=invoice.stato,
                stato_pagamento=invoice.stato_pagamento,
                ore=_hours(invoice.ore or Decimal(0)),
            )
            for invoice in report.fatture
        ],
    }
