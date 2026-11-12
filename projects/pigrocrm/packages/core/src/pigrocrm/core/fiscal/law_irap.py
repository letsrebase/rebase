"""IRAP of a company: the tax base, its deductions, the territorial rule and the regional rates
(REB-1065, taxes design § 1 and § 2). Law as data; nothing here computes a tax.

What the later estimate card reads from here
--------------------------------------------
* `IrapLaw.base_items`: which items of the income statement (art. 2425 of the civil code) enter
  the value of production of an srl and with which sign (art. 5 c. 1 D.Lgs. 446/1997).
* `IrapLaw.permanent_staff_deduction` and the other deductions of art. 11.
* `IrapLaw.territory`: which region's rate applies (art. 4).
* `IrapLaw.rate_for(year, region, activity)`: the rate the region applies to a company with that
  activity (`law_irap_ateco.py`): the ordinary row, or the row its ATECO codes select, or why
  there is none.

The estimate's simplification (stated here because PigroCRM keeps no ledger, § A6)
----------------------------------------------------------------------------------
The law starts from the income statement: A minus B of art. 2425, with the items of
`base_items` left out, then the deductions. PigroCRM holds invoices, purchases and staff
records, not the statement, so the estimate has to start from what it has:

* positive component: the year's revenue (item A1) and nothing else of A (no change in
  inventories or work in progress, no capitalised internal work, no other income);
* negative components: the year's purchases of goods and services and what the space records
  as rent and other running costs (items B6, B7, B8, B14), without depreciation (B10 a, b) and
  without inventory changes (B11), which the space does not record;
* staff cost: all of B9 is excluded from the costs (item `B9`), so the whole staff cost
  stays in the base and is then deducted only for permanent employees (art. 11 c. 4-octies);
  the cost of staff who are not permanent, directors, co.co.co collaborators (unless they act
  in their own professional activity), assimilated workers, associates in participation and
  occasional self-employed work stays taxable, as the law says (art. 11 c. 1 lett. b n. 2 to
  5, art. 5 c. 3).

The estimate therefore overstates the base by the depreciation and the inventory change it does
not see, and says so; it is an estimate, never the declared figure (A6 «Not the books»).
Items the law excludes that a ledger would carry (B10 c, B10 d, B12, B13) are none of these
four, so excluding them costs the estimate nothing.

What is not modelled and must not be read from here: the special rates of a subject that is not
an ordinary company (banks and financial companies, insurers, concessionaires, holdings,
third-sector bodies, public bodies) and the regional incentives that depend on facts the pack
does not hold (new companies, young or women founders, small or mountain municipalities,
collective agreements, headcount, *de minimis* aid), the other sections of the IC form
(financial companies, insurers, holding companies, the flat-rate section), the apportionment
of a company with sites in several regions (art. 4 c. 2: the rule is in `territory`, applied by
`fiscal/irap_sites.py`, which says what it does not model), and the deductions that need a record
of each employee (`PerEmployeeDeduction` is data; the estimate applies it to the staff whose
contract type is stated as fixed-term or seasonal, `StaffMember.contract_type`, and not to the
apprentices, the workers on a training contract and the disabled workers,
`ExcludedStaffDeduction`, whom the law leaves out of the headcount and deducts in full). What is
modelled since REB-1086: the regional rows selected by the company's ATECO codes and the two
points of art. 3 c. 1 D.L. 20 February 2026 n. 21 on the activities of its table 1
(`law_irap_ateco.py`), and the pro-rating of art. 11 c. 4-bis.2 to the calendar year
(`calendar_year_days`). Sources, read on 2026-11-11 --------------------------- * Normattiva,
D.Lgs. 15 December 1997 n. 446, art. 4, art. 5 and art. 11 (text up to date at the act's last
update of 11 August 2026),
https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.legislativo:1997-12-15;446~art5 (and
`~art4`, `~art11`). * Agenzia delle Entrate, «Istruzioni IRAP 2026» (Provvedimento prot. n. 71997
of 27 February 2026, form for tax year 2025), «Quadro IC» section I (rows IC1 to IC14: the items
of the value and of the costs of production of a commercial company) and rows IC65 to IC76 (the
order of the deductions: the deduction of art. 11 c. 4-bis is computed on the gross value net of
the other deductions and applies only if that amount does not exceed 180,999.91 euro). *
Dipartimento delle Finanze, «Fiscalità regionale e locale - IRAP - Aliquote applicabili», «Elenchi
generali» (file `irp2026.csv`, publications in force, the latest dated 12 October 2026), the
region's row «Aliquota ordinaria» or the one that plays its part, and the rows selected by
activity (`law_irap_ateco.py` lists them with their sources).
"""

