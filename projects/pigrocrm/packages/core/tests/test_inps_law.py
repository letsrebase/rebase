"""REB-1064 and REB-1097: the 2026 INPS contributions of a person, in the pack.

The figures the circulars print (n. 8 of 3 February 2026 for the gestione separata, n. 14 of
9 February 2026 for artigiani e commercianti: the contributions on the minimum and the four
maxima) are asserted as printed, not recomputed with the code's own arithmetic. The others, marked
«by hand» or computed in a comment, are what the printed rates give on a round income; the
circulars of the flat-rate reduction (n. 35/2016) and of the advances (Redditi PF 2026) print no
example of the cent rounding.
"""

from datetime import date
from decimal import Decimal
from typing import get_args

import pytest

from pigrocrm.core.fiscal.calendar import tax_calendar
from pigrocrm.core.fiscal.law_inps import INPS_2026, InpsCategoryName
from pigrocrm.core.fiscal.pack import (
    IT_COMPANY_PACK,
    IT_FLAT_RATE_PACK,
    IT_STANDARD_PACK,
    NON_IT_PACK,
    FiscalPack,
)
from pigrocrm.core.fiscal.schemas import InpsCategory

D = Decimal


def test_the_category_names_are_the_profiles() -> None:
    assert set(get_args(InpsCategoryName)) == set(get_args(InpsCategory))


def test_the_two_person_packs_carry_the_year_and_no_other_pack_does() -> None:
    assert IT_STANDARD_PACK.inps_for(2026) is INPS_2026
    assert IT_FLAT_RATE_PACK.inps_for(2026) is INPS_2026
    assert IT_COMPANY_PACK.inps_for(2026) is None
    assert NON_IT_PACK.inps_for(2026) is None


@pytest.mark.parametrize("year", [2025, 2027])
def test_a_year_with_no_entry_has_no_figure_and_never_the_year_before(year: int) -> None:
    assert IT_STANDARD_PACK.inps_for(year) is None


def test_entries_must_increase_by_year() -> None:
    with pytest.raises(ValueError, match="INPS entries"):
        FiscalPack(id="x", version="1", ceilings=(), charges=(), inps=(INPS_2026, INPS_2026))


def test_the_gestione_separata_rate_is_the_sum_circolare_8_prints() -> None:
    law = INPS_2026.gestione_separata
    assert law.ivs_rate + law.additional_rate + law.iscro_rate == law.rate == D("26.07")
    assert law.covered_rate == D("24.00")
    assert law.ceiling == D("122295.00")


def test_gestione_separata_minimum_credits_the_year_with_the_printed_contributions() -> None:
    """Circolare 8/2026 § 6.2: 4.903,25 euro at 26,07% (of which 4.702,00 for the pension) and
    4.513,92 euro at 24% on the 18.808,00 euro minimum."""
    law = INPS_2026.gestione_separata
    assert law.crediting_minimum_income == D("18808.00")
    on_minimum = INPS_2026.contributions("gestione_separata", D("18808"))
    assert on_minimum.total == D("4903.25") == law.crediting_contribution
    covered = INPS_2026.contributions("gestione_separata", D("18808"), covered_elsewhere=True)
    assert covered.total == D("4513.92") == law.crediting_contribution_covered
    assert D("18808") * law.ivs_rate / 100 == D("4702.00")


def test_the_gestione_separata_pays_on_the_income_and_no_floor_applies() -> None:
    assert INPS_2026.contributions("gestione_separata", D("10000")).total == D("2607.00")
    assert INPS_2026.contributions("gestione_separata", D("0")).total == D("0.00")
    assert INPS_2026.contributions("gestione_separata", D("-500")).total == D("0.00")
    assert INPS_2026.contributions("gestione_separata", D("0")).dues == ()


def test_the_gestione_separata_stops_at_the_ceiling() -> None:
    at_ceiling = INPS_2026.contributions("gestione_separata", D("122295"))
    over = INPS_2026.contributions("gestione_separata", D("500000"))
    assert over.base == D("122295.00")
    assert (
        over.total == at_ceiling.total == D("31882.31")
    )  # 122.295 x 26,07% = 31.882,3065, by hand


def test_the_gestione_separata_is_paid_with_the_income_tax() -> None:
    result = INPS_2026.contributions("gestione_separata", D("40000"))
    assert result.fixed == D("0")
    assert [(due.kind, due.due) for due in result.dues] == [
        ("balance_and_first_advance", date(2026, 6, 30)),
        ("second_advance", date(2026, 11, 30)),
    ]


