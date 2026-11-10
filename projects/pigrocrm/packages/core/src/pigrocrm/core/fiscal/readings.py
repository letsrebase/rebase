"""The year's taxes as three readings per line (REB-1026, taxes design § 2 and § 3).

For each tax of the legal entity's pack and year the page draws the **estimate** the code
computes, the **declared** figure an administrator typed from the accountant's return and the
**paid** figure, and says which of them counts: paid over declared over estimate, by stage and
never by time (design § 2). The page adds nothing and compares nothing: a line's figures, the
reading that counts and the totals of each column all come from here.

A figure that cannot be given is `None`, never zero: a tax the code does not compute has no
estimate, a tax with no typed return has no declared figure, and no source writes a paid
figure yet (the F24 due items that would are their own card), so «paid» is `None` on every
line today.
"""

from collections.abc import Mapping
from decimal import Decimal

from pigrocrm.core.fiscal.law import DeclaredTax, TaxYearLaw
from pigrocrm.core.fiscal.schemas import (
    DECLARED_TAXES,
    DeclaredTaxFigureRead,
    ReadingStage,
    TaxReadingLine,
    TaxReadings,
    TaxReadingTotals,
)


def _total(figures: list[Decimal | None]) -> tuple[Decimal | None, int]:
    """The sum of the figures that exist, and how many lines had none. `None` when no line
    has a figure: a column of nothing adds up to nothing, not to zero."""
    given = [figure for figure in figures if figure is not None]
    return (sum(given, Decimal("0.00")) if given else None), len(figures) - len(given)


def _counts(estimate: Decimal | None, declared: Decimal | None) -> ReadingStage | None:
    """The reading that counts: the highest stage with a figure. Paid would come first and has
    no source yet."""
    if declared is not None:
        return "declared"
    return "estimate" if estimate is not None else None


def build_tax_readings(
    law: TaxYearLaw | None,
    estimates: Mapping[DeclaredTax, Decimal | None],
    declared: list[DeclaredTaxFigureRead],
) -> TaxReadings:
    """The lines of `law`'s taxes, then any tax a figure was typed for that the law does not
    list (typed data is never hidden), each with its three readings.

    `estimates` holds the taxes the code has an estimate for, `None` when a parameter is
    missing; a tax absent from it has none and `estimable` says so. Typing a tax and year
    again replaces the row, so `declared` holds at most one row of a tax."""
    typed = {figure.tax: figure for figure in declared}
    listed: list[DeclaredTax] = list(law.taxes) if law is not None else []
    listed += [tax for tax in DECLARED_TAXES if tax in typed and tax not in listed]
    lines: list[TaxReadingLine] = []
    for tax in listed:
        figure = typed.get(tax)
        estimate = estimates.get(tax)
        due = figure.tax_due if figure is not None else None
        lines.append(
            TaxReadingLine(
                tax=tax,
                estimable=tax in estimates,
                estimate=estimate,
                declared=due,
                paid=None,
                counts=_counts(estimate, due),
            )
        )
    estimate_total, estimate_left_out = _total([line.estimate for line in lines])
    declared_total, declared_left_out = _total([line.declared for line in lines])
    paid_total, paid_left_out = _total([line.paid for line in lines])
    return TaxReadings(
        lines=lines,
        totals=TaxReadingTotals(
            estimate=estimate_total,
            estimate_left_out=estimate_left_out,
            declared=declared_total,
            declared_left_out=declared_left_out,
            paid=paid_total,
            paid_left_out=paid_left_out,
        ),
    )


__all__ = ["build_tax_readings"]