from __future__ import annotations

from calendar import isleap
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Final, Literal

from pigrocrm.core.fiscal.law_irap_ateco import (
    ATECO_RULES_2026,
    TABLE_1,
    IrapRateAnswer,
    RegionAtecoRules,
    Table1,
    digits,
    select_rate,
)


# Art. 11 c. 4-bis.2 brings the table of c. 4-bis, its base and the positive components of
# c. 4-bis.1 to the «anno solare», and the 1,850 euro of c. 4-bis.1 is «su base annua» (Normattiva,
# read 2026-11-12). No source read says how the days of that fraction are counted when the year
# has a 29 February: the pack divides by the days of the calendar year the period ends in (365 or
# 366), so a full calendar year is never pro-rated, which is an inference, and the estimate says
# so.
def calendar_year_days(day: date) -> int:
    """The days of the calendar year `day` falls in."""
    return 366 if isleap(day.year) else 365


# What an item of the income statement does to the value of production of a company that is
# not a bank, a financial company or an insurer (art. 5 c. 1): it adds to it, it is deducted
# from it, or it is left out (the item is neither added nor deducted).
IrapTreatment = Literal["positive", "negative", "excluded"]


@dataclass(frozen=True)
class IrapBaseItem:
    """One item of the income statement (art. 2425 c.c., sections A and B) and what art. 5
    c. 1 D.Lgs. 446/1997 does with it. `code` is the statement's own number: `A1` ... `A5`,
    `B6` ... `B14`, with the letter for the sub-items art. 5 singles out (`B10c`, `B10d`)."""

    code: str
    label: str
    treatment: IrapTreatment
    note: str = ""


# Art. 5 c. 1 (Normattiva, read 2026-11-11): the base is the difference between the value and
# the costs of production of sections A and B of art. 2425 c.c., «con esclusione delle voci
# di cui ai numeri 9), 10), lettere c) e d), 12) e 13)», and of the extraordinary components
# that come from a transfer of a business. The financial section (C), the value adjustments
# (D) are outside A and B: not here. The extraordinary components of a transfer of a business
# are booked inside A5 and B14 and are carved out of them (see their notes).
_BASE_ITEMS: Final = (
    IrapBaseItem("A1", "Ricavi delle vendite e delle prestazioni", "positive"),
    IrapBaseItem(
        "A2",
        "Variazioni delle rimanenze di prodotti in corso di lavorazione, semilavorati e finiti",
        "positive",
    ),
    IrapBaseItem("A3", "Variazioni dei lavori in corso su ordinazione", "positive"),
    IrapBaseItem("A4", "Incrementi di immobilizzazioni per lavori interni", "positive"),
    IrapBaseItem(
        "A5",
        "Altri ricavi e proventi, con separata indicazione dei contributi in conto esercizio",
        "positive",
        note="art. 5 c. 3: contributions given by law count, except those tied to non-deductible "
        "costs; the components of a transfer of a business or branch are left out (art. 5 c. 1)",
    ),
    IrapBaseItem("B6", "Costi per materie prime, sussidiarie, di consumo e di merci", "negative"),
    IrapBaseItem("B7", "Costi per servizi", "negative"),
    IrapBaseItem("B8", "Costi per godimento di beni di terzi", "negative"),
    IrapBaseItem(
        "B9",
        "Costi per il personale",
        "excluded",
        note="art. 5 c. 1 leaves item 9 out of the costs; the cost of permanent employees is "
        "deducted separately (art. 11 c. 4-octies), and staff cost booked in other items is "
        "not deductible either (art. 5 c. 3)",
    ),
    IrapBaseItem("B10a", "Ammortamento delle immobilizzazioni immateriali", "negative"),
    IrapBaseItem("B10b", "Ammortamento delle immobilizzazioni materiali", "negative"),
    IrapBaseItem("B10c", "Altre svalutazioni delle immobilizzazioni", "excluded"),
    IrapBaseItem(
        "B10d",
        "Svalutazioni dei crediti compresi nell'attivo circolante e delle disponibilità liquide",
        "excluded",
    ),
    IrapBaseItem(
        "B11",
        "Variazioni delle rimanenze di materie prime, sussidiarie, di consumo e merci",
        "negative",
    ),
    IrapBaseItem("B12", "Accantonamenti per rischi", "excluded"),
    IrapBaseItem("B13", "Altri accantonamenti", "excluded"),
    IrapBaseItem(
        "B14",
        "Oneri diversi di gestione",
        "negative",
        note="art. 5 c. 3: losses on receivables, the property tax (IMU) and the interest part "
        "of finance lease instalments are not deductible; the components of a transfer of a "
        "business or branch are left out (art. 5 c. 1)",
    ),
)


