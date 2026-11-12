"""INPS contributions of a person, keyed by contribution year (REB-1064, taxes design § 1).

The law of the contributions an Italian person pays to INPS for their own work: the
«gestione separata» (a professional with no pension fund) and the «gestioni speciali degli
artigiani e dei commercianti». It is separate from `TaxYearLaw` because the figures are
re-set every year by an INPS circular, so an entry holds for **its year only**: a year that
has no entry has no figure (`FiscalPack.inps_for` answers `None`), never the figure of the year
before. It is also why a frozen reading's `law_year` does not move when a year is added here.

Every figure carries the primary source it was read from; the frame's A5 register rows say the
same. Sources read on 2026-11-11 and 2026-11-12:

* INPS, circolare n. 8 of 3 February 2026 (gestione separata: rates, ceiling, minimum);
* INPS, circolare n. 14 of 9 February 2026 (artigiani e commercianti: rates, minimum, ceilings,
  dates of the fixed part, the reductions it still names);
* INPS, circolare n. 62 of 27 May 2026 (the terms of the part above the minimum, the 0.40%
  deferral and the instalments, for the balance of 2025 and the advances of 2026);
* INPS, circolare n. 35 of 19 February 2016, § 1 (the flat-rate regime's 35% reduction applies to
  the contribution on the minimum income and on the excess: the fixed part exists, reduced, with
  the maternity contribution in its four instalments), which replaced the rule of circolare n. 29
  of 10 February 2015 and refers to it for the rest;
* legge 23 dicembre 2014, n. 190, art. 1 c. 77 (the 35% reduction), c. 79 (the terms), c. 80 (no
  over-65 reduction in the flat-rate regime), Normattiva text in force on 12 November 2026;
* Agenzia delle Entrate, Redditi PF 2026 fascicolo 2, appendix «INPS - Modalità di calcolo degli
  acconti» (the advances), read 2026-11-12;
* legge 27 dicembre 1997, n. 449, art. 59 c. 15 (the 50% reduction of a pensioner over 65) and
  legge 30 dicembre 2024, n. 207, art. 1 c. 186 (the 50% reduction of the IVS rate for who
  enrols for the first time in 2025), as INPS circolare n. 14/2026 § 1, circolare n. 83 of 24
  April 2025 and the news of 28 April 2025 apply them.

What is not here is not verified (A5, REB-975): the cent rounding of a reduced instalment, the
over-65 halving of the traders' 0.48 point and of the maternity contribution, the contribution of
an enrolled partner of an srl, the figures of any other year.
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
    # The share of the previous year's income the two equal advances are computed on: the
    # Agenzia's Redditi PF 2026 fascicolo 2 appendix on the INPS advances says the year's rate is
    # applied to 80% of that income, within the year's ceiling (`InpsYearLaw.advances`).
    advance_base_percent: Decimal


@dataclass(frozen=True)
class TradeRates:
    """The contribution rate of a gestione, in percent, up to the first band and above it.

    `indemnity_add_on` is the part of both rates that is not IVS: the traders' 0.48 for the
    indemnity on the cessation of the activity (art. 1 c. 380 L. 178/2020), which the 50%
    reductions of the IVS rate leave in full (INPS news of 28 April 2025).
    """

    up_to_first_band: Decimal
    above_first_band: Decimal
    indemnity_add_on: Decimal


@dataclass(frozen=True)
class NewEnrolmentReduction:
    """The 50% reduction of the IVS rate for who enrols for the first time in `enrolment_year`
    (art. 1 c. 186 L. 207/2024), for `months` months from the start of the activity.

    Circolare 14/2026 § 1 and circolare 83/2025 §§ 1, 3, 5: it covers the contribution on the
    minimum and on the excess, on the IVS rate only (the maternity contribution and the traders'
    indemnity add-on stay due in full), on request, and cannot be combined with the flat-rate
    35% or with the over-65 reduction.
    """

    percent: Decimal
    enrolment_year: int
    months: int

    def covers(self, first_month: date, year: int) -> bool:
        """Whether an enrolment in `first_month` (a date in the month) is one the law names and
        every month of `year` falls within its `months` months."""
        if first_month.year != self.enrolment_year:
            return False
        start = first_month.year * 12 + first_month.month - 1
        return year * 12 >= start and year * 12 + 11 < start + self.months


@dataclass(frozen=True)
class ArtisansTradersLaw:
    """The «gestioni speciali» of artisans and of traders (artigiani, commercianti).

    * `minimum_income` («minimale»): the contribution on it is owed whatever the income, in four
      fixed instalments (`fixed_instalments`), with the maternity contribution; the part of the
      income above it is paid with the income tax (`terms`), as two advances of equal amount
      and a balance (`InpsYearLaw.advances`).
    * Rates apply up to `first_band_limit` and the higher one above it. The «massimale» is
      `first_band_limit` plus two thirds of it for a person already insured on 31 December 1995
      (`ceiling_with_contributions_before_1996`), `ceiling` for everyone else.
    * `flat_rate_reduction_percent`: the flat-rate regime's reduction of the contribution due
      (art. 1 c. 77 L. 190/2014), by request. It applies to the contribution on the minimum
      income and on the excess alike, so the fixed part exists, reduced, in its four instalments
      with the maternity contribution (circolare 35/2016 § 1, which replaced circolare 29/2015
      on this).
    * `over_65_pensioner_reduction_percent`: art. 59 c. 15 L. 449/1997, on request, for who is
      over 65 and already an INPS pensioner; on the minimum and on the excess.
    * `new_enrolment`: art. 1 c. 186 L. 207/2024, where the year's law covers it.
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
    over_65_pensioner_reduction_percent: Decimal
    new_enrolment: NewEnrolmentReduction | None
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
      minimum income, reduced where a reduction applies, and the maternity contribution, which
      no reduction touches); zero for the gestione separata.
    * `percentage`: owed on the income, paid with the income tax; for an artisan or a trader the
      part above the minimum (reduced where a reduction applies, the flat-rate regime's 35%
      included: circolare 35/2016 § 1), for the gestione separata the whole contribution.
    * `base`: the income the rate applies to, after the ceiling.
    * `rate`: the percent applied up to the first band (the gestione separata's single rate),
      after the reduction.
    * `dues`: the dates; the dates of the part on income are those of the income tax and appear
      only when `percentage` is above zero. They are the dates of the year's circular: the balance
      of the year itself is due with the income tax of the year after (D.P.R. 435/2001 art. 17
      c. 1; INPS circolare 14/2026 § 5), on the dates the circular of the year after states, so
      it is not here.
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
class InpsAdvances:
    """The two advances of a year's contributions (`InpsYearLaw.advances`).

    `base` is the income the rates were applied to, after the ceiling (and, for the gestione
    separata, after the share of it the advances are computed on); `total` is `first` plus
    `second`, due on `first_due` (with the balance of the year before) and `second_due`.
    """

    year: int
    category: InpsCategoryName
    previous_income: Decimal
    base: Decimal
    total: Decimal
    first: Decimal
    second: Decimal
    first_due: date
    second_due: date


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
        over_65_pensioner: bool = False,
        first_enrolment_month: date | None = None,
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
        * `over_65_pensioner` is the 50% reduction INPS granted to an artisan or a trader over 65
          who is already an INPS pensioner (art. 59 c. 15 L. 449/1997). It is the fact that INPS
          granted it, never inferred from the age; the flat-rate regime excludes it (art. 1 c. 80
          L. 190/2014).
        * `first_enrolment_month` is the month of the first enrolment of an artisan or a trader
          INPS granted the 50% reduction of the IVS rate (art. 1 c. 186 L. 207/2024); the year's
          law must cover the whole year from that month on (`NewEnrolmentReduction.covers`), or
          the lookup refuses it. The three reductions exclude each other.
        """
        income = max(income, Decimal(0))
        if category == "gestione_separata":
            if (
                trade is not None
                or flat_rate_reduction
                or contributions_before_1996
                or over_65_pensioner
                or first_enrolment_month is not None
            ):
                raise ValueError(
                    "trade, contributions_before_1996, flat_rate_reduction, over_65_pensioner "
                    "and first_enrolment_month belong to artigiani_commercianti"
                )
            return self._gestione_separata(income, covered_elsewhere)
        if category == "artigiani_commercianti":
            if trade is None:
                raise ValueError(
                    "artigiani_commercianti needs the gestione: artigiani or commercianti"
                )
            if covered_elsewhere:
                raise ValueError("covered_elsewhere belongs to gestione_separata")
            reduction = self._reduction(
                flat_rate_reduction, over_65_pensioner, first_enrolment_month
            )
            return self._artisans_traders(income, trade, contributions_before_1996, reduction)
        raise ValueError(f"unknown INPS category {category!r}")

    def advances(
        self,
        category: InpsCategoryName,
        previous_income: Decimal,
        *,
        trade: TradeGestione | None = None,
        covered_elsewhere: bool = False,
        contributions_before_1996: bool = False,
        flat_rate_reduction: bool = False,
        over_65_pensioner: bool = False,
        first_enrolment_month: date | None = None,
    ) -> InpsAdvances:
        """The two advances of this year's contributions, from the income of the year before
        (`previous_income`, a loss counting as zero), with the arguments of `contributions`.

        Redditi PF 2026 fascicolo 2, appendix «INPS - Modalità di calcolo degli acconti»: two
        advances of equal amount, on the days of the income tax advances of the same year. For
        an artisan or a trader the total is this year's rates on the previous year's income above
        this year's minimum (this year's ceiling), «tenendo conto di eventuali agevolazioni
        spettanti per lo stesso anno»: it is the part of `contributions` above the minimum, the
        fixed part being paid in its four instalments. For the gestione separata it is this
        year's rate on `advance_base_percent` of the previous year's income within this year's
        ceiling. The first advance is rounded to the cent and the second takes the rest, as the
        income tax's advances do.
        """
        previous_income = max(previous_income, Decimal(0))
        # The arguments' pairings are the lookup's own, so a refused combination is refused here
        # with the same sentence.
        contributions = self.contributions(
            category,
            previous_income,
            trade=trade,
            covered_elsewhere=covered_elsewhere,
            contributions_before_1996=contributions_before_1996,
            flat_rate_reduction=flat_rate_reduction,
            over_65_pensioner=over_65_pensioner,
            first_enrolment_month=first_enrolment_month,
        )
        if category == "gestione_separata":
            law = self.gestione_separata
            base = contributions.base * law.advance_base_percent / _HUNDRED
            total = _percent_of(base, contributions.rate)
            terms = law.terms
        else:
            base = contributions.base
            total = contributions.percentage
            terms = self.artisans_traders.terms
        first = _cents(total / 2)
        return InpsAdvances(
            year=self.year,
            category=category,
            previous_income=previous_income,
            base=base,
            total=total,
            first=first,
            second=total - first,
            first_due=_payment_of(terms, "balance_and_first_advance"),
            second_due=_payment_of(terms, "second_advance"),
        )

    def _reduction(
        self, flat_rate: bool, over_65: bool, first_enrolment_month: date | None
    ) -> _Reduction | None:
        """The one reduction in force, refusing two at once."""
        law = self.artisans_traders
        chosen = [
            name
            for name, stated in (
                ("flat_rate_reduction", flat_rate),
                ("over_65_pensioner", over_65),
                ("first_enrolment_month", first_enrolment_month is not None),
            )
            if stated
        ]
        if len(chosen) > 1:
            raise ValueError(
                f"{' and '.join(chosen)} exclude each other: art. 1 c. 80 L. 190/2014 keeps the "
                "over-65 reduction out of the flat-rate regime, and INPS makes the first-enrolment "
                "reduction incompatible with the flat-rate regime and with the over-65 one"
            )
        if flat_rate:
            # Art. 1 c. 77 L. 190/2014: «la contribuzione dovuta ai fini previdenziali, ridotta
            # del 35 per cento»; circolare 35/2016 § 1: on the contribution on the minimum and
            # on the excess, the traders' 0.48 included (it is part of the contribution due).
            return _Reduction(law.flat_rate_reduction_percent, whole_contribution=True)
        if over_65:
            # Art. 59 c. 15 L. 449/1997: «il contributo previdenziale» halved, on the minimum and
            # on the excess. Whether the 0.48 and the maternity are halved is not printed
            # anywhere (A5, to verify): only the IVS is halved here.
            return _Reduction(law.over_65_pensioner_reduction_percent, whole_contribution=False)
        if first_enrolment_month is not None:
            enrolment = law.new_enrolment
            if enrolment is None or not enrolment.covers(first_enrolment_month, self.year):
                raise ValueError(
                    f"the first-enrolment reduction does not cover {self.year} for an enrolment "
                    f"in {first_enrolment_month:%Y-%m}"
                )
            # Circolare 83/2025: «sola aliquota IVS»; the maternity contribution and the traders'
            # 0.48 stay due in full.
            return _Reduction(enrolment.percent, whole_contribution=False)
        return None

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
        self,
        income: Decimal,
        trade: TradeGestione,
        before_1996: bool,
        reduction: _Reduction | None,
    ) -> InpsContributions:
        law = self.artisans_traders
        rates = law.rates(trade)
        ceiling = law.ceiling_with_contributions_before_1996 if before_1996 else law.ceiling
        base = min(income, ceiling)

        def band(amount: Decimal, up_to_rate: Decimal, above_rate: Decimal) -> Decimal:
            # INPS rounds each band to the cent (circolare 14/2026 § 4: a trader's 23,314.31 is
            # 13,763.64 + 9,550.67, where the unrounded sum is 23,314.3036).
            first = min(amount, law.first_band_limit)
            above = max(amount - law.first_band_limit, Decimal(0))
            return _percent_of(first, up_to_rate) + _percent_of(above, above_rate)

        def full(amount: Decimal) -> Decimal:
            return band(amount, rates.up_to_first_band, rates.above_first_band)

        def add_on(amount: Decimal) -> Decimal:
            return band(amount, rates.indemnity_add_on, rates.indemnity_add_on)

        def reduced(amount_full: Decimal, amount_add_on: Decimal) -> Decimal:
            """`amount_full` less the reduction: the whole of it, or its IVS part only (the
            indemnity add-on stays in full), rounded to the cent once (A5, to verify)."""
            if reduction is None:
                return amount_full
            kept = _HUNDRED - reduction.percent
            if reduction.whole_contribution:
                return _cents(amount_full * kept / _HUNDRED)
            return _cents((amount_full - amount_add_on) * kept / _HUNDRED) + amount_add_on

        minimum = law.minimum_income
        above_minimum = max(base, minimum)
        fixed = reduced(full(minimum), add_on(minimum)) + law.maternity_per_year
        percentage = reduced(
            max(full(above_minimum) - full(minimum), _ZERO),
            max(add_on(above_minimum) - add_on(minimum), _ZERO),
        )
        rate = rates.up_to_first_band
        if reduction is not None:
            kept = _HUNDRED - reduction.percent
            rate = (
                rate * kept / _HUNDRED
                if reduction.whole_contribution
                else (rate - rates.indemnity_add_on) * kept / _HUNDRED + rates.indemnity_add_on
            )
        return InpsContributions(
            year=self.year,
            category="artigiani_commercianti",
            income=income,
            base=base,
            rate=rate,
            fixed=fixed,
            percentage=percentage,
            dues=law.fixed_instalments + (law.terms.payments if percentage > 0 else ()),
        )


@dataclass(frozen=True)
class _Reduction:
    """The reduction of an artisan's or a trader's contribution in force: `percent` taken off
    the whole contribution (the flat-rate regime's) or off its IVS part alone."""

    percent: Decimal
    whole_contribution: bool


def _payment_of(terms: PercentagePaymentTerms, kind: InpsDueKind) -> date:
    for payment in terms.payments:
        if payment.kind == kind:
            return payment.due
    raise ValueError(f"the year's terms have no {kind} payment")


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
        "INPS, circolare n. 8 of 3 February 2026 (gestione separata), circolare n. 14 of 9 "
        "February 2026 and circolare n. 35 of 19 February 2016 (artigiani e commercianti), "
        "Agenzia delle Entrate, Redditi PF 2026 fascicolo 2 (the advances), read 2026-11-12"
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
        # Redditi PF 2026 fascicolo 2, appendix «INPS - Modalità di calcolo degli acconti»: «80%
        # del reddito di lavoro autonomo relativo all'anno 2025, tenendo conto del massimale
        # stabilito per il 2026». The ceiling is applied to the income before the 80% (A5, to
        # verify).
        advance_base_percent=Decimal("80"),
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
        artisans=TradeRates(Decimal("24.00"), Decimal("25.00"), Decimal("0.00")),
        # The traders' 0,48 is «aliquota contributiva aggiuntiva nella misura dello 0,48%»
        # (circolare 14/2026 § 1), inside the 24,48 and 25,48.
        traders=TradeRates(Decimal("24.48"), Decimal("25.48"), Decimal("0.48")),
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
        # Art. 1 c. 77 L. 190/2014: «la contribuzione dovuta ai fini previdenziali, ridotta del 35
        # per cento»; circolare 35/2016 § 1: «la contribuzione dovuta, sia quella sul reddito
        # entro il minimale, sia quella sul reddito eventualmente eccedente, viene ridotta del
        # 35%», and «Risulta in ogni caso dovuto il contributo di maternità, pari ad € 7,44
        # annui, da corrispondere alle scadenze previste per la contribuzione in misura fissa».
        # Circolare 14/2026 § 8 still describes the regime; circolare 29/2015 § 1 («senza
        # applicazione del livello minimo imponibile») was replaced by circolare 35/2016 on the
        # fixed part. Applied in 2026 to who had it in 2025 and has not given it up.
        flat_rate_reduction_percent=Decimal("35"),
        # Art. 59 c. 15 L. 449/1997: «il contributo previdenziale può essere a richiesta
        # applicato nella misura della metà»; circolare 14/2026 § 1: «Continuano ad applicarsi,
        # anche per l'anno 2026 [...] la riduzione del 50% dei contributi dovuti dagli artigiani e
        # dagli esercenti attività commerciali con più di 65 anni di età, già pensionati presso le
        # gestioni dell'Istituto»; INPS page «Riduzione contributiva Artigiani e Commercianti»
        # (21 October 2025): «i contributi dovuti sia sul minimale di reddito che sulla quota
        # eccedente il minimale». Not for the flat-rate regime: art. 1 c. 80 L. 190/2014.
        over_65_pensioner_reduction_percent=Decimal("50"),
        # Art. 1 c. 186 L. 207/2024, as circolare 14/2026 § 1 and circolare 83/2025 §§ 1, 3, 5:
        # 50% of the IVS rate for 36 months for who enrols in 2025 for the first time (INPS news
        # of 28 April 2025: «la durata di 36 mesi dalla data di avvio dell'attività»).
        new_enrolment=NewEnrolmentReduction(percent=Decimal("50"), enrolment_year=2025, months=36),
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
    "InpsAdvances",
    "InpsYearLaw",
    "NewEnrolmentReduction",
    "PercentagePaymentTerms",
    "TradeGestione",
    "TradeRates",
]
