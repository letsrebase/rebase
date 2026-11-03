"""The single authority on rounding for this project.

Three scales, not two (slice 4 §6.1). Money and every P&L row are 2 places; hours
are 2 places; **rates and internal hourly costs are 6**, because slice 3 makes
`invoice_lines.unit_price` `Numeric(12,6)` and a rate at two places would
change value the moment hours became an invoice line -- the reconciliation of slice
4B would then fail by cents for a reason nobody could reconstruct.

`ROUND_HALF_UP`, never `ROUND_HALF_EVEN`. Italian fiscal practice, inherited from
slice 3 §6.1, and what the SdI's own arithmetic expects on the invoice line these
hours become. `Decimal.quantize` defaults to the context rounding, which is
`ROUND_HALF_EVEN`; every call here passes the mode explicitly rather than depending
on a context nobody sets.

Lives at the top level of `core`, not inside `timetracking/`, because `analytics/`
needs the identical arithmetic and a second copy is how two totals begin to
disagree.
"""

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

MONEY_SCALE = 2
HOURS_SCALE = 2
FACTOR_SCALE = 6

MONEY_QUANT = Decimal("0.01")
HOURS_QUANT = Decimal("0.01")
FACTOR_QUANT = Decimal("0.000001")

ZERO_MONEY = Decimal("0.00")
ZERO_HOURS = Decimal("0.00")


def round_money(value: Decimal) -> Decimal:
    """Two decimals, `ROUND_HALF_UP`.

    Not `Decimal`'s own `ROUND_HALF_EVEN` default: Italian fiscal practice, and the
    arithmetic the SdI's own checks perform, round a half away from zero. Banker's
    rounding disagrees on alternate cents, which is enough for a file to be discarded.
    """
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def round_hours(value: Decimal) -> Decimal:
    return value.quantize(HOURS_QUANT, rounding=ROUND_HALF_UP)


def line_value(hours: Decimal, factor: Decimal | None) -> Decimal | None:
    """`ROUND(hours x factor, 2)`, or `None` when there is no factor.

    `None`, never `0.00`: an hour with no rate is an hour nobody has priced, and a
    silent zero would say the work was free -- a lie that sums. The caller excludes
    `None` from money aggregates and counts those rows separately as the "ore senza tariffa"
    figure (§5.1).
    """
    if factor is None:
        return None
    return round_money(hours * factor)


def sum_money(values: Iterable[Decimal | None]) -> Decimal:
    """The sum of already-rounded row values, never the rounding of an exact sum.

    The two differ by cents (see `test_money.py`), and this one wins for a reason
    specific to this slice: the row value is what the timesheet prints next to each
    entry, and a total that is not the sum of the visible column is the fastest way
    to lose a client's trust in a document you are sending them to get paid.
    """
    total = ZERO_MONEY
    for value in values:
        if value is not None:
            total += value
    return total


def sum_hours(values: Iterable[Decimal | None]) -> Decimal:
    total = ZERO_HOURS
    for value in values:
        if value is not None:
            total += value
    return total


def percentage_of(part: Decimal, whole: Decimal) -> Decimal | None:
    """`None` when `whole` is zero, never `0.00`.

    Zero per cent means "everything I earned went out in costs". A zero denominator
    means nothing has been earned yet. Two different facts; the report does not
    flatten them (§7.1, criterion 6). No division is ever executed on a zero
    denominator, so this is also the only place that question is asked.
    """
    if whole == 0:
        return None
    return round_money(part / whole * Decimal(100))


def share(part: Decimal, whole: Decimal) -> float:
    """`part / whole` as the float a `share` field carries, `0.0` when there is nothing to
    scale against (a zero or negative `whole`). The one zero-guarded ratio of this
    project: a share is presentation, so the float is made here and nowhere else."""
    return float(part / whole) if whole > 0 else 0.0


def percent_to_share(percent: Decimal) -> float:
    """A percentage stored as `50.00` as the share in [0, 1] every `share` is."""
    return float(percent / 100)
