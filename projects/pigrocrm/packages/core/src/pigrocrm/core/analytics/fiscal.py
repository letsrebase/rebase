"""The annual income estimate -- the last piece of the previous system's logic to leave the browser.

Slice 1 §2.2 cited three constants inside `App.jsx` -- `FORFETTARIO_INPS_RATE = 0.2607`,
a 67% profitability coefficient and a 5% substitute tax -- as the empirical justification
for this whole architecture. They were there to compute a margin. Bringing them into
`packages/core` is the final payment on that debt, and slice 1 §14 assigns it to this
slice.

The **level** is the correction this module was written for: the previous system computed this
per offer, which is wrong in three independent ways (see `test_fiscal_estimate.py`'s own
docstring for all three). Here it is annual, never per deal, and there is deliberately no
per-deal variant anywhere in this package.

The **formula** was carried over from the previous system until REB-1066, which settled it from
the law (`fiscal/law_flat_rate.py`): the INPS contributions are owed on the flat-rate income, and
they are deducted from it before the substitute tax rate is applied (L. 190/2014 art. 1 c. 64).
The previous formula did it the other way round, taking the contributions on the income less the
substitute tax. Both give the same total to set aside and the same net income, to within two cents
of rounding; they split it differently between the two lines.

The three rates are **not** constants here either. Task 4B-2 put them on `fiscal_profile`
as three nullable columns, `FiscalPanel.tsx` exposes all three as editable fields, and
this module takes them as arguments: the ATECO coefficient depends on the activity code
and the INPS gestione separata rate is re-set by the Legge di Bilancio most years, so a
literal in this file would be the same defect one directory further in.

Pure: no session, no ORM, so the arithmetic is provable on its own and the same numbers
can be checked against a spreadsheet.
"""

from decimal import Decimal
from uuid import UUID

from pigrocrm.core.analytics.schemas import FiscalEstimate
from pigrocrm.core.fiscal.law_flat_rate import ContributionBase, FlatRateLaw, SubstituteTaxBase
from pigrocrm.core.money import round_money

# Named in the payload, not only in the page copy (§8): a labelled estimate is useful,
# an estimate presented as an actual is the original defect in a new form. Each clause
# is a real reason the figure will differ from the declaration -- the INPS floor and
# ceiling above all, which is precisely why this computation is meaningless per deal.
WARNING = (
    "Stima indicativa. Non tiene conto del minimale e del massimale contributivo, "
    "di altri redditi, degli acconti già versati né di deduzioni e detrazioni. "
    "Per la dichiarazione fai riferimento al tuo commercialista."
)

_HUNDRED = Decimal(100)


def _contribution_base(base: ContributionBase, taxable_amount: Decimal) -> Decimal:
    """The amount the contributions rate applies to, as the law of the year names it
    (`FlatRateLaw.contribution_base`): the flat-rate income, before the contributions are
    deducted from it."""
    match base:
        case "flat_rate_income":
            return taxable_amount


def _substitute_tax_base(
    base: SubstituteTaxBase, taxable_amount: Decimal, contributions: Decimal
) -> Decimal:
    """The amount the substitute tax rate applies to, as the law of the year names it
    (`FlatRateLaw.substitute_tax_base`): the flat-rate income less the contributions deducted.
    The contributions are never more than the income, since the rate is at most 100%, so the
    base is never negative: the cap the return's instructions put on the deduction (what fits
    in the income, rows LM35 and LM36) never bites."""
    match base:
        case "flat_rate_income_net_of_contributions":
            return taxable_amount - contributions


def coefficient_warning(flat_rate: FlatRateLaw, coefficient: Decimal | None) -> str | None:
    """A note for a profile whose coefficient is not one of the year's annex 4 (L. 190/2014).

    A warning and not a refusal, so a profile saved before the table was shipped keeps working
    and a save that touches nothing else is never turned down for a value the person typed
    long ago. `None` when the coefficient is listed or not stated."""
    if coefficient is None or flat_rate.lists_coefficient(coefficient):
        return None
    listed = ", ".join(f"{value.normalize():f}%" for value in sorted(flat_rate.coefficient_values))
    return (
        f"Il coefficiente di redditività {coefficient.normalize():f}% non è tra quelli "
        f"dell'allegato 4 della legge 190/2014 ({listed}): controlla il parametro in "
        "Impostazioni. Il coefficiente è quello del gruppo ATECO dell'attività."
    )


