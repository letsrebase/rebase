"""The annual income estimate -- the last piece of the previous system's logic to leave the browser.

Slice 1 §2.2 cited three constants inside `App.jsx` -- `FORFETTARIO_INPS_RATE = 0.2607`,
a 67% profitability coefficient and a 5% substitute tax -- as the empirical justification
for this whole architecture. They were there to compute a margin. Bringing them into
`packages/core` is the final payment on that debt, and slice 1 §14 assigns it to this
slice.

The **formula** is carried over unchanged; it was never the defect. The **level** is
different, and that is the whole correction: the previous system computed this per offer, which is
wrong in three independent ways (see `test_fiscal_estimate.py`'s own docstring for all
three). Here it is annual, never per deal, and there is deliberately no per-deal variant
anywhere in this package.

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
from pigrocrm.core.fiscal.law import ContributionBase
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


def _contribution_base(
    base: ContributionBase, taxable_amount: Decimal, substitute: Decimal
) -> Decimal:
    """The amount the contributions rate applies to, as the law of the year names it. TO
    VERIFY with the accountant (A5 row «Flat-rate contribution base...», REB-975): a second
    answer is a second `ContributionBase` member and a second arm here, never a flag in a
    caller."""
    match base:
        case "taxable_amount_net_of_substitute_tax":
            return taxable_amount - substitute


def estimate_income(
    *,
    year: int,
    legal_entity_id: UUID,
    revenue: Decimal,
    coefficient: Decimal | None,
    rate_substitute: Decimal | None,
    social_security_rate: Decimal | None,
    contribution_base: ContributionBase | None,
) -> FiscalEstimate:
    """Taxable base, substitute tax, contributions and estimated net for one year.

    Percentages in, so `67.00` rather than `0.67`: the columns store what a user reads
    and types, and the single division by 100 happens here.

    A missing parameter yields `None` for every line that depends on it, never a guess.
    A parameter nobody configured is not a parameter of zero, and a report that filled it
    in would be presenting an assumption as a figure -- the same rule `line_value` obeys
    for an hour with no rate, and `percentage_of` for a zero denominator.

    `round_money` at every step rather than once at the end, so the printed lines add up
    to the printed total: §6.2's rule, applied to a report instead of an invoice. The
    contributions base is the *rounded* substitute tax subtracted from the *rounded*
    taxable base, because those are the two figures the reader can see.

    `contribution_base` is the year's law (`TaxYearLaw.contribution_base`); `None` means the
    pack has no income estimate for the year, and then no parameter is read and every line is
    `None`: a regime with no law for a year computes nothing for it.
    """
    if contribution_base is None:
        coefficient = rate_substitute = social_security_rate = None
    taxable_amount = (
        round_money(revenue * coefficient / _HUNDRED) if coefficient is not None else None
    )
    substitute = (
        round_money(taxable_amount * rate_substitute / _HUNDRED)
        if (taxable_amount is not None and rate_substitute is not None)
        else None
    )
    # The gestione separata is owed on the base the year's law names, which is what makes it
    # depend on two of the three rates and not one.
    contributions = (
        round_money(
            _contribution_base(contribution_base, taxable_amount, substitute)
            * social_security_rate
            / _HUNDRED
        )
        if (
            contribution_base is not None
            and taxable_amount is not None
            and substitute is not None
            and social_security_rate is not None
        )
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
        revenue=revenue,
        # The rates are echoed back beside the figures they produced. A reader looking at
        # a number they did not expect needs to see which coefficient it came from, and
        # the profile row may have been edited since.
        profitability_coefficient=coefficient,
        taxable_amount=taxable_amount,
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


__all__ = ["WARNING", "estimate_income"]
