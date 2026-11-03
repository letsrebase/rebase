"""The SQL fragments the analytics queries share: what counts as revenue, which azienda,
which date puts a document in a period."""

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, SQLColumnExpression, func

from pigrocrm.core.analytics.schemas import CashBase, RevenueBase
from pigrocrm.core.db.clock import month_bounds
from pigrocrm.core.invoices.models import Invoice


def revenue_filter() -> tuple[ColumnElement[bool], ...]:
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


def invoice_azienda(azienda_id: UUID | None) -> tuple[ColumnElement[bool], ...]:
    """One azienda's invoices, or every azienda's (REB-630, spec 2026-10-03 §1.9):
    the one extra predicate the sums below take, so an azienda's figure reads that
    azienda's register alone and «tutte» stays the unfiltered sum."""
    return () if azienda_id is None else (Invoice.azienda_id == azienda_id,)


def in_year(when: SQLColumnExpression[date | None], anno: int) -> tuple[ColumnElement[bool], ...]:
    """`when` within the calendar year `anno`, as a range a plain index on the column can
    serve (an `extract(year ...)` over it cannot)."""
    first, _ = month_bounds(anno, 1)
    _, last = month_bounds(anno, 12)
    return (when >= first, when <= last)


def day_after(day: date) -> datetime:
    """The first instant after `day`, in UTC: `created_at > day` read as a day comparison
    (`date(created_at) > day`) without wrapping the column in a function."""
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC)


def accrual_date() -> SQLColumnExpression[date | None]:
    """The one definition of "the month a document's period names":
    `coalesce(competenza_da, data_emissione)`, so a document that never declared a
    period is read by the one date it has and a register with no periods reads the same
    under either base. Shared by the period P&L (ORB-61) and the cash view (ORB-133), so
    the two screens cannot put the same invoice in two different months."""
    return func.coalesce(Invoice.competenza_da, Invoice.data_emissione)


def revenue_date(base: RevenueBase) -> SQLColumnExpression[date | None]:
    """The date that puts an invoice's revenue in a period, for the two readings the
    period P&L offers (ORB-61). `emissione` is the recorded default (§7.1);
    `competenza` is `accrual_date`. The fiscal figures take no base and stay by
    emission; the cash view has a base of its own (`CashBase`, `cash_date`)."""
    if base == "competenza":
        return accrual_date()
    return Invoice.data_emissione


def cash_date(
    base: CashBase, by_money: SQLColumnExpression[date | None]
) -> SQLColumnExpression[date | None]:
    """The date that puts a document's money in a month of the cash view (ORB-133).
    Under `competenza` it is `accrual_date` for every series; under `incasso` it is
    whatever the money's own reading is for that series, which the caller names."""
    if base == "competenza":
        return accrual_date()
    return by_money
