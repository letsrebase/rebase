"""The flat-rate regime's law, keyed by tax year (REB-1066, taxes design § 1).

Law of 23 December 2014 n. 190 (L. 190/2014), art. 1 c. 54-89, read on Normattiva on 2026-11-11
(the text in force from 12-8-2026 and the earlier texts in force from 1-1-2015 and from
1-1-2016), with the tax agency's instructions for the 2026 individuals' income tax return (the
Redditi PF 2026 booklet 3, section LM). Each figure is a row of the A5 register of
`2026-11-09-space-profiles-modules-and-the-money-picture-design.md`, and carries its source in
the comment above it.

What the law says, in the order the estimate applies it:

1. The income is `revenue collected x profitability coefficient` (c. 64), the coefficient of the
   ATECO group of the activity (annex 4, below).
2. The compulsory social security contributions **paid** in the period are deducted from that
   income (c. 64: «si deducono dal reddito determinato ai sensi del presente comma»; the return
   deducts them in rows LM35 and LM36 before the substitute tax is computed in LM39).
3. The substitute tax is 15% of what is left (c. 64), or 5% for the start-up conditions of c. 65.
4. Advances follow the rules for the payment of personal income tax (c. 64, last sentence).

The contributions themselves are owed on the income of step 1, before the deduction of step 2
(c. 77 for the «artigiani e commercianti» regime; the return reports the gross flat-rate income
by pension fund in LM34, columns 1 and 2). The substitute tax is deducted from nothing: it is not
part of any base. `FlatRateLaw.contribution_base` and `substitute_tax_base` say so as data.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from fractions import Fraction
from typing import Literal

# What the contributions rate applies to. The flat-rate income of c. 64, before the contributions
# are deducted from it: a contribution cannot be based on a figure that its own deduction
# produces, and the substitute tax is not a deduction at all.
ContributionBase = Literal["flat_rate_income"]

# What the substitute tax rate applies to: the flat-rate income less the contributions deducted
# (c. 64; Agenzia, instructions of row LM36: «la differenza tra l'importo di rigo LM34, colonna
# 3, e l'importo di rigo LM35, colonna 2»).
SubstituteTaxBase = Literal["flat_rate_income_net_of_contributions"]

# The classification the annex's codes are written in. D.Lgs. 81/2025 art. 1 c. 1 (12 June 2025,
# Normattiva, read 2026-11-11) keeps ATECO 2007 until new coefficients are approved on ATECO
# 2025, and the Redditi PF 2026 instructions say the same. No approval of ATECO 2025
# coefficients was found on that date. The wording is `ATECO_EDITION_WORDING`.
AtecoEdition = Literal["ATECO 2007"]

ATECO_EDITION_WORDING = (
    "Fino alla approvazione dei nuovi coefficienti di redditività elaborati sulla base della "
    "classificazione delle attività economiche ATECO 2025, i soggetti di cui all'articolo 1, "
    "comma 54, della legge 23 dicembre 2014, n. 190, continuano a determinare il reddito "
    "imponibile applicando all'ammontare dei ricavi o dei compensi percepiti il coefficiente di "
    "redditività previsto nell'allegato 4 alla citata legge n. 190 del 2014, individuato sulla "
    "base del codice corrispondente all'attività esercitata secondo la classificazione ATECO "
    "2007 (D.Lgs. 12 giugno 2025 n. 81, art. 1 c. 1)"
)


@dataclass(frozen=True)
class ProfitabilityGroup:
    """One row of annex 4 of L. 190/2014: a group of activities and its coefficient.

    `ateco_2007` lists the codes the annex names for the group, each as the annex writes it:
    a two-digit division («10»), or a range of divisions' groups or classes written with a
    hyphen («46.2-46.9», the annex's «da 46.2 a 46.9»). `name` is the annex's own group name."""

    number: int
    key: str
    name: str
    ateco_2007: tuple[str, ...]
    coefficient: Decimal


def _divisions(codes: str) -> tuple[str, ...]:
    return tuple(codes.split())


# Annex 4 «(articolo 1, comma 64) (Regime forfetario dei contribuenti minimi)», in force from 1
# January 2019 (L. 30 dicembre 2018 n. 145, which replaced the table; the Agenzia's instructions
# call it its «allegato 2»). Normattiva, L. 190/2014 «Allegato 4», text in force from 1-1-2019,
# read 2026-11-11. The coefficients are the same as in every earlier text of the annex (the
# texts in force from 1-1-2015 and from 1-1-2016 differ in the turnover thresholds only, which
# the 2019 text dropped), so one table serves every year of the regime.
ANNEX_4: tuple[ProfitabilityGroup, ...] = (
    ProfitabilityGroup(
        1,
        "food_and_beverage_industries",
        "Industrie alimentari e delle bevande",
        _divisions("10 11"),
        Decimal("40"),
    ),
    ProfitabilityGroup(
        2,
        "wholesale_and_retail_trade",
        "Commercio all'ingrosso e al dettaglio",
        ("45", "46.2-46.9", "47.1-47.7", "47.9"),
        Decimal("40"),
    ),
    ProfitabilityGroup(
        3,
        "street_trade_in_food_and_beverages",
        "Commercio ambulante di prodotti alimentari e bevande",
        ("47.81",),
        Decimal("40"),
    ),
    ProfitabilityGroup(
        4,
        "street_trade_in_other_products",
        "Commercio ambulante di altri prodotti",
        ("47.82", "47.89"),
        Decimal("54"),
    ),
    ProfitabilityGroup(
        5,
        "construction_and_real_estate",
        "Costruzioni e attività immobiliari",
        _divisions("41 42 43 68"),
        Decimal("86"),
    ),
    ProfitabilityGroup(
        6,
        "trade_intermediaries",
        "Intermediari del commercio",
        ("46.1",),
        Decimal("62"),
    ),
    ProfitabilityGroup(
        7,
        "accommodation_and_food_service",
        "Attività dei servizi di alloggio e di ristorazione",
        _divisions("55 56"),
        Decimal("40"),
    ),
    ProfitabilityGroup(
        8,
        "professional_scientific_technical_health_education_finance_and_insurance",
        "Attività professionali, scientifiche, tecniche, sanitarie, di istruzione, servizi "
        "finanziari e assicurativi",
        _divisions("64 65 66 69 70 71 72 73 74 75 85 86 87 88"),
        Decimal("78"),
    ),
    ProfitabilityGroup(
        9,
        "other_economic_activities",
        "Altre attività economiche",
        _divisions(
            "01 02 03 05 06 07 08 09 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 "
            "31 32 33 35 36 37 38 39 49 50 51 52 53 58 59 60 61 62 63 77 78 79 80 81 82 84 90 "
            "91 92 93 94 95 96 97 98 99"
        ),
        Decimal("67"),
    ),
)


@dataclass(frozen=True)
class FlatRateAdvanceLaw:
    """How the substitute tax is paid in advance, from tax year 2026 (the page below was read in
    2026; no earlier year's rule is stated here).

    * The payment rules are those of personal income tax: c. 64, last sentence, «Si applicano le
      disposizioni in materia di versamento dell'imposta sui redditi delle persone fisiche».
      That is why the base (100% of the tax declared for the previous year) and the 51.65
      threshold below are the ones of Agenzia delle Entrate, «Come si paga l'Irpef» (updated
      12-3-2026): «L'acconto Irpef è dovuto se l'imposta dichiarata [...] è superiore a 51,65
      euro [...] L'acconto è pari al 100% dell'imposta dichiarata». The page does not repeat
      them for the substitute tax; the statute's reference does.
    * What the same page says of the flat-rate regime itself: «I soggetti che devono applicare
      gli Indici sintetici di affidabilità (Isa) e i soggetti che rientrano nel regime
      forfetario effettuano un unico versamento entro il 30 novembre se l'importo totale dovuto
      non supera 206 euro; l'acconto in due rate di pari importo entro le stesse scadenze
      previste per gli altri contribuenti (30 giugno e 30 novembre)». The 50% and 50% come from
      art. 58 D.L. 26 ottobre 2019 n. 124 (in force from 27 October 2019, Normattiva, read
      2026-11-11: «in due rate ciascuna nella misura del 50 per cento»), which names these
      taxpayers through art. 12-quinquies c. 3 and 4 D.L. 34/2019.
    * Paying within 30 days after a deadline adds 0.40%: the same page, «oppure entro i
      successivi 30 giorni pagando una maggiorazione dello 0,40%».

    The instalment plan of the June balance and first advance is not here: the sources read do
    not state it for the substitute tax (A5, to verify, REB-975). The dates are the pack's
    calendar (`TaxCalendarLaw`).
    """

    advance_percent_of_base: Decimal
    first_advance_percent: Decimal
    second_advance_percent: Decimal
    # No advance is due at or below this amount.
    no_advance_at_or_below: Decimal
    # When the total advance does not exceed this, it is paid once, by 30 November.
    single_november_payment_at_or_below: Decimal
    deferral_surcharge_percent: Decimal
    deferral_window_days: int


@dataclass(frozen=True)
class FlatRateLaw:
    """The law of the flat-rate regime for the tax years from the `TaxYearLaw` that holds it.

    `coefficients` is annex 4; `contribution_base` and `substitute_tax_base` say what each rate
    applies to (see the module's text). The start-up relief of c. 65 differs by year: from tax
    year 2016 the substitute tax rate is `startup_rate_percent` instead of `standard_rate_percent`
    for the year the activity starts and the `startup_following_years` after it; for 2015 the
    law instead reduced the income by `startup_income_reduction` for the year of start and the
    two after it, at the ordinary rate. `None` on a field means the year's law has no such rule.

    The estimate reads `contribution_base`, `substitute_tax_base` and the annex's coefficients.
    The rest is data for the cards that follow: the substitute rate it applies is the profile's
    typed one, so nothing here applies the start-up relief yet, and the advances are not
    computed (REB-1066 puts the law in the pack and builds no estimate on it).
    """

    coefficients: tuple[ProfitabilityGroup, ...]
    ateco_edition: AtecoEdition
    contribution_base: ContributionBase
    substitute_tax_base: SubstituteTaxBase
    # L. 190/2014 art. 1 c. 64: «un'imposta sostitutiva dell'imposta sui redditi, delle
    # addizionali regionali e comunali e dell'imposta regionale sulle attività produttive [...]
    # pari al 15 per cento».
    standard_rate_percent: Decimal
    # c. 65, from tax year 2016 (art. 1 c. 111 L. 28 dicembre 2015 n. 208; Normattiva text in
    # force from 1-1-2016): «per il periodo d'imposta in cui l'attività è iniziata e per i
    # quattro successivi, l'aliquota di cui al comma 64 è stabilita nella misura del 5 per
    # cento»; the Agenzia's 2026 instructions state it as in force today.
    startup_rate_percent: Decimal | None
    # c. 65 as first enacted, tax year 2015 only (Normattiva text in force 1-1-2015 to
    # 31-12-2015): «per il periodo d'imposta in cui l'attività è iniziata e per i due
    # successivi, il reddito determinato ai sensi del comma 64 è ridotto di un terzo».
    startup_income_reduction: Fraction | None
    startup_following_years: int
    # c. 65 lett. a), b) and c), the same wording in every text read, summarised in English.
    startup_conditions: tuple[str, ...]
    advances: FlatRateAdvanceLaw | None = None

    @property
    def coefficient_values(self) -> frozenset[Decimal]:
        """Every coefficient annex 4 contains."""
        return frozenset(group.coefficient for group in self.coefficients)

    def lists_coefficient(self, value: Decimal) -> bool:
        """Whether `value` (a percentage, `67.00` for 67%) is one of annex 4's coefficients."""
        return value in self.coefficient_values

    def group(self, key: str) -> ProfitabilityGroup:
        """The annex's group named `key`; `KeyError` for a key it has not."""
        for group in self.coefficients:
            if group.key == key:
                return group
        raise KeyError(key)


STARTUP_CONDITIONS: tuple[str, ...] = (
    "no artistic, professional or business activity, alone, associated or in a family "
    "business, in the three years before the activity started (c. 65 lett. a)",
    "the activity is in no way a mere continuation of an earlier one done as an employee or as "
    "self-employed, except where that was the compulsory practice for an art or a profession "
    "(c. 65 lett. b)",
    "when an activity run before by another person is continued, that activity's revenue in the "
    "tax period before the one the relief is recognised in does not exceed the limit of c. 54 "
    "(c. 65 lett. c)",
)

FLAT_RATE_2015 = FlatRateLaw(
    coefficients=ANNEX_4,
    ateco_edition="ATECO 2007",
    contribution_base="flat_rate_income",
    substitute_tax_base="flat_rate_income_net_of_contributions",
    standard_rate_percent=Decimal("15"),
    startup_rate_percent=None,
    startup_income_reduction=Fraction(1, 3),
    startup_following_years=2,
    startup_conditions=STARTUP_CONDITIONS,
)

FLAT_RATE_2016 = replace(
    FLAT_RATE_2015,
    startup_rate_percent=Decimal("5"),
    startup_income_reduction=None,
    startup_following_years=4,
)

FLAT_RATE_ADVANCES_2026 = FlatRateAdvanceLaw(
    advance_percent_of_base=Decimal("100"),
    first_advance_percent=Decimal("50"),
    second_advance_percent=Decimal("50"),
    no_advance_at_or_below=Decimal("51.65"),
    single_november_payment_at_or_below=Decimal("206"),
    deferral_surcharge_percent=Decimal("0.40"),
    deferral_window_days=30,
)

FLAT_RATE_2026 = replace(FLAT_RATE_2016, advances=FLAT_RATE_ADVANCES_2026)


__all__ = [
    "ANNEX_4",
    "ATECO_EDITION_WORDING",
    "FLAT_RATE_2015",
    "FLAT_RATE_2016",
    "FLAT_RATE_2026",
    "FLAT_RATE_ADVANCES_2026",
    "STARTUP_CONDITIONS",
    "AtecoEdition",
    "ContributionBase",
    "FlatRateAdvanceLaw",
    "FlatRateLaw",
    "ProfitabilityGroup",
    "SubstituteTaxBase",
]
