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
from pigrocrm.core.money import round_money

# Named in the payload, not only in the page copy (§8): a labelled estimate is useful,
# an estimate presented as an actual is the original defect in a new form. Each clause
# is a real reason the figure will differ from the declaration -- the INPS floor and
# ceiling above all, which is precisely why this computation is meaningless per deal.
AVVERTENZA = (
    "Stima indicativa. Non tiene conto del minimale e del massimale contributivo, "
    "di altri redditi, degli acconti già versati né di deduzioni e detrazioni. "
    "Per la dichiarazione fai riferimento al tuo commercialista."
)

_HUNDRED = Decimal(100)


def estimate_income(
    *,
    anno: int,
    azienda_id: UUID,
    ricavi: Decimal,
    coefficiente: Decimal | None,
    aliquota_sostitutiva: Decimal | None,
    aliquota_inps: Decimal | None,
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
    """
    imponibile = round_money(ricavi * coefficiente / _HUNDRED) if coefficiente is not None else None
    sostitutiva = (
        round_money(imponibile * aliquota_sostitutiva / _HUNDRED)
        if (imponibile is not None and aliquota_sostitutiva is not None)
        else None
    )
    # The gestione separata is owed on the taxable base net of the substitute tax, which
    # is what makes it depend on two of the three rates and not one.
    contributi = (
        round_money((imponibile - sostitutiva) * aliquota_inps / _HUNDRED)
        if (imponibile is not None and sostitutiva is not None and aliquota_inps is not None)
        else None
    )
    # Off `ricavi`, not off `imponibile`: the coefficient decides what is *taxed*, not
    # what was collected, and the money actually left in the account is the whole
    # invoiced amount less the two outgoings.
    netto = (
        round_money(ricavi - sostitutiva - contributi)
        if (sostitutiva is not None and contributi is not None)
        else None
    )
    return FiscalEstimate(
        anno=anno,
        azienda_id=azienda_id,
        avvertenza=AVVERTENZA,
        ricavi=ricavi,
        # The rates are echoed back beside the figures they produced. A reader looking at
        # a number they did not expect needs to see which coefficient it came from, and
        # the profile row may have been edited since.
        coefficiente_redditivita=coefficiente,
        imponibile=imponibile,
        aliquota_imposta_sostitutiva=aliquota_sostitutiva,
        imposta_sostitutiva=sostitutiva,
        aliquota_inps=aliquota_inps,
        contributi=contributi,
        reddito_netto_stimato=netto,
        totale_dovuto=(
            round_money(sostitutiva + contributi)
            if (sostitutiva is not None and contributi is not None)
            else None
        ),
    )


__all__ = ["AVVERTENZA", "estimate_income"]