def estimate_income(
    *,
    year: int,
    legal_entity_id: UUID,
    revenue: Decimal,
    coefficient: Decimal | None,
    rate_substitute: Decimal | None,
    social_security_rate: Decimal | None,
    flat_rate: FlatRateLaw | None,
) -> FiscalEstimate:
    """Taxable base, substitute tax, contributions and estimated net for one year.

    Percentages in, so `67.00` rather than `0.67`: the columns store what a user reads
    and types, and the single division by 100 happens here.

    The order is the law's (L. 190/2014 art. 1 c. 64): the income is the revenue times the
    coefficient; the contributions are owed on that income; they are deducted from it, and the
    substitute tax is owed on what is left. The substitute tax therefore depends on the INPS
    rate as well as on its own, and without an INPS rate it has no figure: a figure computed as
    if no contribution were deducted would overstate it, which is a guess.

    A missing parameter yields `None` for every line that depends on it, never a guess.
    A parameter nobody configured is not a parameter of zero, and a report that filled it
    in would be presenting an assumption as a figure -- the same rule `line_value` obeys
    for an hour with no rate, and `percentage_of` for a zero denominator.

    `round_money` at every step rather than once at the end, so the printed lines add up
    to the printed total: §6.2's rule, applied to a report instead of an invoice. The
    substitute tax is computed on the *rounded* income less the *rounded* contributions,
    because those are the figures the reader can see.

    The contributions deducted are the year's own, as the estimate computes them. The law
    deducts the contributions *paid* in the tax period (the balance of the year before and the
    advances of this one), which a year's own figures approximate and an accountant's declared
    figure replaces (taxes design § 2).

    `flat_rate` is the year's law (`TaxYearLaw.flat_rate`); `None` means the pack has no income
    estimate for the year, and then no parameter is read and every line is `None`: a regime
    with no law for a year computes nothing for it.
    """
    if flat_rate is None:
        coefficient = rate_substitute = social_security_rate = None
    taxable_amount = (
        round_money(revenue * coefficient / _HUNDRED) if coefficient is not None else None
    )
    # The gestione separata is owed on the base the year's law names, which is what makes it
    # depend on two of the three rates and not one.
    contributions = (
        round_money(
            _contribution_base(flat_rate.contribution_base, taxable_amount)
            * social_security_rate
            / _HUNDRED
        )
        if (
            flat_rate is not None
            and taxable_amount is not None
            and social_security_rate is not None
        )
        else None
    )
    substitute_base = (
        _substitute_tax_base(flat_rate.substitute_tax_base, taxable_amount, contributions)
        if (flat_rate is not None and taxable_amount is not None and contributions is not None)
        else None
    )
    substitute = (
        round_money(substitute_base * rate_substitute / _HUNDRED)
        if (substitute_base is not None and rate_substitute is not None)
        else None
    )
    # Off `revenue`, not off `taxable_amount`: the coefficient decides what is *taxed*, not
    # what was collected, and the money actually left in the account is the whole
    # invoiced amount less the two outgoings.
    net = (
        round_money(revenue - substitute - contributions)
        if (substitute is not None and contributions is not None)
        else None
    )
    return FiscalEstimate(
        year=year,
        legal_entity_id=legal_entity_id,
        warning=WARNING,
        coefficient_warning=(
            coefficient_warning(flat_rate, coefficient) if flat_rate is not None else None
        ),
        revenue=revenue,
        # The rates are echoed back beside the figures they produced. A reader looking at
        # a number they did not expect needs to see which coefficient it came from, and
        # the profile row may have been edited since.
        profitability_coefficient=coefficient,
        taxable_amount=taxable_amount,
        substitute_tax_base=substitute_base,
        substitute_tax_rate=rate_substitute,
        substitute_tax=substitute,
        social_security_rate=social_security_rate,
        contributions=contributions,
        estimated_net_income=net,
        due_total=(
            round_money(substitute + contributions)
            if (substitute is not None and contributions is not None)
            else None
        ),
    )


__all__ = ["WARNING", "coefficient_warning", "estimate_income"]