@dataclass(frozen=True)
class PermanentStaffDeduction:
    """Art. 11 c. 4-octies: the whole cost of the employees on a permanent contract is
    deducted, and so is, up to 70% of the cost, that of every seasonal worker employed for at
    least 120 days in two tax periods, from the second contract with the same employer within
    two years of the end of the previous one. Percentages of the cost."""

    permanent_percent: Decimal
    seasonal_percent: Decimal
    seasonal_min_days: int
    seasonal_window_years: int


@dataclass(frozen=True)
class ExcludedStaffDeduction:
    """Art. 11 c. 1 lett. a n. 5: the cost of apprentices, of disabled workers (art. 1 L. 68/1999,
    art. 5 L. 482/1968) and of staff on a training contract (the law's «formazione e lavoro»)
    is deducted, in
    `percent`, over and above c. 4-octies; they are not counted in the headcount of c. 4-bis.1."""

    percent: Decimal


@dataclass(frozen=True)
class PerEmployeeDeduction:
    """Art. 11 c. 4-bis.1: for a company whose positive components of the value of production
    do not exceed `components_limit` in the tax period, a deduction of `amount` a year for each
    employee who is not on a permanent contract, up to `max_employees` of them; apprentices,
    disabled workers and workers on training contracts are not counted in the headcount. Pro
    rata to the days of the employment (c. 4-bis.2), and never more than the employee's pay and
    charges (c. 4-septies)."""

    amount: Decimal
    max_employees: int
    components_limit: Decimal


@dataclass(frozen=True)
class SmallBaseDeduction:
    """One row of the table of art. 11 c. 4-bis: `amount` is deducted (up to the base itself)
    when the base does not exceed `base_up_to`. Rows are in ascending order of `base_up_to`."""

    base_up_to: Decimal
    amount: Decimal


@dataclass(frozen=True)
class IrapTerritoryLaw:
    """Which region a company's value of production belongs to (art. 4).

    Art. 4 c. 1: the tax is levied on the value of production of the activity carried on in
    the region. Art. 4 c. 2: with activity in several regions, the value is attributed to each
    region in proportion to the pay of the staff continuously assigned for at least
    `minimum_months` to its establishments, yards, offices or fixed bases; the value of activity
    carried on elsewhere without staff for that long goes to the region where the company is
    domiciled. A company with one site therefore pays the rate of the region of its registered
    office (`single_site`). The attribution key of several sites is data here and
    `fiscal/irap_sites.py` applies it to the legal entity's sites and the pay of its staff.
    """

    single_site: Literal["registered_office_region"]
    multi_region_key: Literal["staff_pay_by_region"]
    minimum_months: int
    fallback: Literal["domicile_region"]


