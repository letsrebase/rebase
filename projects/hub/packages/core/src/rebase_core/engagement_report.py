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
    kind: str
    year: int | None = None
    number: int | None = None
    status: str
    payment_status: str
    data: date | None = None
    hours: Decimal | None = None


class _CrmEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: date
    hours: Decimal
    description: str | None = None
    invoice: _CrmInvoice | None = None


class CrmReport(BaseModel):
    """The door's report, as much of it as the hub reads: its rows, the CRM's own split
    of billed hours, the invoices, and the deal as it stands."""

    model_config = ConfigDict(extra="ignore")

    deal: dict[str, Any] | None = None
    days: list[_CrmEntry]
    invoiced_hours: Decimal
    hours_uninvoiced: Decimal
    invoices: list[_CrmInvoice] = Field(default_factory=list)


def join_windows(windows: list[CrmReport]) -> CrmReport:
    """The windows of one period as one report: the rows one after the other, the billed
    hours summed, an invoice that holds hours of two windows once with both, newest
    first as the CRM lists them, and the deal as the last window found it."""
    invoices: dict[UUID | int, _CrmInvoice] = {}
    for window in reversed(windows):
        for invoice in window.invoices:
            key: UUID | int = invoice.id if invoice.id is not None else id(invoice)
            seen = invoices.get(key)
            if seen is None:
                invoices[key] = invoice
            else:
                total = (seen.hours or Decimal(0)) + (invoice.hours or Decimal(0))
                invoices[key] = seen.model_copy(update={"hours": total})
    return CrmReport(
        deal=windows[-1].deal,
        days=[entry for window in windows for entry in window.days],
        invoiced_hours=sum((window.invoiced_hours for window in windows), Decimal(0)),
        hours_uninvoiced=sum((window.hours_uninvoiced for window in windows), Decimal(0)),
        invoices=list(invoices.values()),
    )


def _invoice_number(invoice: _CrmInvoice) -> str:
    if invoice.number is None:
        return "senza numero"
    return f"{invoice.number}/{invoice.year}" if invoice.year is not None else str(invoice.number)


def _invoice_label(invoice: _CrmInvoice) -> str:
    number = _invoice_number(invoice)
    return number if invoice.kind == "fattura" else f"{invoice.kind} {number}"


def _labels(entries: Iterable[_CrmEntry]) -> list[str]:
    """The invoices a day's hours sit on, each named once, in the order they came."""
    return list(dict.fromkeys(_invoice_label(e.invoice) for e in entries if e.invoice is not None))


def _hours_per_invoice(entries: Iterable[_CrmEntry]) -> list[ReportDayInvoice]:
    """A day's hours on each invoice they sit on, in `_labels`' order, each summed from
    that invoice's own entries: the hours on no invoice are left out, and a proforma
    and an invoice with the same number stay apart (REB-505)."""
    hours: dict[tuple[str, str], Decimal] = {}
    for entry in entries:
        if entry.invoice is not None:
            key = (_invoice_number(entry.invoice), entry.invoice.kind)
            hours[key] = hours.get(key, Decimal(0)) + entry.hours
    return [
        ReportDayInvoice(number=number, kind=kind, hours=_hours(ore))
        for (number, kind), ore in hours.items()
    ]


def group_report(crm: dict[str, Any], expected_days: int | None) -> dict[str, Any]:
    """The CRM's report (one row per time entry) as `MatchReport`'s figures: summed by
    day, by ISO week (Monday to Sunday, «2026-W53» running into January) and by month,
    the total and its share of `expected_days` times eight. The billed hours are the
    CRM's own figures, since only the CRM knows which invoices count. Pure: a body that
    is not the report raises `ValidationError`."""
    return group(CrmReport.model_validate(crm), expected_days)


def group(report: CrmReport, expected_days: int | None) -> dict[str, Any]:
    by_day: dict[date, list[_CrmEntry]] = {}
    for entry in sorted(report.days, key=lambda row: row.data):
        by_day.setdefault(entry.data, []).append(entry)
    per_giorno = [
        ReportDay(
            data=day,
            hours=_hours(sum((e.hours for e in entries), Decimal(0))),
            descriptions=[
                e.description.strip()
                for e in entries
                if e.description is not None and e.description.strip()
            ],
            invoices=_labels(entries),
            hours_by_invoice=_hours_per_invoice(entries),
        )
        for day, entries in by_day.items()
    ]
    weeks: dict[tuple[int, int], Decimal] = {}
    months: dict[tuple[int, int], Decimal] = {}
    for day in per_giorno:
        iso = day.data.isocalendar()
        weeks[(iso.year, iso.week)] = weeks.get((iso.year, iso.week), Decimal(0)) + day.hours
        month = (day.data.year, day.data.month)
        months[month] = months.get(month, Decimal(0)) + day.hours
    total = _hours(sum((day.hours for day in per_giorno), Decimal(0)))
    expected_hours = _hours(expected_days * HOURS_PER_DAY) if expected_days else None
    return {
        "expected_days": expected_days,
        "expected_hours": expected_hours,
        "hours_total": total,
        "equivalent_days": _hours(total / HOURS_PER_DAY),
        "progress": _hours(total / expected_hours * 100) if expected_hours else None,
        "invoiced_hours": _hours(report.invoiced_hours),
        "hours_uninvoiced": _hours(report.hours_uninvoiced),
        "by_day": per_giorno,
        "by_week": [
            ReportWeek(
                week=f"{year}-W{week:02d}",
                da=date.fromisocalendar(year, week, 1),
                a=date.fromisocalendar(year, week, 7),
                hours=_hours(hours),
            )
            for (year, week), hours in sorted(weeks.items())
        ],
        "by_month": [
            ReportMonth(month=f"{year}-{month:02d}", hours=_hours(hours))
            for (year, month), hours in sorted(months.items())
        ],
        "invoices": [
            ReportInvoice(
                number=_invoice_number(invoice),
                kind=invoice.kind,
                data=invoice.data,
                status=invoice.status,
                payment_status=invoice.payment_status,
                hours=_hours(invoice.hours or Decimal(0)),
            )
            for invoice in report.invoices
        ],
    }
