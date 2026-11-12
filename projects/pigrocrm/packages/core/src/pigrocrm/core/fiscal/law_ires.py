"""The variations of the srl's profit that PigroCRM can model (REB-1089, taxes design § 1).
Law as data; nothing here computes a tax.

The srl's IRES starts from the accounting profit and adjusts it with the variations of the tax
return (section RF of the Redditi SC form). The estimate (`fiscal/company_estimate.py`) models
three groups of them, each with the facts the product holds or asks for:

* **IRAP deducted from IRES** (`IrapDeductionLaw`): a share of the IRAP paid in the year is
  deductible from the business income.
* **Costs the law limits by kind** (`CostLimit`, `RepresentationLaw`): vehicles, mobile phones and
  hotel and restaurant services are deducted in a percentage of their cost, representation
  expenses within a cap on the year's revenue.
* **Depreciation instead of the full cost of an asset** (`DepreciationLaw`): the cost of a tangible
  asset is deducted a share a year with the coefficient of the ministerial table.

IRAP is not touched by the first two groups: it starts from the income statement (art. 5 c. 1
D.Lgs. 446/1997, `fiscal/law_irap.py`), where these costs are not limited. It is touched by the
third, since an asset's cost leaves the costs of the year and its depreciation (item B10 b) enters
them.

The TUIR renumbering
--------------------
The articles of the D.P.R. 22 December 1986 n. 917 (TUIR) cited here are those in force for tax
year 2026. The D.Lgs. 19 June 2026 n. 117 approved a new consolidated act of the income taxes
(GU n. 152 of 3 July 2026, in force since 4 July 2026) that renumbers them from 2027: Normattiva
already shows
art. 6 c. 1 D.L. 185/2008 and art. 2 c. 1 D.L. 201/2011 as repealed by it. The entry below starts
in 2026 and is never edited for that: the 2027 entry restates each rule against the new numbering,
once the Agenzia's instructions for the 2027 form cite it.

What is not modelled
--------------------
* The 10% lump sum of art. 6 c. 1 D.L. 185/2008 is due only when net interest expense is in the
  IRAP base (circolare 8/E/2013 § 1.2). PigroCRM records no interest, so it is not taken: the
  estimate says so.
* IRAP paid is read from the tax due items settled in the year; the share of the IRAP on staff cost
  is computed on the year's own IRAP base for every payment, the balance of the year before
  included (the base of the year before is not read).
* The limits on the rent and hire of a vehicle (art. 164 c. 1 lett. b), the interest limit (art. 96
  TUIR), ACE, the representation gifts under 50 euro per unit, the 5% limit on maintenance
  (art. 102 c. 6), the intangible assets and the goods whose depreciation table row is not in
  `DepreciationLaw.groups`.

Sources, read on 2026-11-12
---------------------------
* Normattiva, D.L. 29 November 2008 n. 185 art. 6 c. 1 and D.L. 6 December 2011 n. 201 art. 2 c. 1,
  1-bis and 1-ter (text in force on 2026-01-01).
* Agenzia delle Entrate, circolare 8/E of 3 April 2013, §§ 1.1 and 1.2 (the analytic deduction, the
  incidence formula, the cash rule, the coexistence with the 10% and the cap at the tax paid), and
  Redditi SC 2026 instructions, section RF, rows RF16 and RF55 (codes 12 and 33).
* Normattiva, D.P.R. 917/1986 art. 99 c. 1 (other taxes are deductible in the year of payment),
  art. 102 c. 2, 5 and 9, art. 108 c. 2, art. 109 c. 5, art. 164 c. 1 (text in force on 2026-01-01).
* Gazzetta Ufficiale n. 11 of 15 January 2009, D.M. 19 November 2008 art. 1 (what a representation
  expense is). Its art. 1 c. 2 percentages (1.3%, 0.5%, 0.1%) were replaced by the law's own (1.5%,
  0.6%, 0.4%, art. 108 c. 2 as amended): the law's are used.
* Agenzia delle Entrate, D.M. 31 December 1988 of the Ministry of Finance, depreciation
  coefficients of the tangible assets used in a business or a profession (GU n. 27 of
  2 February 1989), table in force from 16 May 1996.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from pigrocrm.core.purchases.deductibility import (
    ASSET_GROUPS,
    DEDUCTIBILITY_KINDS,
    OTHER_ASSET_GROUP,
)


@dataclass(frozen=True)
class IrapDeductionLaw:
    """How the IRAP paid is deducted from the business income of the srl.

    Art. 2 c. 1 D.L. 201/2011 makes deductible, in the year it is paid (art. 99 c. 1 TUIR: the
    cash rule), the IRAP on the taxable share of the cost of employees and assimilated workers,
    net of the deductions of art. 11 D.Lgs. 446/1997: `staff_share_norm`. The Agenzia's
    incidence is the staff cost net of those deductions over the IRAP base net of them
    (circolare 8/E/2013 § 1.2), between 0 and 100%. Art. 6 c. 1 D.L. 185/2008 makes a lump sum of
    `lump_sum_percent`% of the IRAP deductible, for the IRAP on net interest expense
    (`lump_sum_condition`). Both coexist and their sum may not exceed the IRAP paid in the year; the
    estimate takes the first only, as the base it computes holds no interest.
    """

    lump_sum_percent: Decimal
    lump_sum_norm: str
    lump_sum_condition: str
    staff_share_norm: str
    cash_rule_norm: str
    # What counts as staff cost for the share: employees (art. 49 TUIR) and the assimilated
    # income of art. 50 c. 1 lett. c-bis, which includes the fees of a director of a company.
    staff_cost_norm: str
    source: str


@dataclass(frozen=True)
class CostLimit:
    """A kind of cost the law deducts only in part: `percent` of the cost is deductible.

    `acquisition_cost_cap` is the part of an asset's acquisition cost that counts for its
    depreciation (art. 164 c. 1 lett. b: a car's cost above the cap is not considered), `None`
    where the law has no cap for the kind."""

    kind: str
    label: str
    percent: Decimal
    norm: str
    acquisition_cost_cap: Decimal | None = None


@dataclass(frozen=True)
class RepresentationTier:
    """One band of the representation cap: `percent`% of the revenue up to `up_to` (the band's
    upper end, `None` for the open one), counted for the part of the revenue the lower bands do
    not cover."""

    up_to: Decimal | None
    percent: Decimal


@dataclass(frozen=True)
class RepresentationLaw:
    """Art. 108 c. 2 TUIR: representation expenses are deductible in the year they are borne,
    within a cap that is a share of the year's revenue and other proceeds of the characteristic
    activity, in bands. D.M. 19 November 2008 art. 1 says which expenses are representation."""

    label: str
    tiers: tuple[RepresentationTier, ...]
    norm: str

    def cap(self, revenue: Decimal) -> Decimal:
        """What may be deducted for a year with `revenue`, never negative."""
        if revenue <= 0:
            return Decimal(0)
        total = Decimal(0)
        floor = Decimal(0)
        for tier in self.tiers:
            top = revenue if tier.up_to is None else min(revenue, tier.up_to)
            if top > floor:
                total += (top - floor) * tier.percent / Decimal(100)
            if tier.up_to is None or revenue <= tier.up_to:
                break
            floor = tier.up_to
        return total


@dataclass(frozen=True)
class AssetGroupRow:
    """A row of the depreciation table (D.M. 31 December 1988) that a purchase can name."""

    code: str
    label: str
    coefficient: Decimal
    row: str


@dataclass(frozen=True)
class DepreciationLaw:
    """Art. 102 c. 2 TUIR: the deduction of a tangible asset's cost is at most the cost times
    the coefficient of the ministerial table, halved in the first year (`first_year_percent`);
    art. 102 c. 5: an asset whose unit cost is at most `full_deduction_limit` euro is deducted in
    full in the year it is bought, so it needs no coefficient. `groups` are the table rows the
    pack carries; a purchase flagged as an asset in `OTHER_ASSET_GROUP` is one it does not."""

    first_year_percent: Decimal
    full_deduction_limit: Decimal
    groups: tuple[AssetGroupRow, ...]
    norm: str
    table: str

    def coefficient(self, group: str | None) -> Decimal | None:
        """The coefficient, in percent, of `group`; `None` for a group the table does not carry."""
        for row in self.groups:
            if row.code == group:
                return row.coefficient
        return None


@dataclass(frozen=True)
class IresVariationsLaw:
    """The variations of the srl's income that the estimate models, from `from_year` until the
    next entry (a statutory section: it holds until a later entry replaces it)."""

    from_year: int
    irap_deduction: IrapDeductionLaw
    limits: tuple[CostLimit, ...]
    representation: RepresentationLaw
    depreciation: DepreciationLaw

    def limit(self, kind: str) -> CostLimit | None:
        """The limit of `kind`, `None` for `full` and for a kind the entry does not limit."""
        for entry in self.limits:
            if entry.kind == kind:
                return entry
        return None


# Art. 164 c. 1 lett. b: «Non si tiene conto della parte del costo di acquisizione che eccede lire
# 35 milioni per le autovetture e gli autocaravan». 35,000,000 lire at the statutory rate of
# 1,936.27 lire per euro (Reg. CE 2866/98) is 18,075.99 euro. The text raises the cap to euro
# 25.822,84 for the vehicles of agents and commercial representatives.
_CAR_COST_CAP: Final = Decimal("18075.99")
_AGENT_CAR_COST_CAP: Final = Decimal("25822.84")

_ART_164 = "art. 164 c. 1 TUIR"
_ART_102_9 = "art. 102 c. 9 TUIR"
_ART_109_5 = "art. 109 c. 5 TUIR"

IRES_VARIATIONS_2026: Final = IresVariationsLaw(
    from_year=2026,
    irap_deduction=IrapDeductionLaw(
        lump_sum_percent=Decimal("10"),
        lump_sum_norm="art. 6 c. 1 D.L. 185/2008",
        lump_sum_condition=(
            "the IRAP on the taxable share of net interest expense and similar charges, "
            "set as a lump sum of 10% of the IRAP (circolare 8/E/2013 § 1.2: the 10% of the IRAP "
            "paid, provided net interest enters the IRAP base)"
        ),
        staff_share_norm="art. 2 c. 1 D.L. 201/2011",
        cash_rule_norm="art. 99 c. 1 TUIR; circolare 8/E/2013 § 1.2",
        staff_cost_norm="art. 49 and art. 50 c. 1 lett. c-bis TUIR; circolare 8/E/2013 § 1.1",
        source=(
            "Normattiva, D.L. 185/2008 art. 6 c. 1 and D.L. 201/2011 art. 2 c. 1 (text in force "
            "2026-01-01); Agenzia delle Entrate, circolare 8/E of 3 April 2013 §§ 1.1 and 1.2; "
            "Redditi SC 2026 istruzioni, quadro RF, rows RF16 and RF55 codes 12 and 33; read "
            "2026-11-12"
        ),
    ),
    limits=(
        CostLimit(
            "vehicle",
            "veicoli (auto, autocaravan e motocicli non esclusivamente strumentali)",
            Decimal("20"),
            f"{_ART_164} lett. b",
            _CAR_COST_CAP,
        ),
        CostLimit(
            "vehicle_agent",
            "veicoli di agenti e rappresentanti di commercio",
            Decimal("80"),
            f"{_ART_164} lett. b",
            _AGENT_CAR_COST_CAP,
        ),
        CostLimit(
            "vehicle_employee",
            "veicoli in uso promiscuo ai dipendenti",
            Decimal("70"),
            f"{_ART_164} lett. b-bis",
            _CAR_COST_CAP,
        ),
        CostLimit(
            "phone",
            "telefoni",
            Decimal("80"),
            _ART_102_9,
        ),
        CostLimit(
            "hospitality",
            "alberghi, ristoranti e somministrazione di alimenti e bevande",
            Decimal("75"),
            _ART_109_5,
        ),
    ),
    representation=RepresentationLaw(
        label="spese di rappresentanza oltre il limite sui ricavi",
        tiers=(
            RepresentationTier(Decimal("10000000"), Decimal("1.5")),
            RepresentationTier(Decimal("50000000"), Decimal("0.6")),
            RepresentationTier(None, Decimal("0.4")),
        ),
        norm="art. 108 c. 2 TUIR; D.M. 19 November 2008 art. 1",
    ),
    depreciation=DepreciationLaw(
        first_year_percent=Decimal("50"),
        full_deduction_limit=Decimal("516.46"),
        # The four rows below read the same in every group of the table with an office (all but
        # group 19, hotels and restaurants, whose furniture is the row «Mobili e arredamento» at
        # 10%): office furniture 12%, electronic office machines with computers and electronic
        # telephone systems 20%, cars and motorcycles 25%, goods vehicles 20%.
        groups=(
            AssetGroupRow(
                "office_furniture",
                "Mobili e macchine ordinarie d'ufficio",
                Decimal("12"),
                "«Mobili e macchine ordinarie d'ufficio»",
            ),
            AssetGroupRow(
                "office_electronics",
                "Macchine d'ufficio elettroniche, computer e sistemi telefonici elettronici",
                Decimal("20"),
                "«Macchine d'ufficio elettromeccaniche ed elettroniche compresi i computers e i "
                "sistemi telefonici elettronici»",
            ),
            AssetGroupRow(
                "cars",
                "Autovetture, motoveicoli e simili",
                Decimal("25"),
                "«Autovetture, motoveicoli e simili»",
            ),
            AssetGroupRow(
                "goods_vehicles",
                "Autoveicoli da trasporto",
                Decimal("20"),
                "«Autoveicoli da trasporto (autoveicoli pesanti in genere, carrelli elevatori, "
                "mezzi di trasporto interno, ecc.)»",
            ),
        ),
        norm="art. 102 c. 2 and 5 TUIR",
        table=(
            "D.M. 31 December 1988 (GU n. 27 of 2 February 1989), table in force from 16 May "
            "1996, rows common to groups 1 to 18 and 20 to 22; Agenzia delle Entrate, «Decreto "
            "del 31/12/1988 - Min. Finanze», read 2026-11-12"
        ),
    ),
)


def _check(law: IresVariationsLaw) -> None:
    """The vocabulary a person states and the law that answers it must agree: every limited kind
    has a rule, every group but `other` has a coefficient. Checked on import, so a value added
    to one side and not the other fails at once."""
    limited = {entry.kind for entry in law.limits} | {"representation"}
    states = set(DEDUCTIBILITY_KINDS) - {"full"}
    if limited != states:
        raise ValueError(f"deductibility kinds and law disagree: {sorted(limited ^ states)}")
    groups = {row.code for row in law.depreciation.groups}
    if groups != set(ASSET_GROUPS) - {OTHER_ASSET_GROUP}:
        raise ValueError("asset groups and the depreciation table disagree")


_check(IRES_VARIATIONS_2026)

__all__ = [
    "IRES_VARIATIONS_2026",
    "AssetGroupRow",
    "CostLimit",
    "DepreciationLaw",
    "IresVariationsLaw",
    "IrapDeductionLaw",
    "RepresentationLaw",
    "RepresentationTier",
]
