"""The advances of a person's INPS contributions, from the profile's facts (REB-1097).

A pure lookup over the pack and the profile: it answers the two advances of a contribution year
(`InpsYearLaw.advances`) or why it cannot, in the words of `advances.Missing`, never a guess. The
income they are computed on is the previous year's (Redditi PF 2026 fascicolo 2, appendix «INPS -
Modalità di calcolo degli acconti»), so it is an argument: an estimate of it is the caller's.
The facts are read the way the contributions' estimate reads them (`ordinary_estimate._inps`): a
fact the figure needs and the profile does not state is a `Missing`, the older ceiling's
(`inps_before_1996`) only where it changes the figure, and the reductions INPS granted are
applied only where the profile says so.

The flat-rate regime's election is a fact of the profile too: a flat-rate artisan or trader who
did not say whether the 35% reduction was requested (`flat_rate_inps_reduction`) has no advance,
since the two answers give different figures.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pigrocrm.core.fiscal.advances import Missing
from pigrocrm.core.fiscal.law_inps import InpsAdvances, InpsYearLaw, TradeGestione
from pigrocrm.core.fiscal.ordinary_estimate import InpsFacts, inps_reductions
from pigrocrm.core.fiscal.pack import FiscalPack
from pigrocrm.core.fiscal.schemas import FiscalProfileRead


def inps_advance_plan(
    pack: FiscalPack,
    year: int,
    profile: FiscalProfileRead,
    previous_income: Decimal | None,
    *,
    flat_rate_regime: bool,
) -> InpsAdvances | Missing:
    """The advances of `year`'s INPS contributions for the person `profile` describes, on the
    income of the year before (`previous_income`, `None` when it cannot be estimated)."""
    law = pack.inps_for(year)
    if law is None:
        return Missing(f"the pack carries no INPS law for {year}")
    if profile.inps_category == "gestione_separata":
        if profile.inps_covered_elsewhere is None:
            return Missing(
                "the profile does not say whether the person is a pensioner or insured elsewhere"
            )
        if previous_income is None:
            return _no_income(year)
        return law.advances(
            "gestione_separata",
            previous_income,
            covered_elsewhere=profile.inps_covered_elsewhere,
        )
    if profile.inps_category != "artigiani_commercianti":
        return Missing("the profile does not state the INPS category")
    trade: TradeGestione
    if profile.inps_trade == "artigiani":
        trade = "artigiani"
    elif profile.inps_trade == "commercianti":
        trade = "commercianti"
    else:
        return Missing("the profile does not say whether the person is an artisan or a trader")
    if previous_income is None:
        return _no_income(year)
    kwargs: dict[str, Any] = {"trade": trade}
    if flat_rate_regime:
        if profile.flat_rate_inps_reduction is None:
            return Missing(
                "the profile does not say whether the flat-rate 35% INPS reduction was requested"
            )
        kwargs["flat_rate_reduction"] = profile.flat_rate_inps_reduction
    if not kwargs.get("flat_rate_reduction"):
        # The flat-rate 35% excludes the other two reductions (art. 1 c. 80 L. 190/2014; INPS
        # news of 28 April 2025); without it they apply where the profile says INPS granted them.
        facts = InpsFacts(
            category="artigiani_commercianti",
            trade=trade,
            covered_elsewhere=None,
            before_1996=profile.inps_before_1996,
            over_65_reduction=profile.inps_over_65_reduction,
            new_enrolment_reduction=profile.inps_new_enrolment_reduction,
            first_enrolment_month=profile.inps_first_enrolment_month,
        )
        kwargs.update(inps_reductions(law, facts)[0])
    return _by_ceiling(law, previous_income, profile.inps_before_1996, kwargs)


def _no_income(year: int) -> Missing:
    return Missing(f"the income of {year - 1}, the base of the INPS advances, cannot be estimated")


def _by_ceiling(
    law: InpsYearLaw, income: Decimal, before_1996: bool | None, kwargs: dict[str, Any]
) -> InpsAdvances | Missing:
    """The advances of an artisan or a trader. The older ceiling matters only above the lower
    one: where both answer the same, the question is not needed."""
    if before_1996 is not None:
        return law.advances(
            "artigiani_commercianti", income, contributions_before_1996=before_1996, **kwargs
        )
    newer = law.advances(
        "artigiani_commercianti", income, contributions_before_1996=False, **kwargs
    )
    older = law.advances("artigiani_commercianti", income, contributions_before_1996=True, **kwargs)
    if newer == older:
        return newer
    return Missing(
        "the profile does not say whether the person had contributions before 1996: with this "
        "income the contribution ceiling changes the advances"
    )


__all__ = ["inps_advance_plan"]