@dataclass(frozen=True)
class IrapRegionRate:
    """A region's (or autonomous province's) ordinary IRAP rate for a company, in percent.

    `mef_id` and `published` are the Dipartimento delle Finanze's record of the regional
    publication that sets it (`ID_IRAP`, `DATA_PUBBLICAZIONE` of the dataset); `norm` is the
    provision the dataset cites for the row. `note` says, where the region's table holds
    another rate an ordinary company may qualify for, which one and why it is not the figure.
    `ateco` holds the rows of the table that the company's ATECO codes select
    (`law_irap_ateco.py`).
    """

    key: str
    name: str
    rate: Decimal
    mef_id: int
    published: str
    norm: str
    note: str = ""
    ateco: RegionAtecoRules = RegionAtecoRules()


@dataclass(frozen=True)
class IrapActivity:
    """What a company says about its activity: the prevailing ATECO code in the 2007 and the
    2025 classification, as typed (`35.11.00`) or `None` when not stated. The regional lists are
    written in ATECO 2007 and table 1 of D.L. 21/2026 in ATECO 2025, so a rate that depends on
    both needs both."""

    ateco_2007: str | None = None
    ateco_2025: str | None = None


@dataclass(frozen=True)
class IrapRegionalRates:
    """The ordinary rates of one tax year, one row per region or autonomous province."""

    year: int
    rates: tuple[IrapRegionRate, ...]

    def rate(self, key: str) -> IrapRegionRate | None:
        for row in self.rates:
            if row.key == key:
                return row
        return None


@dataclass(frozen=True)
class IrapLaw:
    """IRAP of an srl: base, deductions, territorial rule and the regional rates by year."""

    base_items: tuple[IrapBaseItem, ...]
    permanent_staff: PermanentStaffDeduction
    excluded_staff: ExcludedStaffDeduction
    per_employee: PerEmployeeDeduction
    small_base: tuple[SmallBaseDeduction, ...]
    territory: IrapTerritoryLaw
    regional_rates: tuple[IrapRegionalRates, ...]
    table_1: Table1

    def item(self, code: str) -> IrapBaseItem:
        for item in self.base_items:
            if item.code == code:
                return item
        raise KeyError(code)

    def small_base_deduction(
        self, base: Decimal, period_days: int = 365, year_days: int = 365
    ) -> Decimal:
        """The deduction of art. 11 c. 4-bis for a base already net of the other deductions (it is
        not due above the last row's limit, 180,999.91 in the Agenzia's instructions), brought to
        the calendar year as art. 11 c. 4-bis.2 says for a period that is not twelve months:
        the base is annualised (`base * year_days / period_days`), the row is the first whose
        limit the annual base does not exceed, and its amount is brought back to the period
        (`amount * period_days / year_days`), never more than the base. Zero for a base above
        the last limit or not positive. A period as long as its calendar year is not pro-rated."""
        if base <= 0:
            return Decimal(0)
        annual = base * year_days / period_days
        for row in self.small_base:
            if annual <= row.base_up_to:
                return min(row.amount * period_days / year_days, base)
        return Decimal(0)

    def region_row(self, year: int, region: str) -> IrapRegionRate | None:
        """The table's row of `region` for `year` (its name, ordinary rate and sources), `None`
        for a year or a key the pack holds nothing for."""
        code = region.strip().upper()
        for table in self.regional_rates:
            if table.year == year:
                return table.rate(code)
        return None

    def rate_for(
        self, year: int, region: str, activity: IrapActivity | None = None
    ) -> IrapRateAnswer | None:
        """The IRAP rate, in percent, for a company whose registered office is in `region`, for
        tax year `year`, with the activity it states (none stated is a company that stated no
        code). `region` is the ISTAT region code (`01` to `20`), or `BZ` or `TN` for the two
        autonomous provinces, which set their own rates; the caller takes it from the place
        data of the legal entity's address. The answer carries the rate, the row of the
        region's table that selected it (none for the ordinary rate) and what to tell the
        reader, or says which fact or list stops a rate (`law_irap_ateco.select_rate`). `None`
        for a year the pack holds no table for or a key it does not know. Never a guess."""
        code = region.strip().upper()
        for table in self.regional_rates:
            if table.year == year:
                row = table.rate(code)
                if row is None:
                    return None
                stated = activity or IrapActivity()
                return select_rate(
                    year,
                    row.rate,
                    row.ateco,
                    self.table_1,
                    digits(stated.ateco_2007),
                    digits(stated.ateco_2025),
                )
        return None