# --- artigiani e commercianti ---------------------------------------------------------------


def test_the_contribution_on_the_minimum_is_what_circolare_14_prints() -> None:
    """§ 2: artigiani 4.521,36 (4.513,92 IVS + 7,44 maternity); commercianti 4.611,64 (4.604,20
    IVS and the indemnity financing + 7,44)."""
    artisans = INPS_2026.contributions("artigiani_commercianti", D("0"), trade="artigiani")
    traders = INPS_2026.contributions("artigiani_commercianti", D("0"), trade="commercianti")
    assert artisans.fixed == D("4521.36")
    assert traders.fixed == D("4611.64")
    assert INPS_2026.artisans_traders.minimum_income == D("18808.00")
    assert INPS_2026.artisans_traders.maternity_per_year == D("7.44")


def test_an_income_below_the_minimum_owes_the_fixed_part_only() -> None:
    result = INPS_2026.contributions("artigiani_commercianti", D("9000"), trade="artigiani")
    assert result.fixed == D("4521.36")
    assert result.percentage == D("0.00")
    assert result.total == D("4521.36")
    assert [due.due for due in result.dues] == [
        date(2026, 5, 18),
        date(2026, 8, 20),
        date(2026, 11, 16),
        date(2027, 2, 16),
    ]
    assert {due.kind for due in result.dues} == {"fixed_instalment"}


def test_an_income_above_the_minimum_adds_the_percentage_and_its_dates() -> None:
    # 30.000 x 24% = 7.200,00 in all, of which 4.513,92 is on the minimum.
    result = INPS_2026.contributions("artigiani_commercianti", D("30000"), trade="artigiani")
    assert result.fixed == D("4521.36")
    assert result.percentage == D("2686.08")  # 7.200,00 - 4.513,92
    assert result.total == D("7207.44")  # 30.000 x 24% + 7,44
    assert [due.kind for due in result.dues] == [
        "fixed_instalment",
        "fixed_instalment",
        "fixed_instalment",
        "fixed_instalment",
        "balance_and_first_advance",
        "second_advance",
    ]
    assert result.dues[-2].due == date(2026, 6, 30)
    assert result.dues[-1].due == date(2026, 11, 30)


@pytest.mark.parametrize(
    ("trade", "before_1996", "income", "printed_maximum"),
    [
        # § 4: «€ 22.864,51 (€ 56.224*24% + € 37.483*25%)»
        ("artigiani", True, "93707", "22864.51"),
        # «€ 23.314,31 (€ 56.224*24,48% + € 37.483*25,48%)»
        ("commercianti", True, "93707", "23314.31"),
        # «€ 30.011,51 (€ 56.224*24% + € 66.071*25%)»
        ("artigiani", False, "122295", "30011.51"),
        # «€ 30.598,53 (€ 56.224*24,48% + € 66.071*25,48%)»
        ("commercianti", False, "122295", "30598.53"),
    ],
)
def test_the_maximum_contribution_is_what_circolare_14_prints(
    trade: str, before_1996: bool, income: str, printed_maximum: str
) -> None:
    kwargs = {"trade": trade, "contributions_before_1996": before_1996}
    at_ceiling = INPS_2026.contributions("artigiani_commercianti", D(income), **kwargs)  # type: ignore[arg-type]
    way_over = INPS_2026.contributions("artigiani_commercianti", D("900000"), **kwargs)  # type: ignore[arg-type]
    maternity = INPS_2026.artisans_traders.maternity_per_year
    assert at_ceiling.total - maternity == D(printed_maximum)
    # The ceiling stops the contribution: more income owes the same.
    assert way_over.total == at_ceiling.total
    assert way_over.base == D(income)


def test_the_ceilings_are_the_ones_circolare_14_prints() -> None:
    law = INPS_2026.artisans_traders
    assert law.first_band_limit == D("56224.00")
    assert law.ceiling_with_contributions_before_1996 == D("93707.00")
    assert law.ceiling == D("122295.00")
    # § 4: 93.707 is 56.224 plus two thirds of it, 37.483.
    assert law.first_band_limit + D("37483") == law.ceiling_with_contributions_before_1996


