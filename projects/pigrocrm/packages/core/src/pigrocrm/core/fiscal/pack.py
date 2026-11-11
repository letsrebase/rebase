"""The jurisdiction pack -- pure data, no database row (REB-344 §8, REB-361).

`FiscalProfile` stays exactly what it already is: a singleton, non-historicised row
of *emission-time defaults* for the one issuer (`fiscal/models.py`'s own docstring).
A ceiling and a statutory charge are not emission defaults -- they are a fact about
the jurisdiction's regime, ported from mastro's own `FiscalPack`/`Ceiling`/
`StatutoryCharge` interfaces (`fiscal/pack.ts`, `packs/it-flat-rate.ts` in
`~/projects/personal/mastro`, commit `53ef2942`) and trimmed to exactly what a
ceiling engine and a social security surcharge line need: no `TaxTreatment`/`resolveTaxTreatment`
machinery, since `FiscalProfile.default_vat_nature`/`default_vat_rate` already solve
that problem for the one regime this product supports.

Three shipped packs. `IT_FLAT_RATE_PACK` is the flat-rate regime, the currently-in-force version
(`it-flat-rate.ts`'s `effectiveFrom: '2023-01-01'`, not the pre-2023
`itFlatRatePackV0`, since PigroCRM has no history to reconcile against an older
regime). `IT_STANDARD_PACK` is Italy's ordinary regime (mastro's `it-standard.ts`):
no revenue ceiling and no statutory charge, because the flat-rate regime's thresholds and
its social security surcharge are facts of that regime alone. `NON_IT_PACK` is mastro's `generic`
pack
under the name this product gave it (REB-619): a company outside Italy, no ceiling,
no charge, no national invoice format; there is no second no-ceiling pack beside it,
since one that declared nothing and did nothing else would only be a duplicate.
`FiscalProfile.pack_id`/`pack_version` is the pointer to *which* pack
governs -- a plain string pair, no historicisation, matching `FiscalProfile`'s own
un-historicised shape -- read once by the ceiling service at evaluation time. A
further pack is a new module and a new registry entry here, never a branch on
`FiscalProfile.regime_code` inside the ceiling or social security surcharge logic (mastro's own
invariant 1: no country-specific logic outside a jurisdiction pack).

A pack also holds the tax law of its regime, keyed by tax year (`fiscal/law.py`,
REB-974): `FiscalPack.laws`, each entry valid from a tax year, and `law_for(year)` is the one
that governs a year, so a year computed under one pack keeps the rules it was computed with
after the entity changes pack. The pointer stays un-historicised; what is frozen is each
reading (`FiscalEstimateReading`), which stores the pack, its version and the law year it used.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

from pigrocrm.core.fiscal.irpef_2026 import IRPEF_2026
from pigrocrm.core.fiscal.law import (
    CompanyTaxLaw,
    IncomeTaxAdvanceLaw,
    IvaSettlementLaw,
    MonthDay,
    PayrollLaw,
    TaxCalendarLaw,
    TaxDateRule,
    TaxYearLaw,
)
from pigrocrm.core.fiscal.law_inps import INPS_2026, InpsYearLaw
from pigrocrm.core.fiscal.law_irap import IRAP_LAW

# The pack ids, as literals so a schema can name them in a `Literal` type
# (`fiscal/schemas.py::PackId`) and a profile cannot point at a pack that does not exist.
PACK_IT_FLAT_RATE: Final = "it-flat-rate"
PACK_IT_STANDARD: Final = "it-standard"
PACK_IT_COMPANY: Final = "it-company"
PACK_NON_IT: Final = "non-it"

# The only basis this pack's ceilings use today -- mastro's `cash_received_calendar_year`
# (`pack.ts`'s `CeilingBasis`). Money in the bank, within the calendar year, matching
# how the law itself measures the flat-rate regime's own ceilings ("ricavi o compensi
# percepiti").
CeilingBasis = Literal["cash_received_calendar_year"]

# What crossing a ceiling does to the regime -- mastro's own `consequence` text,
# trimmed to the one fact the ceiling service needs to report rather than the
# bilingual prose mastro attaches to it.
CeilingConsequence = Literal["leaves_next_year", "leaves_immediately"]


@dataclass(frozen=True)
class CeilingAlertLevel:
    """One threshold a ceiling's own usage ratio can cross, each with its own label
    -- "approaching" reads differently from "reached" (mastro's `CeilingAlertLevel`,
    `pack.ts:67-77`)."""

    report: Decimal
    label: str


@dataclass(frozen=True)
class Ceiling:
    """A revenue ceiling a pack declares -- mastro's `Ceiling` (`pack.ts:79-112`),
    trimmed to `measure: 'absolute_amount'` and `perimeter: {kind: 'all_clients'}`:
    the only shapes `IT_FLAT_RATE_PACK` needs. A `percentage_share`/`client`-scoped
    ceiling (a contract's own concentration clause) is REB-352's own, later concern
    (§1.5 of the forecasting spec) and is not modelled here."""

    id: str
    label: str
    threshold: Decimal
    basis: CeilingBasis
    consequence: CeilingConsequence
    levels_alert: tuple[CeilingAlertLevel, ...]


@dataclass(frozen=True)
class StatutoryCharge:
    """A statutory charge a pack requires an invoice to carry -- mastro's
    `StatutoryCharge` (`pack.ts:195-221`), trimmed to `amount: {kind: 'percentage'}`
    (`IT_FLAT_RATE_PACK`'s social security surcharge is the only charge this port ships; mastro's
    virtual stamp duty is not ported, per REB-344 §8: PigroCRM never recharges its
    stamp duty to the client).

    `description_line` is the exact `InvoiceLine.description` text the
    invoice-line-construction step writes -- naming the surcharge and its legal
    basis, per REB-361's own "Needed" -- and the one stable marker the tagged-figure
    mechanism (`ceiling.py`) reads back to identify this charge's own lines without a
    new `InvoiceLine`/`Invoice` column: `vat_nature`/`legal_reference` cannot serve
    that role, because every zero-rate line on a flat-rate regime invoice already carries
    the *regime's* single `vat_nature`/`legal_reference` pair
    (`fiscal/regime.py::_flat_rate.resolve_line_vat`), identical whether the line is
    a fee or a statutory charge -- `description` is the only column a per-charge
    identity can live on without touching the two that the SdI already governs.

    `count_by_the_limit`/`count_by_the_coefficient` mirror mastro's own per-charge
    `countsTowardsRevenuePerimeter` declaration (`pack.ts:209-220`,
    `it-flat-rate.ts:283`), extended with a second boolean PigroCRM alone needs:
    mastro never computes a coefficiente-based tax estimate, so its own charge has no
    equivalent of "counts toward the ceiling but is not taxable income" (REB-352 §2,
    §6's resolved decision) -- that fact has nowhere else to live but on the charge
    itself, the same place mastro puts the first boolean.
    """

    id: str
    label: str
    description_line: str
    rate: Decimal
    count_by_the_limit: bool
    count_by_the_coefficient: bool


@dataclass(frozen=True)
class SalesWithholding:
    """What a withholding agent keeps from a professional's invoice (REB-994).

    Every figure is a row of the frame's A5 register: the rate is Agenzia delle Entrate's
    «F24 Ritenute reddito lavoro autonomo», «Aliquote» (20% on work by an independent worker
    resident in Italy, by way of advance), the base («Importi su cui si applica la ritenuta»)
    is the fee and the expenses refunded at cost, and the payment reason `A` is the
    Certificazione Unica 2026 instructions' «prestazioni di lavoro autonomo rientranti
    nell'esercizio di arte o professione abituale». `taxpayer_type` is FPR12's `RT01`, the
    withholding on a natural person: the ordinary regime of this pack is a person's. Whether
    the issuer is an independent worker at all is not modelled: the accountant's list (REB-975)
    has the question, and the ordinary regime is read as one, as the design says.
    """

    rate: Decimal
    payment_reason: str
    taxpayer_type: str


@dataclass(frozen=True)
class FiscalPack:
    """A jurisdiction pack: its ceilings, its statutory charges and its tax law -- mastro's
    `FiscalPack` (`pack.ts:297-350`), trimmed to the sections this port needs.
    Never named by id inside the ceiling or social security surcharge logic (mastro's own `pack.ts`
    header: "nothing in this file may name a concrete pack") -- a caller resolves one
    by `id`/`version` through `resolve_pack` and passes the object on."""

    id: str
    version: str
    ceilings: tuple[Ceiling, ...]
    charges: tuple[StatutoryCharge, ...]
    # The tax law of the regime, one entry per change, oldest first (`fiscal/law.py`). Empty
    # for a regime with nothing computed (`non-it`): no figure, never a guess.
    laws: tuple[TaxYearLaw, ...] = ()
    # The tax dates, one entry per change, oldest first (`TaxCalendarLaw`, REB-1004). Empty
    # for a regime with no date of its own (`non-it`).
    calendars: tuple[TaxCalendarLaw, ...] = ()
    # The withholding an invoice to a withholding agent carries; `None` for a regime whose
    # invoices carry none (the flat-rate regime is not withheld, L. 190/2014 art. 1 c. 67,
    # and a company abroad issues no FatturaPA).
    sales_withholding: SalesWithholding | None = None
    # The INPS contributions of a person, one entry per contribution year (`fiscal/law_inps.py`,
    # REB-1064). Empty for a pack with no person's INPS.
    inps: tuple[InpsYearLaw, ...] = ()

    def __post_init__(self) -> None:
        years = [law.from_year for law in self.laws]
        if years != sorted(set(years)):
            raise ValueError(f"pack {self.id!r}: law entries must have increasing from_year")
        calendar_years = [calendar.from_year for calendar in self.calendars]
        if calendar_years != sorted(set(calendar_years)):
            raise ValueError(f"pack {self.id!r}: calendar entries must have increasing from_year")
        inps_years = [law.year for law in self.inps]
        if inps_years != sorted(set(inps_years)):
            raise ValueError(f"pack {self.id!r}: INPS entries must have increasing year")

    def law_for(self, year: int) -> TaxYearLaw | None:
        """The law that governs tax year `year`: the latest entry starting at or before it,
        `None` when the pack has no law for that year yet."""
        governing = None
        for law in self.laws:
            if law.from_year > year:
                break
            governing = law
        return governing

    @property
    def has_periodic_iva(self) -> bool:
        """The pack's regime settles IVA by period in some tax year (REB-1021): the ordinary
        regime does, the flat-rate regime and a company abroad do not."""
        return any(law.iva is not None for law in self.laws)

    def calendar_for(self, year: int) -> TaxCalendarLaw | None:
        """The dates that govern calendar year `year`: the latest entry starting at or before
        it, `None` when the pack carries no dates for that year (never a guess)."""
        governing = None
        for calendar in self.calendars:
            if calendar.from_year > year:
                break
            governing = calendar
        return governing

    def inps_for(self, year: int) -> InpsYearLaw | None:
        """The INPS law of contribution year `year`, `None` when the pack has none for exactly
        that year: INPS re-sets every figure each year, so the latest earlier entry is not a
        guess worth making."""
        return next((law for law in self.inps if law.year == year), None)

    def charge(self, charge_id: str) -> StatutoryCharge:
        for charge in self.charges:
            if charge.id == charge_id:
                return charge
        raise KeyError(charge_id)


SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID = "rivalsa_inps"

# 4%, `it-flat-rate.ts:278`'s `basisPoints: 400`. The base is the fee subtotal alone,
# never widened by a recharged stamp duty (`chargeSlotOrder`'s other slot): REB-344 §9
# confirms PigroCRM never recharges stamp duty to the client, so the base the law and
# mastro's own pack agree on collapses to the fee alone here.
SOCIAL_SECURITY_SURCHARGE_INPS = StatutoryCharge(
    id=SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID,
    label="Rivalsa contributiva INPS 4%",
    description_line="Rivalsa contributiva INPS 4% (art. 1, comma 212, legge 662/1996)",
    rate=Decimal("0.04"),
    # It counts toward the ceiling in full ("l'importo concorre al limite dei ricavi
    # e compensi del regime flat-rate regime", the circolare `it-flat-rate.ts:262` cites)
    # while never entering the coefficiente base ("pur non costituendo reddito
    # imponibile", the same citation's second half) -- REB-352 §2 and §6's resolved
    # decision, the reason this charge carries two booleans and not one.
    count_by_the_limit=True,
    count_by_the_coefficient=False,
)

# The tax calendar (REB-1004, taxes design § 2, suppliers design § 6). Every date is a row of
# the frame's A5 register, verified on 2026-11-10 against the source its rule names. The
# entries start in 2026, the year the sources were read: an earlier year has no calendar
# in the pack (the instalments, for one, ended in November until the 2022 tax period), and
# a decree that moves a date of a later year is a new entry, never a branch in a service.
# These are the nominal days: the deadline that falls on a Saturday, a Sunday or a national
# holiday moves to the next working day (`fiscal/calendar.py`, A5 row «A deadline on a
# Saturday...»). The 1% on a quarterly payment, the 100 euro below which a periodic payment
# is carried forward and the quarterly eligibility ceilings are `IvaSettlementLaw`'s, in the
# law of `it-standard` below; LIPE dates are not in A5's verified rows.
_SRC_IVA_PAYMENT = "Agenzia delle Entrate, «F24 IVA Periodica - Come e quando si versa»"
_SRC_IVA_ADVANCE = "Agenzia delle Entrate, «Come pagare l'Iva», «Acconto Iva»"
_SRC_IVA_AUGUST = "Agenzia delle Entrate, Scadenzario fiscale, scadenze per Iva del 20-08-2026"
_SRC_INCOME_TAX = "Agenzia delle Entrate, «Imposte sui redditi (Irpef, Ires)» (persone fisiche)"
_SRC_SUBSTITUTE_TAX = (
    "Agenzia delle Entrate, Scadenzario fiscale, «Regime forfetario agevolato: versamento "
    "imposta sostitutiva» (30 June and 30 November 2026)"
)

# Monthly payers pay the previous month's VAT on the 16th; the July payment falls on 20 August
# (the Agenzia's scheduler lists it there for 2026; that it does so every year is TO VERIFY in
# A5, and a pack entry for a later year states it again once it is).
_IVA_MONTHLY_DATES = tuple(MonthDay(month=m, day=20 if m == 8 else 16) for m in range(1, 13))

_IVA_RULES_2026 = (
    TaxDateRule(
        tax="iva",
        kind="periodic_payment",
        cadence="monthly",
        dates=_IVA_MONTHLY_DATES,
        covers="the VAT of the month before",
        source=f"{_SRC_IVA_PAYMENT}; {_SRC_IVA_AUGUST}",
    ),
    TaxDateRule(
        tax="iva",
        kind="annual_balance",
        cadence="quarterly",
        dates=(MonthDay(month=3, day=16),),
        covers="the VAT of the fourth quarter of the year before, with the annual settlement",
        source=_SRC_IVA_PAYMENT,
    ),
    TaxDateRule(
        tax="iva",
        kind="quarterly_payment",
        cadence="quarterly",
        dates=(MonthDay(month=5, day=16),),
        covers="the VAT of the first quarter",
        source=_SRC_IVA_PAYMENT,
    ),
    TaxDateRule(
        tax="iva",
        kind="quarterly_payment",
        cadence="quarterly",
        dates=(MonthDay(month=8, day=20),),
        covers="the VAT of the second quarter",
        source=_SRC_IVA_PAYMENT,
    ),
    TaxDateRule(
        tax="iva",
        kind="quarterly_payment",
        cadence="quarterly",
        dates=(MonthDay(month=11, day=16),),
        covers="the VAT of the third quarter",
        source=_SRC_IVA_PAYMENT,
    ),
    TaxDateRule(
        tax="iva",
        kind="advance",
        cadence=None,
        dates=(MonthDay(month=12, day=27),),
        covers="the advance of the December payment (monthly) or of the fourth quarter (quarterly)",
        source=_SRC_IVA_ADVANCE,
    ),
)

_IT_STANDARD_CALENDAR_2026 = TaxCalendarLaw(
    from_year=2026,
    rules=(
        *_IVA_RULES_2026,
        TaxDateRule(
            tax="income_tax",
            kind="balance_and_first_advance",
            cadence=None,
            dates=(MonthDay(month=6, day=30),),
            covers="the balance of the year before and the first advance of the year",
            source=_SRC_INCOME_TAX,
        ),
        TaxDateRule(
            tax="income_tax",
            kind="second_advance",
            cadence=None,
            dates=(MonthDay(month=11, day=30),),
            covers="the second or only advance of the year",
            source=_SRC_INCOME_TAX,
        ),
        TaxDateRule(
            tax="income_tax",
            kind="instalments_end",
            cadence=None,
            dates=(MonthDay(month=12, day=16),),
            covers="the last instalment of the June balance and first advance",
            source=_SRC_INCOME_TAX,
        ),
    ),
)

# A company's calendar (REB-1033, taxes design § 1 and § 3). IVA is the same settlement as the
# ordinary regime's (the Agenzia's pages address every taxpayer), so it reuses the rules above.
# IRES and IRAP follow the company's fiscal year: the balance and first advance on the last day
# of the sixth month after the year end, the second advance on the last day of the eleventh
# (Agenzia, «Imposte sui redditi (Irpef, Ires)», section on companies). The dates below are for
# a calendar-year company that approves its financial statements within 120 days, the case the
# Agenzia's deadline calendar lists, so they are marked `calendar_year_only`; another year end,
# a late approval and the ISA postponement to 20 July are not modelled. The annual chamber of
# commerce fee falls on the day of the first advance (art. 17 c. 3 lett. a DPR 435/2001); its
# amount is TO VERIFY (A5) and not in the pack. Withholding on pay to others (code 1040 for
# professionals) is due by the 16th of the month after the payment, the same day as IVA's, and
# the deadline calendar moves it to 20 August in 2026 as it does IVA (the deferral of a payment
# of 100 euro or less is not modelled). Payroll withholding and contributions are the staff
# project's dates (`PayrollLaw`).
_SRC_IRES = (
    "Agenzia delle Entrate, «Imposte sui redditi (Irpef, Ires)» (società di capitali) and "
    "Scadenzario fiscale, «Soggetti Ires: versamento saldo 2025 e primo acconto 2026 dell'Ires» "
    "(30 June 2026)"
)
_SRC_IRES_SECOND = (
    "Agenzia delle Entrate, «Imposte sui redditi (Irpef, Ires)» (società di capitali) and Fisco "
    "Oggi, Scadenzario, «Versamenti da dichiarazioni – Acconto 2026» (30 November 2026)"
)
_SRC_IRAP = (
    "Agenzia delle Entrate, Scadenzario fiscale, «Soggetti Ires: versamento saldo 2025 e primo "
    "acconto 2026 dell'Irap» (30 June 2026)"
)
_SRC_IRAP_SECOND = (
    "Fisco Oggi, Scadenzario, «Versamenti da dichiarazioni – Acconto 2026» (codice tributo 3813, "
    "30 November 2026)"
)
_SRC_WITHHOLDING = (
    "Agenzia delle Entrate, «F24 Ritenute reddito lavoro autonomo - Come e quando si versa»; "
    "Scadenzario fiscale, «Sostituti d'imposta: versamento ritenute operate nel mese precedente» "
    "(codice tributo 1040, 20 August 2026)"
)
_SRC_CHAMBER_FEE = (
    "Camere di commercio, «Termini di versamento» del diritto annuale (art. 17 c. 3 lett. a DPR "
    "435/2001; ordinary deadline 30 June 2026 at dirittoannuale.camcom.it)"
)

_WITHHOLDING_DATES = tuple(MonthDay(month=m, day=20 if m == 8 else 16) for m in range(1, 13))

_IT_COMPANY_CALENDAR_2026 = TaxCalendarLaw(
    from_year=2026,
    rules=(
        *_IVA_RULES_2026,
        TaxDateRule(
            tax="ires",
            kind="balance_and_first_advance",
            cadence=None,
            dates=(MonthDay(month=6, day=30),),
            covers="the IRES balance of the year before and the first advance of the year",
            source=_SRC_IRES,
            calendar_year_only=True,
        ),
        TaxDateRule(
            tax="ires",
            kind="second_advance",
            cadence=None,
            dates=(MonthDay(month=11, day=30),),
            covers="the second or only IRES advance of the year",
            source=_SRC_IRES_SECOND,
            calendar_year_only=True,
        ),
        TaxDateRule(
            tax="irap",
            kind="balance_and_first_advance",
            cadence=None,
            dates=(MonthDay(month=6, day=30),),
            covers="the IRAP balance of the year before and the first advance of the year",
            source=_SRC_IRAP,
            calendar_year_only=True,
        ),
        TaxDateRule(
            tax="irap",
            kind="second_advance",
            cadence=None,
            dates=(MonthDay(month=11, day=30),),
            covers="the second or only IRAP advance of the year",
            source=_SRC_IRAP_SECOND,
            calendar_year_only=True,
        ),
        TaxDateRule(
            tax="chamber_fee",
            kind="annual_fee",
            cadence=None,
            dates=(MonthDay(month=6, day=30),),
            covers="the annual fee to the chamber of commerce, with the first advance",
            source=_SRC_CHAMBER_FEE,
            calendar_year_only=True,
        ),
        TaxDateRule(
            tax="withholding",
            kind="withholding_payment",
            cadence=None,
            dates=_WITHHOLDING_DATES,
            covers="the withholding on pay to others of the month before",
            source=_SRC_WITHHOLDING,
        ),
    ),
)

# The flat-rate regime has no periodic IVA settlement (suppliers design § 6), so no IVA date.
# Its substitute tax falls on the same two days as the income tax; the split and the thresholds
# of its advances, and its instalments, are TO VERIFY in A5 and not in the calendar.
_IT_FLAT_RATE_CALENDAR_2026 = TaxCalendarLaw(
    from_year=2026,
    rules=(
        TaxDateRule(
            tax="substitute_tax",
            kind="balance_and_first_advance",
            cadence=None,
            dates=(MonthDay(month=6, day=30),),
            covers="the substitute tax balance of the year before and the first advance",
            source=_SRC_SUBSTITUTE_TAX,
        ),
        TaxDateRule(
            tax="substitute_tax",
            kind="second_advance",
            cadence=None,
            dates=(MonthDay(month=11, day=30),),
            covers="the second or only substitute tax advance of the year",
            source=_SRC_SUBSTITUTE_TAX,
        ),
    ),
)

# The payroll dates every Italian employer follows (A5 rows «Payroll withholding is paid on
# the 16th of the month after the payment», «INPS contributions are paid on the 16th of the
# month after the month worked», «INAIL's yearly premium is paid by 16 February», all verified
# 2026-11-10). A flat-rate taxpayer is a withholding agent on employees (L. 190/2014, art. 1
# c. 69, A5), so it follows them too.
ITALIAN_PAYROLL = PayrollLaw(
    withholding_day=16, contributions_day=16, inail_settlement=MonthDay(month=2, day=16)
)

# it-flat-rate.ts:127-180: two ceilings, both `cash_received_calendar_year` /
# `all_clients`. €85,000 loses the regime from the *following* fiscal year
# (comma 54, l. 190/2014); €100,000 loses it *immediately*, in the same year
# (comma 71). Alert ratios ported verbatim (0.8/1.0 and 0.9/1.0).
IT_FLAT_RATE_PACK = FiscalPack(
    id=PACK_IT_FLAT_RATE,
    version="1",
    ceilings=(
        Ceiling(
            id="revenue_ceiling",
            label="Soglia di ricavi e compensi del regime forfettario",
            threshold=Decimal("85000.00"),
            basis="cash_received_calendar_year",
            consequence="leaves_next_year",
            levels_alert=(
                CeilingAlertLevel(
                    report=Decimal("0.8"),
                    label="In avvicinamento alla soglia di ricavi",
                ),
                CeilingAlertLevel(
                    report=Decimal("1"),
                    label="Soglia di ricavi raggiunta",
                ),
            ),
        ),
        Ceiling(
            id="immediate_exit_ceiling",
            label="Soglia di fuoriuscita immediata dal regime forfettario",
            threshold=Decimal("100000.00"),
            basis="cash_received_calendar_year",
            consequence="leaves_immediately",
            levels_alert=(
                CeilingAlertLevel(
                    report=Decimal("0.9"),
                    label="In avvicinamento alla fuoriuscita immediata dal regime",
                ),
                CeilingAlertLevel(
                    report=Decimal("1"),
                    label="Soglia di fuoriuscita immediata raggiunta",
                ),
            ),
        ),
    ),
    charges=(SOCIAL_SECURITY_SURCHARGE_INPS,),
    # The regime's start year and one arithmetic for every year are TO VERIFY (A5 row «The
    # flat-rate regime exists from 1 January 2015...»): the estimate has always applied the
    # same arithmetic to every year, so one entry from the regime's own first year keeps its
    # figures as they were, and a year before it has no law and so no figure.
    # Its contribution base is the formula `analytics/fiscal.py` carried
    # over from the previous system: TO VERIFY with the accountant (A5 row «Flat-rate
    # contribution base...», REB-975), as are the rest of the estimate's rows (the 5%/15%
    # substitute rate, the profitability coefficients, the profile's INPS rate the estimate reads
    # (the INPS law itself is `inps` below), the cash basis of the revenue). No advance rule:
    # the flat-rate regime's substitute tax advances are not in the register (A5 row added by
    # REB-974, TO VERIFY).
    laws=(
        TaxYearLaw(
            from_year=2015,
            contribution_base="taxable_amount_net_of_substitute_tax",
            # The payroll dates are those of every employer: the sources were read in 2026 and
            # the provisions are older (D.Lgs. 241/1997 art. 18, now art. 32 and 8 of D.Lgs.
            # 33/2025), so the entry does not wait for 2026. A reading frozen under this entry
            # keeps its `law_year` of 2015, which a second entry would have moved.
            payroll=ITALIAN_PAYROLL,
            taxes=("substitute_tax", "inps"),
        ),
    ),
    calendars=(_IT_FLAT_RATE_CALENDAR_2026,),
    inps=(INPS_2026,),
)

# A company established outside Italy (REB-619, spec 2026-10-03 §1.3): no ceiling,
# because the flat-rate regime's thresholds are a fact of Italian law; no statutory charge,
# because the social security surcharge is too; and no stamp duty or `Natura`, which are the regime
# strategy's (`fiscal/regime.py::FOREIGN`, selected by this pack's id). Its invoices
# are never FatturaPA, so `regime_code` is `NULL` on a profile that points here.
NON_IT_PACK = FiscalPack(id=PACK_NON_IT, version="1", ceilings=(), charges=())

# Italy's ordinary regime, RF01 (mastro's `it-standard.ts`: `ceilings: []`, `charges: []`).
# No ceiling, because the 85,000 and 100,000 euro thresholds are the flat-rate regime's
# (l. 190/2014, commi 54 and 71) and an ordinary legal entity is held to neither. No statutory
# charge either, as in mastro: the social security surcharge this product ships is the flat-rate
# regime's
# zero-VAT line, and on an ordinary invoice the surcharge would take VAT and join the
# taxable base, a rule this product has not been given, so a contract's
# `applies_social_charge` election adds no line here
# (`social_security_surcharge_line_for_contract`).
# The regime strategy (`fiscal/regime.py::ORDINARY`) is selected by this pack's id.
# The advance rules are the Agenzia page's of today, read in 2026 (A5, verified), so the
# entry starts there: an earlier year has no law in this pack yet, and asks for none. The
# dates are the calendar's (`_IT_STANDARD_CALENDAR_2026`). No
# income estimate: the IRPEF brackets and the regional surcharges are verified (REB-1062) and
# looked up from `irpef`; the municipal surcharge and the INPS rates are TO VERIFY (A5), and a
# pack states no figure it cannot cite.
IT_STANDARD_PACK = FiscalPack(
    id=PACK_IT_STANDARD,
    version="1",
    ceilings=(),
    charges=(),
    sales_withholding=SalesWithholding(
        rate=Decimal("20.00"), payment_reason="A", taxpayer_type="RT01"
    ),
    laws=(
        TaxYearLaw(
            from_year=2026,
            taxes=("irpef", "inps"),
            # IRPEF brackets of art. 11 TUIR (L. 199/2025) and the regional surcharges of every
            # region, read on 2026-11-11 (A5, REB-1062): `fiscal/irpef_2026.py`.
            irpef=IRPEF_2026,
            advances=IncomeTaxAdvanceLaw(
                advance_percent_of_base=Decimal("100"),
                first_advance_percent=Decimal("40"),
                second_advance_percent=Decimal("60"),
                first_advance_percent_isa=Decimal("50"),
                second_advance_percent_isa=Decimal("50"),
                no_advance_at_or_below=Decimal("51.65"),
                single_november_payment_below=Decimal("257.52"),
                deferral_surcharge_percent=Decimal("0.40"),
                deferral_window_days=30,
            ),
            payroll=ITALIAN_PAYROLL,
            iva=IvaSettlementLaw(
                carry_forward_limit=Decimal("100.00"),
                monthly_carry_periods=tuple(range(1, 11)),
                quarterly_carry_periods=(1, 2),
                quarterly_surcharge_percent=Decimal("1.00"),
                quarterly_surcharge_periods=(1, 2, 3),
                quarterly_turnover_limit_services=Decimal("500000.00"),
                quarterly_turnover_limit_other=Decimal("800000.00"),
            ),
        ),
    ),
    calendars=(_IT_STANDARD_CALENDAR_2026,),
    inps=(INPS_2026,),
)

# An srl (REB-1005, taxes design § 1). It invoices exactly like `it-standard`: `RF01`, the
# same regime strategy (`fiscal/regime.py::ORDINARY`), no ceiling, no statutory charge. It
# differs in what it owes: IRES and IRAP, whose rates are the two verified ones below. The
# rest of what the design lists for it stays a label until its figure is verified and its
# card lands: IVA is C3's, the annual chamber of commerce fee is TO VERIFY (A5), the payroll
# and director rows are C7's, and the company's advance and instalment rules are the
# estimate card's. No income estimate: `contribution_base` is the flat-rate regime's.
# The entry starts in 2026, the year the rates below were read; an earlier year has no law in
# this pack yet, and asks for none.
IT_COMPANY_PACK = FiscalPack(
    id=PACK_IT_COMPANY,
    version="1",
    ceilings=(),
    charges=(),
    laws=(
        TaxYearLaw(
            from_year=2026,
            taxes=("ires", "irap"),
            company_tax=CompanyTaxLaw(
                # D.P.R. 917/1986 (TUIR), art. 77 c. 1, read on Normattiva on 2026-11-10 (A5).
                ires_rate=Decimal("24.00"),
                # D.Lgs. 446/1997, art. 16 c. 1: 3.90%, as the Ministry of Economy and
                # Finance (Dipartimento delle Finanze, «IRAP: disciplina del tributo») and the
                # regions' 2026 tables state it, read on 2026-11-10 (A5). Normattiva's text
                # of the article prints 3,50 under notes (44) and (47), which say that
                # D.L. 66/2014's change was repealed by L. 190/2014 art. 1 c. 22, so that
                # figure is not the rate. The rate
                # a company pays is its region's: art. 16 c. 3 lets a region vary it by up to
                # the points below, c. 1-bis and the D.L. 21/2026 set other rates for other
                # subjects. Which region's rate applies and each region's 2026 ordinary rate
                # are `irap` below (REB-1065, A5 «IRAP region rules», verified 2026-11-11).
                irap_ordinary_rate=Decimal("3.90"),
                irap_regional_variation_max_points=Decimal("0.92"),
                irap=IRAP_LAW,
            ),
            payroll=ITALIAN_PAYROLL,
        ),
    ),
    calendars=(_IT_COMPANY_CALENDAR_2026,),
)

_PACKS: dict[tuple[str, str], FiscalPack] = {
    (IT_COMPANY_PACK.id, IT_COMPANY_PACK.version): IT_COMPANY_PACK,
    (IT_FLAT_RATE_PACK.id, IT_FLAT_RATE_PACK.version): IT_FLAT_RATE_PACK,
    (IT_STANDARD_PACK.id, IT_STANDARD_PACK.version): IT_STANDARD_PACK,
    (NON_IT_PACK.id, NON_IT_PACK.version): NON_IT_PACK,
}


def resolve_pack(pack_id: str, pack_version: str) -> FiscalPack:
    """The pack `FiscalProfile.pack_id`/`pack_version` points at, or `KeyError`
    naming the pair -- the one place a pack is looked up by its identity, mirroring
    mastro's own registry (`registry.ts`) trimmed to a plain dict."""

    try:
        return _PACKS[(pack_id, pack_version)]
    except KeyError:
        raise KeyError(f"unknown fiscal pack {pack_id!r} version {pack_version!r}") from None


def current_pack(pack_id: str) -> FiscalPack:
    """The newest version of a pack, for a caller that holds only the id: an invoice's frozen
    profile names the pack and not its version (`FiscalSnapshot.pack_id`)."""
    versions = [pack for (known, _), pack in _PACKS.items() if known == pack_id]
    if not versions:
        raise KeyError(f"unknown fiscal pack {pack_id!r}")
    return max(versions, key=lambda pack: int(pack.version))


def known_charge_markers() -> frozenset[str]:
    """The `description_line` of every statutory charge any shipped pack declares. What a
    register reads to tell a charge's line from a fee's, whatever pack the legal entity is on
    today: an invoice keeps the lines it was issued with after the profile moves to
    another pack."""
    return frozenset(charge.description_line for pack in _PACKS.values() for charge in pack.charges)


__all__ = [
    "IT_COMPANY_PACK",
    "IT_FLAT_RATE_PACK",
    "ITALIAN_PAYROLL",
    "IT_STANDARD_PACK",
    "NON_IT_PACK",
    "PACK_IT_COMPANY",
    "PACK_IT_FLAT_RATE",
    "PACK_IT_STANDARD",
    "PACK_NON_IT",
    "SOCIAL_SECURITY_SURCHARGE_INPS",
    "SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID",
    "Ceiling",
    "CeilingAlertLevel",
    "CeilingBasis",
    "CeilingConsequence",
    "FiscalPack",
    "IncomeTaxAdvanceLaw",
    "MonthDay",
    "SalesWithholding",
    "StatutoryCharge",
    "TaxCalendarLaw",
    "TaxDateRule",
    "TaxYearLaw",
    "current_pack",
    "known_charge_markers",
    "resolve_pack",
]