def _rate(
    key: str,
    name: str,
    rate: str,
    mef_id: int,
    published: str,
    norm: str,
    note: str = "",
) -> IrapRegionRate:
    return IrapRegionRate(key, name, Decimal(rate), mef_id, published, norm, note)


def _with_ateco(
    rows: tuple[IrapRegionRate, ...], rules: Mapping[str, RegionAtecoRules]
) -> tuple[IrapRegionRate, ...]:
    """The year's rows with the rows its table selects by activity attached to each region."""
    return tuple(replace(row, ateco=rules.get(row.key, RegionAtecoRules())) for row in rows)


# Tax year 2026. Source for every row: Dipartimento delle Finanze, IRAP, «Aliquote applicabili»,
# «Elenchi generali», `irp2026.csv` (publications in force, the latest dated 12 October 2026),
# read on 2026-11-11; the row is the one the dataset calls «Aliquota ordinaria» (code 001),
# except Veneto (see its note), and `published` is the dataset's `DATA_PUBBLICAZIONE` for the
# region's record.
# Keys are ISTAT region codes; `BZ` and `TN` are the autonomous provinces.
_RATES_2026: Final = IrapRegionalRates(
    year=2026,
    rates=_with_ateco(
        (
            _rate("01", "Piemonte", "3.90", 2313, "2026-04-01", "art. 16 c. 1 D.Lgs. 446/1997"),
            _rate(
                "02", "Valle d'Aosta", "3.90", 2401, "2026-06-05", "art. 16 c. 1 D.Lgs. 446/1997"
            ),
            _rate("03", "Lombardia", "3.90", 2312, "2026-03-30", "art. 16 c. 1 D.Lgs. 446/1997"),
            _rate(
                "BZ",
                "Provincia autonoma di Bolzano",
                "3.90",
                2501,
                "2026-10-12",
                "art. 21-bis c. 7-quater L.P. 11 August 1998 n. 9",
                "the table also lists 2.68 for subjects that pay additional pay under "
                "territorial or company agreements; not the ordinary rate",
            ),
            _rate(
                "TN",
                "Provincia autonoma di Trento",
                "2.68",
                2302,
                "2026-03-20",
                "art. 1 c. 8 L.P. 30 December 2015 n. 21",
                "reduced rate of the province for subjects of art. 16 c. 1 that do not fall in "
                "the provincial code 010 and do not mainly carry on an activity named by D.L. "
                "21/2026; the table also lists 2.00 (code 002, for subjects applying collective "
                "agreements signed in the province) and 4.82 (code 010)",
            ),
            _rate(
                "05",
                "Veneto",
                "4.08",
                2421,
                "2026-06-09",
                "art. 2 c. 1 lett. b L.R. 32/2024",
                "the dataset has no «Aliquota ordinaria» row: code 001 is the general increase "
                "to 4.08, which applies to every subject not listed in its other codes (codes 02 "
                "and 03: new young or women companies of 2024 to 2026 at 2.90; 12: gaming "
                "machines at 4.82; 15 and 17: the ATECO lists of `law_irap_ateco.py`; 19 to 24: "
                "the D.L. 21/2026 activities; the others social cooperatives, banks, insurers, "
                "concessionaires, public bodies and holdings), read 2026-11-12",
            ),
            _rate(
                "06",
                "Friuli-Venezia Giulia",
                "3.90",
                2383,
                "2026-05-21",
                "art. 16 c. 1 D.Lgs. 446/1997",
                "the table also lists 2.98 for companies with one to five employees on the "
                "last day of the period (art. 2 c. 1 lett. a L.R. 17/2008, code 010) and other "
                "reductions under conditions; not the ordinary rate",
            ),
            _rate(
                "07",
                "Liguria",
                "3.90",
                2303,
                "2026-03-20",
                "art. 16 c. 1 D.Lgs. 446/1997",
                "4.82 for the ATECO codes of its code 006 (art. 2 L.R. 43/2007), selected by "
                "`law_irap_ateco.py`",
            ),
            _rate(
                "08",
                "Emilia-Romagna",
                "3.90",
                2320,
                "2026-04-02",
                "art. 16 c. 1 D.Lgs. 446/1997",
                "4.20 (code 002) for the activities of the Allegato A of L.R. 1/2025, which are "
                "most commercial and service activities, and 4.82 (code 004) for the ATECOFIN "
                "2004 sectors 23, 40, 64 and 67: selected by `law_irap_ateco.py`; this is the "
                "rate of a company whose activity is on neither",
            ),
            _rate(
                "09",
                "Toscana",
                "3.90",
                2318,
                "2026-04-13",
                "art. 16 c. 1 D.Lgs. 446/1997",
                "4.82 (code 007) for the activities of its ATECO 2007 list, among them 45 to "
                "47, 55, 56, 61 to 63, 68 to 74: selected by `law_irap_ateco.py`; this is the "
                "rate of a company whose activity is not on it",
            ),
            _rate(
                "10",
                "Umbria",
                "4.30",
                2481,
                "2026-07-30",
                "art. 16 c. 1 D.Lgs. 446/1997; c. 4 L.R. 11 April 2025 n. 2",
                "4.82 (code 002) for the ATECO 2007 codes of art. 23 L.R. 36/2007 (post, "
                "telecommunications, building development, real estate, financial auxiliaries): "
                "selected by `law_irap_ateco.py`",
            ),
            _rate(
                "11",
                "Marche",
                "4.73",
                2307,
                "2026-03-20",
                "art. 1 c. 3 L.R. 19 December 2001 n. 35; art. 29 L.R. 37/2008",
                "the table also lists 4.13 (codes 010, 011) for companies of sections C to G "
                "with a seat in the region under conditions on the INAIL premium, exports or "
                "hiring; not the ordinary rate",
            ),
            _rate(
                "12",
                "Lazio",
                "4.82",
                2361,
                "2026-04-14",
                "art. 16 c. 1 D.Lgs. 446/1997; art. 2 c. 4 L.R. 20/2025",
            ),
            _rate(
                "13",
                "Abruzzo",
                "4.82",
                2309,
                "2026-03-26",
                "art. 1 c. 174 L. 311/2004; art. 1 c. 6 and 7 L.R. 12 December 2006 n. 44",
            ),
            _rate(
                "14",
                "Molise",
                "4.97",
                2441,
                "2026-07-20",
                "art. 16 c. 1 D.Lgs. 446/1997; art. 2 c. 86 L. 191/2009",
                "includes the 0.92 point increase",
            ),
            _rate(
                "15",
                "Campania",
                "4.97",
                2325,
                "2026-04-01",
                "art. 16 c. 1 D.Lgs. 446/1997; L.R. 16 January 2014 n. 4",
            ),
            _rate(
                "16",
                "Puglia",
                "4.82",
                2461,
                "2026-07-23",
                "art. 4 c. 1 L.R. 29 December 2015 n. 40",
            ),
            _rate(
                "17",
                "Basilicata",
                "3.90",
                2317,
                "2026-04-13",
                "art. 16 c. 1 D.Lgs. 446/1997",
                "the energy and oil sectors have rows 012 to 014 (4.82, 6.82, 5.90) that "
                "`law_irap_ateco.py` does not decide",
            ),
            _rate(
                "18",
                "Calabria",
                "4.82",
                2306,
                "2026-03-20",
                "art. 16 c. 1 and 3 D.Lgs. 446/1997; art. 1 c. 174 L. 311/2004",
            ),
            _rate(
                "19",
                "Sicilia",
                "3.90",
                2305,
                "2026-03-30",
                "art. 16 c. 1 D.Lgs. 446/1997; art. 3 c. 16 lett. a L.R. 9 May 2017 n. 8",
                "the table also lists 3.65 (code 014) for small and medium companies in crafts, "
                "trade, tourism and services under conditions; not the ordinary rate",
            ),
            _rate(
                "20",
                "Sardegna",
                "2.93",
                2381,
                "2026-05-13",
                "art. 16 c. 1 D.Lgs. 446/1997; art. 3 c. 5 lett. a L.R. 9 March 2015 n. 5",
            ),
        ),
        ATECO_RULES_2026,
    ),
)

