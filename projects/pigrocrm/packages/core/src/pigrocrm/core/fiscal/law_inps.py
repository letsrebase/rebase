"""INPS contributions of a person, keyed by contribution year (REB-1064, taxes design § 1).

The law of the contributions an Italian person pays to INPS for their own work: the
«gestione separata» (a professional with no pension fund) and the «gestioni speciali degli
artigiani e dei commercianti». It is separate from `TaxYearLaw` because the figures are
re-set every year by an INPS circular, so an entry holds for **its year only**: a year that
has no entry has no figure (`FiscalPack.inps_for` answers `None`), never the figure of the year
before. It is also why a frozen reading's `law_year` does not move when a year is added here.

Every figure carries the primary source it was read from; the frame's A5 register rows say the
same. Sources read on 2026-11-11:

* INPS, circolare n. 8 of 3 February 2026 (gestione separata: rates, ceiling, minimum);
* INPS, circolare n. 14 of 9 February 2026 (artigiani e commercianti: rates, minimum, ceilings,
  dates of the fixed part, the 35% reduction of the flat-rate regime);
* INPS, circolare n. 62 of 27 May 2026 (the terms of the part above the minimum, the 0.40%
  deferral and the instalments, for the balance of 2025 and the advances of 2026);
* INPS, circolare n. 29 of 10 February 2015 (how the flat-rate regime's reduced contribution is
  paid: no minimum, no fixed part), which circolare n. 14/2026 § 8 still refers to;
* legge 23 dicembre 2014, n. 190, art. 1 c. 77 (the 35% reduction), Normattiva text in force on
  11 November 2026.

What is not here is not verified (A5, REB-975): the split of the advances of these contributions,
the 50% reduction of a pensioner over 65 and of a first enrolment (the circular states them and
the person's age and history are not in the profile), the contribution of an enrolled partner of
an srl, and the figures of any other year.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

# Same values as `fiscal/schemas.py::InpsCategory` (a test holds the two together: this module
# is imported by the pack, which `schemas` imports).
InpsCategoryName = Literal["gestione_separata", "artigiani_commercianti"]
# The profile's `artigiani_commercianti` is one category and the two gestioni have different
# rates (24% against 24.48%, circolare 14/2026 § 3), so the lookup takes which one it is. The
# profile does not say yet: a later card adds the column (REB-1064's report says why).
TradeGestione = Literal["artigiani", "commercianti"]
# What a payment of the year settles: one of the four fixed instalments on the minimum income, or
# a payment dated like the income tax's (the balance of the year before with the first advance,
# the second advance).
InpsDueKind = Literal["fixed_instalment", "balance_and_first_advance", "second_advance"]

_CENT = Decimal("0.01")
_HUNDRED = Decimal(100)
_ZERO = Decimal("0.00")


def _cents(amount: Decimal) -> Decimal:
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def _percent_of(amount: Decimal, rate: Decimal) -> Decimal:
    return _cents(amount * rate / _HUNDRED)


@dataclass(frozen=True)
class InpsDue:
    """One date a contribution is paid. `covers` says in English what it settles."""

    kind: InpsDueKind
    due: date
    covers: str


@dataclass(frozen=True)
class PercentagePaymentTerms:
    """How the contribution on income (the part above the minimum for an artisan or a trader,
    the whole of the gestione separata's) is paid: with the income tax, on its days.

    Source: INPS circolare n. 62 of 27 May 2026, § 3 «Termini e modalità di versamento» and
    § 4 «La rateizzazione» (art. 2 D.L. 63/2002; art. 18 c. 4 D.Lgs. 241/1997; art. 8 D.Lgs.
    1/2024): «entro il 30 giugno 2026 [...] per i versamenti a saldo per l'anno di imposta 2025 e
    primo acconto per l'anno 2026 ed entro il 30 novembre 2026 per il secondo acconto 2026»;
    «qualora il contribuente intenda pagare quanto dovuto a titolo di saldo 2025 in forma rateale,
    il pagamento deve essere completato entro la data del 16 dicembre 2026»; «[...] nel periodo
    tra il 30 giugno 2026 e il 30 luglio 2026 [...] la maggiorazione dello 0,40%».
    """

    payments: tuple[InpsDue, ...]
    deferral_surcharge_percent: Decimal
    deferral_until: date
    instalments_end: date


@dataclass(frozen=True)
class GestioneSeparataLaw:
    """A professional with no fund, enrolled in the «gestione separata» (art. 2 c. 26 L.
    335/1995), with a VAT position.

    `rate` is the one for a person with no other mandatory insurance and no pension, `covered_rate`
    the one for a pensioner or a person insured elsewhere. The rate is a sum (`ivs_rate` +
    `additional_rate` + `iscro_rate`) and the sources print the parts, so the parts are data too.
    """

    rate: Decimal
    covered_rate: Decimal
    ivs_rate: Decimal
    additional_rate: Decimal
    iscro_rate: Decimal
    # The annual ceiling («massimale»): contributions are due on income up to it.
    ceiling: Decimal
    # Not a floor of what is owed: an income below it still pays on the income, and the year is
    # credited in full only by paying the contribution on this income (`crediting_*`).
    crediting_minimum_income: Decimal
    crediting_contribution: Decimal
    crediting_contribution_covered: Decimal
    terms: PercentagePaymentTerms


@dataclass(frozen=True)
class TradeRates:
    """The IVS rate of a gestione, in percent, up to the first band and above it."""

    up_to_first_band: Decimal
    above_first_band: Decimal


@dataclass(frozen=True)
class ArtisansTradersLaw:
    """The «gestioni speciali» of artisans and of traders (artigiani, commercianti).

    * `minimum_income` («minimale»): the contribution on it is owed whatever the income, in four
      fixed instalments (`fixed_instalments`), with the maternity contribution; the part of the
      income above it is paid with the income tax (`terms`).
    * Rates apply up to `first_band_limit` and the higher one above it. The «massimale» is
      `first_band_limit` plus two thirds of it for a person already insured on 31 December 1995
      (`ceiling_with_contributions_before_1996`), `ceiling` for everyone else.
    * `flat_rate_reduction_percent`: the flat-rate regime's reduced contribution (art. 1 c. 77
      L. 190/2014), by request. It is owed on the flat-rate income with **no minimum and no fixed
      part**, paid at the income tax's dates, with the maternity contribution in two equal
      shares at the advances (circolare 29/2015 § 1; still referred to by circolare 14/2026 § 8).
    """

    minimum_income: Decimal
    first_band_limit: Decimal
    ceiling_with_contributions_before_1996: Decimal
    ceiling: Decimal
    maternity_per_month: Decimal
    artisans: TradeRates
    traders: TradeRates
    fixed_instalments: tuple[InpsDue, ...]
    flat_rate_reduction_percent: Decimal
    # What is paid in instalments is only what is above the minimum, never the contribution on
    # the minimum (circolare 62/2026 § 4: «con esclusione [...] dei contributi dovuti sul
    # minimale»). The gestione separata's is paid in instalments in full.
    fixed_part_in_instalments: bool
    terms: PercentagePaymentTerms

    @property
    def maternity_per_year(self) -> Decimal:
        return _cents(self.maternity_per_month * 12)

    def rates(self, trade: TradeGestione) -> TradeRates:
        if trade == "artigiani":
            return self.artisans
        if trade == "commercianti":
            return self.traders
        raise ValueError(f"unknown gestione {trade!r}: artigiani or commercianti")


@dataclass(frozen=True)
class InpsContributions:
    """What a person owes INPS for `year` on an income, and when.

    * `fixed`: owed whatever the income, in four fixed instalments (the contribution on the
      minimum income and the maternity contribution); zero for the gestione separata and for the
      flat-rate regime's reduced contribution.
    * `percentage`: owed on the income, paid with the income tax; for an artisan or a trader the
      part above the minimum, for the gestione separata the whole contribution, for the reduced
      flat-rate regime the whole reduced contribution with the maternity contribution.
    * `base`: the income the rate applies to, after the ceiling.
    * `rate`: the percent applied up to the first band (the gestione separata's single rate).
    * `dues`: the dates; the dates of the part on income are those of the income tax and appear
      only when `percentage` is above zero. They are the dates of the year's circular: the balance
      of the year itself is paid with the income tax of the year after, which the circular of the
      year after states, so it is not here.
    """

    year: int
    category: InpsCategoryName
    income: Decimal
    base: Decimal
    rate: Decimal
    fixed: Decimal
    percentage: Decimal
    dues: tuple[InpsDue, ...]

    @property
    def total(self) -> Decimal:
        return self.fixed + self.percentage


@dataclass(frozen=True)
class InpsYearLaw:
    """The INPS law of one contribution year (`FiscalPack.inps_for`)."""

    year: int
    gestione_separata: GestioneSeparataLaw
    artisans_traders: ArtisansTradersLaw
    # The circulars the year's figures were read from, for the estimate that shows what it was
    # computed from.
    source: str = ""

    def contributions(
        self,
        category: InpsCategoryName,
        income: Decimal,
        *,
        trade: TradeGestione | None = None,
        covered_elsewhere: bool = False,
        contributions_before_1996: bool = False,
        flat_rate_reduction: bool = False,
    ) -> InpsContributions:
        """The contributions of `category` on `income` (the income the contributions are based
        on; a loss counts as zero).

        * `trade` is required for `artigiani_commercianti`.
        * `covered_elsewhere` is the gestione separata's lower rate: a pensioner, or a person
          insured elsewhere.
        * `contributions_before_1996` is the artisan's or trader's older ceiling, for a person
          with contributions on 31 December 1995.
        * `flat_rate_reduction` is the 35% reduction of an artisan or a trader in the flat-rate
          regime who applied for it; it does not exist in the gestione separata.
        """
        income = max(income, Decimal(0))
        if category == "gestione_separata":
            if trade is not None or flat_rate_reduction or contributions_before_1996:
                raise ValueError(
                    "trade, contributions_before_1996 and flat_rate_reduction belong to "
                    "artigiani_commercianti"
                )
            return self._gestione_separata(income, covered_elsewhere)
        if category == "artigiani_commercianti":
            if trade is None:
                raise ValueError(
                    "artigiani_commercianti needs the gestione: artigiani or commercianti"
                )
            if covered_elsewhere:
                raise ValueError("covered_elsewhere belongs to gestione_separata")
            return self._artisans_traders(
                income, trade, contributions_before_1996, flat_rate_reduction
            )
        raise ValueError(f"unknown INPS category {category!r}")

    def _gestione_separata(self, income: Decimal, covered_elsewhere: bool) -> InpsContributions:
        law = self.gestione_separata
        rate = law.covered_rate if covered_elsewhere else law.rate
        base = min(income, law.ceiling)
        amount = _percent_of(base, rate)
        return InpsContributions(
            year=self.year,
            category="gestione_separata",
            income=income,
            base=base,
            rate=rate,
            fixed=_ZERO,
            percentage=amount,
            dues=law.terms.payments if amount > 0 else (),
        )

    def _artisans_traders(
        self, income: Decimal, trade: TradeGestione, before_1996: bool, reduced: bool
    ) -> InpsContributions:
        law = self.artisans_traders
        rates = law.rates(trade)
        ceiling = law.ceiling_with_contributions_before_1996 if before_1996 else law.ceiling
        base = min(income, ceiling)

        def ivs(amount: Decimal) -> Decimal:
            # INPS rounds each band to the cent (circolare 14/2026 § 4: a trader's 23,314.31 is
            # 13,763.64 + 9,550.67, where the unrounded sum is 23,314.3036).
            first = min(amount, law.first_band_limit)
            above = max(amount - law.first_band_limit, Decimal(0))
            return _percent_of(first, rates.up_to_first_band) + _percent_of(
                above, rates.above_first_band
            )

        if reduced:
            # Art. 1 c. 77 L. 190/2014: «la contribuzione dovuta ai fini previdenziali, ridotta
            # del 35 per cento», on the flat-rate income, with no minimum (circolare 29/2015 § 1).
            # The circular gives no worked example, so the reduction is taken on the whole
            # contribution due, once, rounded to the cent (A5, REB-975).
            kept = _HUNDRED - law.flat_rate_reduction_percent
            amount = _cents(ivs(base) * kept / _HUNDRED) + law.maternity_per_year
            return InpsContributions(
                year=self.year,
                category="artigiani_commercianti",
                income=income,
                base=base,
                rate=rates.up_to_first_band * kept / _HUNDRED,
                fixed=_ZERO,
                percentage=amount,
                dues=law.terms.payments,
            )

        on_minimum = ivs(law.minimum_income)
        fixed = on_minimum + law.maternity_per_year
        percentage = max(ivs(max(base, law.minimum_income)) - on_minimum, _ZERO)
        return InpsContributions(
            year=self.year,
            category="artigiani_commercianti",
            income=income,
            base=base,
            rate=rates.up_to_first_band,
            fixed=fixed,
            percentage=percentage,
            dues=law.fixed_instalments + (law.terms.payments if percentage > 0 else ()),
        )


# --- 2026 ---------------------------------------------------------------------------------
# The sources are in the module docstring; each figure names its own below.

_TERMS_2026 = PercentagePaymentTerms(
    payments=(
        InpsDue(
            kind="balance_and_first_advance",
            due=date(2026, 6, 30),
            covers="the balance of 2025 and the first advance of 2026",
        ),
        InpsDue(kind="second_advance", due=date(2026, 11, 30), covers="the second advance of 2026"),
    ),
    # Circolare 62/2026 § 3: 30 June 2026 to 30 July 2026 with the 0.40% surcharge.
    deferral_surcharge_percent=Decimal("0.40"),
    deferral_until=date(2026, 7, 30),
    # Circolare 62/2026 § 3: the instalments of the 2025 balance end on 16 December 2026.
    instalments_end=date(2026, 12, 16),
)

INPS_2026 = InpsYearLaw(
    year=2026,
    source=(
        "INPS, circolare n. 8 of 3 February 2026 (gestione separata) and circolare n. 14 of 9 "
        "February 2026 (artigiani e commercianti), read 2026-11-11"
    ),
    gestione_separata=GestioneSeparataLaw(
        # Circolare 8/2026 § 2: «26,07% (25,00 IVS + 0,72 aliquota aggiuntiva + 0,35 ISCRO)» for
        # «soggetti non assicurati presso altra forma di previdenza obbligatoria»; «24%» for
        # «pensionati o assicurati presso altre forme di previdenza obbligatorie» (art. 1 c. 79
        # L. 247/2007; art. 1 c. 165 L. 232/2016; art. 1 c. 154 L. 213/2023).
        rate=Decimal("26.07"),
        covered_rate=Decimal("24.00"),
        ivs_rate=Decimal("25.00"),
        additional_rate=Decimal("0.72"),
        iscro_rate=Decimal("0.35"),
        # Circolare 8/2026 § 6.1: «il massimale di reddito previsto dall'articolo 2, comma 18,
        # della legge n. 335/1995, è pari a 122.295,00 euro».
        ceiling=Decimal("122295.00"),
        # Circolare 8/2026 § 6.2 «Minimale. Accredito contributivo»: 18.808,00 euro; «gli
        # iscritti per i quali è applicata l'aliquota del 24% avranno l'accredito dell'intero
        # anno con un contributo annuo di 4.513,92 euro»; «4.903,25 euro (di cui 4.702,00 euro ai
        # fini pensionistici) per i professionisti che applicano l'aliquota del 26,07%».
        crediting_minimum_income=Decimal("18808.00"),
        crediting_contribution=Decimal("4903.25"),
        crediting_contribution_covered=Decimal("4513.92"),
        terms=_TERMS_2026,
    ),
    artisans_traders=ArtisansTradersLaw(
        # Circolare 14/2026 § 2: «il reddito minimo annuo [...] è pari a 18.808,00 euro» (the
        # daily minimum wage of 58,13 euro times 312, plus 671,39: art. 1 c. 3 L. 233/1990 and
        # art. 6 L. 415/1991; ISTAT +1.4%).
        minimum_income=Decimal("18808.00"),
        # § 3: «fino al limite della prima fascia di retribuzione annua pensionabile pari, per il
        # corrente anno, all'importo di 56.224,00 euro».
        first_band_limit=Decimal("56224.00"),
        # § 4: «il massimale di reddito annuo [...] è pari a 93.707,00 euro (56.224,00 euro più
        # 37.483,00 euro)» for who was insured by 1 January 1996; «122.295,00 euro» (art. 2 c. 18
        # L. 335/1995) for «lavoratori privi di anzianità contributiva al 31 dicembre 1995».
        ceiling_with_contributions_before_1996=Decimal("93707.00"),
        ceiling=Decimal("122295.00"),
        # § 1: «un contributo per le prestazioni di maternità stabilito [...] nella misura di
        # 0,62 euro mensili» (art. 49 c. 1 L. 488/1999).
        maternity_per_month=Decimal("0.62"),
        # § 3 table: artigiani 24% up to 56.224,00 and 25% above; commercianti 24,48% and 25,48%
        # (art. 24 c. 22 D.L. 201/2011; art. 3-ter D.L. 384/1992; the traders' 0,48 is art. 1
        # c. 380 L. 178/2020).
        artisans=TradeRates(Decimal("24.00"), Decimal("25.00")),
        traders=TradeRates(Decimal("24.48"), Decimal("25.48")),
        # § 9: «18 maggio 2026, 20 agosto 2026, 16 novembre 2026 e 16 febbraio 2027, per il
        # versamento delle quattro rate dei contributi dovuti sul minimale di reddito».
        fixed_instalments=(
            InpsDue(
                kind="fixed_instalment",
                due=date(2026, 5, 18),
                covers="the first instalment of the contribution on the minimum income",
            ),
            InpsDue(
                kind="fixed_instalment",
                due=date(2026, 8, 20),
                covers="the second instalment of the contribution on the minimum income",
            ),
            InpsDue(
                kind="fixed_instalment",
                due=date(2026, 11, 16),
                covers="the third instalment of the contribution on the minimum income",
            ),
            InpsDue(
                kind="fixed_instalment",
                due=date(2027, 2, 16),
                covers="the fourth instalment of the contribution on the minimum income",
            ),
        ),
        # § 8: «il regime [...] consiste nella riduzione contributiva del 35%» (art. 1 c. 77 L.
        # 190/2014), applied in 2026 to who had it in 2025 and has not given it up.
        flat_rate_reduction_percent=Decimal("35"),
        fixed_part_in_instalments=False,
        terms=_TERMS_2026,
    ),
)

__all__ = [
    "INPS_2026",
    "ArtisansTradersLaw",
    "GestioneSeparataLaw",
    "InpsCategoryName",
    "InpsContributions",
    "InpsDue",
    "InpsDueKind",
    "InpsYearLaw",
    "PercentagePaymentTerms",
    "TradeGestione",
    "TradeRates",
]