def test_the_higher_rate_starts_above_the_first_band() -> None:
    at_band = INPS_2026.contributions("artigiani_commercianti", D("56224"), trade="artigiani")
    plus_1000 = INPS_2026.contributions("artigiani_commercianti", D("57224"), trade="artigiani")
    assert plus_1000.total - at_band.total == D("250.00")  # 1.000 x 25%
    plus_1000_trader = (
        INPS_2026.contributions("artigiani_commercianti", D("57224"), trade="commercianti").total
        - INPS_2026.contributions("artigiani_commercianti", D("56224"), trade="commercianti").total
    )
    assert plus_1000_trader == D("254.80")  # 1.000 x 25,48%


def test_the_flat_rate_reduction_applies_to_the_minimum_and_keeps_the_fixed_part() -> None:
    """Circolare 35/2016 § 1: «la contribuzione dovuta, sia quella sul reddito entro il minimale,
    sia quella sul reddito eventualmente eccedente, viene ridotta del 35%», and «Risulta in ogni
    caso dovuto il contributo di maternità, pari ad € 7,44 annui, da corrispondere alle scadenze
    previste per la contribuzione in misura fissa». It replaced circolare 29/2015, which had no
    fixed part. The circulars print no example: by hand, 4.513,92 x 65% = 2.934,048 and 4.800,00
    on 20.000 less 4.513,92 = 286,08 x 65% = 185,952, each rounded to the cent."""
    result = INPS_2026.contributions(
        "artigiani_commercianti", D("20000"), trade="artigiani", flat_rate_reduction=True
    )
    assert result.fixed == D("2941.49")  # 2.934,05 + 7,44
    assert result.percentage == D("185.95")
    assert [due.kind for due in result.dues] == [
        "fixed_instalment",
        "fixed_instalment",
        "fixed_instalment",
        "fixed_instalment",
        "balance_and_first_advance",
        "second_advance",
    ]
    low = INPS_2026.contributions(
        "artigiani_commercianti", D("1000"), trade="artigiani", flat_rate_reduction=True
    )
    assert low.fixed == D("2941.49")
    assert low.percentage == D("0.00")
    assert {due.kind for due in low.dues} == {"fixed_instalment"}


def test_a_flat_rate_trader_owes_the_reduced_minimum_whatever_the_income() -> None:
    """The card's example: 4.604,20 x 65% = 2.992,73 (0,48 included: it is part of the
    contribution due) plus 7,44, where circolare 29/2015's reading gave 1.591,20 plus 7,44."""
    result = INPS_2026.contributions(
        "artigiani_commercianti", D("10000"), trade="commercianti", flat_rate_reduction=True
    )
    assert result.fixed == D("3000.17")
    assert result.percentage == D("0.00")
    above = INPS_2026.contributions(
        "artigiani_commercianti", D("30000"), trade="commercianti", flat_rate_reduction=True
    )
    # 30.000 x 24,48% = 7.344,00, less 4.604,20 = 2.739,80 x 65% = 1.780,87.
    assert (above.fixed, above.percentage) == (D("3000.17"), D("1780.87"))


def test_the_reduction_follows_the_higher_band_and_the_trader_rate() -> None:
    # 70.000 as a trader: 56.224 x 24,48% = 13.763,64 and 13.776 x 25,48% = 3.510,12
    # (13.776,00 x 0,2548 = 3.510,1248), 17.273,76 less 4.604,20 = 12.669,56 x 65% = 8.235,21,
    # plus 2.992,73 and 7,44.
    result = INPS_2026.contributions(
        "artigiani_commercianti", D("70000"), trade="commercianti", flat_rate_reduction=True
    )
    assert result.percentage == D("8235.21")
    assert result.total == D("11235.38")


def test_the_traders_indemnity_point_is_part_of_the_traders_rate() -> None:
    law = INPS_2026.artisans_traders
    assert law.artisans.indemnity_add_on == D("0.00")
    assert law.traders.indemnity_add_on == D("0.48")
    assert law.traders.up_to_first_band - law.artisans.up_to_first_band == D("0.48")