IRAP_LAW: Final = IrapLaw(
    base_items=_BASE_ITEMS,
    # D.Lgs. 446/1997 art. 11 c. 4-octies, Normattiva, read 2026-11-11.
    permanent_staff=PermanentStaffDeduction(
        permanent_percent=Decimal("100"),
        seasonal_percent=Decimal("70"),
        seasonal_min_days=120,
        # «nell'arco temporale di due anni a partire dalla cessazione del precedente contratto».
        seasonal_window_years=2,
    ),
    # Art. 11 c. 1 lett. a n. 5 and c. 4-bis.1 (last sentence), Normattiva, read 2026-11-12:
    # «le spese relative agli apprendisti, ai disabili e le spese per il personale assunto con
    # contratti di formazione e lavoro» are deducted in full; «non si tiene conto degli
    # apprendisti, dei disabili e del personale assunto con contratti di formazione lavoro» in the
    # headcount of c. 4-bis.1. Istruzioni IRAP 2026, rigo IS4 and IS5 (read 2026-11-12).
    excluded_staff=ExcludedStaffDeduction(percent=Decimal("100")),
    # Art. 11 c. 4-bis.1 and 4-bis.2, Normattiva, read 2026-11-11.
    per_employee=PerEmployeeDeduction(
        amount=Decimal("1850"),
        max_employees=5,
        components_limit=Decimal("400000"),
    ),
    # Art. 11 c. 4-bis lett. a to d, Normattiva, read 2026-11-11: 8,000 euro if the base does
    # not exceed 180,759.91; 6,000 if it exceeds that but not 180,839.91; 4,000 if it exceeds
    # that but not 180,919.91; 2,000 if it exceeds that but not 180,999.91, up to the base.
    # Lett. d-bis raises these only for the subjects of art. 3 c. 1 lett. b and c
    # (partnerships and individual firms), not for an srl (lett. a).
    small_base=(
        SmallBaseDeduction(Decimal("180759.91"), Decimal("8000")),
        SmallBaseDeduction(Decimal("180839.91"), Decimal("6000")),
        SmallBaseDeduction(Decimal("180919.91"), Decimal("4000")),
        SmallBaseDeduction(Decimal("180999.91"), Decimal("2000")),
    ),
    # Art. 4 c. 1 and 2 D.Lgs. 446/1997, Normattiva, read 2026-11-11.
    territory=IrapTerritoryLaw(
        single_site="registered_office_region",
        multi_region_key="staff_pay_by_region",
        minimum_months=3,
        fallback="domicile_region",
    ),
    regional_rates=(_RATES_2026,),
    # D.L. 20 February 2026 n. 21 art. 3 c. 1 and Tabella 1, Gazzetta Ufficiale n. 42 of
    # 20-2-2026, read 2026-11-12.
    table_1=TABLE_1,
)

__all__ = [
    "IRAP_LAW",
    "calendar_year_days",
    "IrapBaseItem",
    "IrapActivity",
    "IrapLaw",
    "IrapRegionRate",
    "IrapRegionalRates",
    "IrapTerritoryLaw",
    "IrapTreatment",
    "ExcludedStaffDeduction",
    "PerEmployeeDeduction",
    "PermanentStaffDeduction",
    "SmallBaseDeduction",
]