def test_an_over_65_pensioner_pays_half_of_the_ivs_on_the_minimum_and_on_the_excess() -> None:
    """Art. 59 c. 15 L. 449/1997 and INPS's page: «sia sul minimale di reddito che sulla quota
    eccedente». Whether the 0,48 and the maternity are halved is not printed (A5), so only the
    IVS is: an artisan's 4.513,92 halves to 2.256,96 and 2.686,08 to 1.343,04."""
    artisan = INPS_2026.contributions(
        "artigiani_commercianti", D("30000"), trade="artigiani", over_65_pensioner=True
    )
    assert (artisan.fixed, artisan.percentage) == (D("2264.40"), D("1343.04"))
    # A trader keeps the 0,48 (18.808 x 0,48% = 90,28 on the minimum, 144,00 - 90,28 = 53,72
    # on the excess) and the maternity: 4.604,20 - 90,28 = 4.513,92 halves to 2.256,96.
    trader = INPS_2026.contributions(
        "artigiani_commercianti", D("30000"), trade="commercianti", over_65_pensioner=True
    )
    assert (trader.fixed, trader.percentage) == (D("2354.68"), D("1396.76"))


def test_a_first_enrolment_halves_the_ivs_like_the_over_65_reduction_does() -> None:
    """Circolare 83/2025: «sola aliquota IVS»; maternity and a trader's 0,48 stay in full."""
    enrolled = INPS_2026.contributions(
        "artigiani_commercianti",
        D("30000"),
        trade="commercianti",
        first_enrolment_month=date(2025, 3, 15),
    )
    assert (enrolled.fixed, enrolled.percentage) == (D("2354.68"), D("1396.76"))


@pytest.mark.parametrize(
    ("first_month", "year", "covered"),
    [
        # 36 months from January 2025 end in December 2027; from December 2025, in November 2028.
        (date(2025, 1, 1), 2026, True),
        (date(2025, 1, 1), 2027, True),
        (date(2025, 1, 1), 2028, False),
        (date(2025, 12, 1), 2027, True),
        (date(2025, 12, 1), 2028, False),
        # The law names enrolments of 2025 only.
        (date(2024, 12, 1), 2026, False),
        (date(2026, 1, 1), 2026, False),
    ],
)
def test_the_first_enrolment_reduction_covers_whole_years_of_the_36_months(
    first_month: date, year: int, covered: bool
) -> None:
    enrolment = INPS_2026.artisans_traders.new_enrolment
    assert enrolment is not None
    assert enrolment.covers(first_month, year) is covered


def test_a_first_enrolment_the_law_does_not_cover_is_refused() -> None:
    with pytest.raises(ValueError, match="does not cover 2026"):
        INPS_2026.contributions(
            "artigiani_commercianti",
            D("30000"),
            trade="artigiani",
            first_enrolment_month=date(2024, 6, 1),
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"flat_rate_reduction": True, "over_65_pensioner": True},
        {"flat_rate_reduction": True, "first_enrolment_month": date(2025, 5, 1)},
        {"over_65_pensioner": True, "first_enrolment_month": date(2025, 5, 1)},
    ],
)
def test_the_three_reductions_exclude_each_other(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="exclude each other"):
        INPS_2026.contributions(
            "artigiani_commercianti",
            D("30000"),
            trade="artigiani",
            **kwargs,  # type: ignore[arg-type]
        )


def test_the_gestione_separata_has_none_of_the_artisans_reductions() -> None:
    with pytest.raises(ValueError, match="belong"):
        INPS_2026.contributions("gestione_separata", D("1000"), over_65_pensioner=True)
    with pytest.raises(ValueError, match="belong"):
        INPS_2026.contributions(
            "gestione_separata", D("1000"), first_enrolment_month=date(2025, 1, 1)
        )


# --- the advances ---------------------------------------------------------------------------


def test_an_artisans_advances_are_the_part_above_the_minimum_in_two_equal_halves() -> None:
    """Redditi PF 2026 fascicolo 2, appendix «INPS»: two advances of equal amount, on the days of
    the income tax advances, from 2026's rates on the 2025 income above 2026's minimum:
    30.000 x 24% = 7.200,00 less 4.513,92 = 2.686,08."""
    advances = INPS_2026.advances("artigiani_commercianti", D("30000"), trade="artigiani")
    assert (advances.total, advances.first, advances.second) == (
        D("2686.08"),
        D("1343.04"),
        D("1343.04"),
    )
    assert (advances.first_due, advances.second_due) == (date(2026, 6, 30), date(2026, 11, 30))


def test_an_income_at_or_below_the_minimum_has_no_advance() -> None:
    advances = INPS_2026.advances("artigiani_commercianti", D("15000"), trade="artigiani")
    assert (advances.total, advances.first, advances.second) == (D("0.00"),) * 3


def test_the_advances_take_the_reductions_of_the_year() -> None:
    """«tenendo conto di eventuali agevolazioni spettanti per lo stesso anno»: the flat-rate
    regime's excess of 1.780,87 on 30.000 as a trader is paid 890,44 and 890,43 (the first
    advance takes the cent the halving rounds up)."""
    advances = INPS_2026.advances(
        "artigiani_commercianti", D("30000"), trade="commercianti", flat_rate_reduction=True
    )
    assert (advances.total, advances.first, advances.second) == (
        D("1780.87"),
        D("890.44"),
        D("890.43"),
    )
    over_65 = INPS_2026.advances(
        "artigiani_commercianti", D("30000"), trade="artigiani", over_65_pensioner=True
    )
    assert over_65.total == D("1343.04")


def test_the_gestione_separata_advances_are_the_rate_on_80_percent_of_the_income() -> None:
    """«applicando le aliquote previste per l'anno 2026 sull'80% del reddito di lavoro autonomo
    relativo all'anno 2025, tenendo conto del massimale»: 40.000 x 80% = 32.000 x 26,07%."""
    advances = INPS_2026.advances("gestione_separata", D("40000"))
    assert advances.base == D("32000")
    assert (advances.total, advances.first, advances.second) == (
        D("8342.40"),
        D("4171.20"),
        D("4171.20"),
    )
    covered = INPS_2026.advances("gestione_separata", D("40000"), covered_elsewhere=True)
    assert covered.total == D("7680.00")  # 32.000 x 24%


def test_the_gestione_separata_advances_stop_at_the_ceiling() -> None:
    # 122.295 x 80% = 97.836 x 26,07% = 25.505,8452; the first advance takes the odd cent.
    advances = INPS_2026.advances("gestione_separata", D("500000"))
    assert (advances.total, advances.first, advances.second) == (
        D("25505.85"),
        D("12752.93"),
        D("12752.92"),
    )


def test_the_advances_refuse_what_the_contributions_refuse() -> None:
    with pytest.raises(ValueError, match="gestione"):
        INPS_2026.advances("artigiani_commercianti", D("30000"))
    with pytest.raises(ValueError, match="belong"):
        INPS_2026.advances("gestione_separata", D("30000"), flat_rate_reduction=True)


def test_the_part_above_the_minimum_is_not_paid_in_instalments_with_the_minimum() -> None:
    assert INPS_2026.artisans_traders.fixed_part_in_instalments is False
    terms = INPS_2026.artisans_traders.terms
    assert terms.deferral_surcharge_percent == D("0.40")
    assert terms.deferral_until == date(2026, 7, 30)
    assert terms.instalments_end == date(2026, 12, 16)


@pytest.mark.parametrize("pack", [IT_STANDARD_PACK, IT_FLAT_RATE_PACK], ids=lambda pack: pack.id)
def test_the_dates_on_income_are_the_packs_income_tax_dates(pack: FiscalPack) -> None:
    """INPS's 30 June and 30 November are the days the pack's own calendar gives the income
    tax (or the flat-rate substitute tax)."""
    calendar = tax_calendar(pack, 2026)
    assert calendar is not None
    own = {
        entry.kind: entry.nominal_date
        for entry in calendar
        if entry.tax in {"income_tax", "substitute_tax"}
        and entry.kind in {"balance_and_first_advance", "second_advance"}
    }
    law_dates = {due.kind: due.due for due in INPS_2026.artisans_traders.terms.payments}
    assert law_dates == own


def test_a_category_needs_its_own_arguments() -> None:
    with pytest.raises(ValueError, match="gestione"):
        INPS_2026.contributions("artigiani_commercianti", D("1000"))
    with pytest.raises(ValueError, match="belong"):
        INPS_2026.contributions("gestione_separata", D("1000"), trade="artigiani")
    with pytest.raises(ValueError, match="gestione_separata"):
        INPS_2026.contributions(
            "artigiani_commercianti", D("1000"), trade="artigiani", covered_elsewhere=True
        )
    with pytest.raises(ValueError, match="belong"):
        INPS_2026.contributions("gestione_separata", D("1000"), flat_rate_reduction=True)
    with pytest.raises(ValueError, match="unknown gestione"):
        INPS_2026.contributions("artigiani_commercianti", D("1000"), trade="artisans")  # type: ignore[arg-type]
