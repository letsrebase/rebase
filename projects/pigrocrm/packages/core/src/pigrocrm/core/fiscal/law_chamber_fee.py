"""The annual chamber of commerce fee («diritto annuale») of an srl, 2026 (REB-1068, taxes design
§ 1 and § 2). Law as data and one pure lookup: year, turnover and province give the fee. The
date it falls on is the calendar's (`TaxDateRule` of kind `annual_fee`), not repeated here.

How the fee is made up
----------------------
An srl is registered in the ordinary section and pays by turnover (the turnover of the year
before: 2025 for the fee of 2026), as the Ministry's note of 16 January 2026 says:

1. The 2014 tiers of the interministerial decree of 21 April 2011 are applied to the turnover:
   200 euro up to 100,000 euro (a fixed amount) plus the rate of each higher tier up to the one
   the turnover falls in, with a ceiling of 40,000 euro; each slice is kept to five decimals.
2. The amount is cut by 50% (art. 28 c. 1 D.L. 90/2014, from 2017 on): so 100 euro up to
   100,000 euro of turnover and at most 20,000 euro.
3. The chamber's increase is added, as a percentage of the cut amount (art. 18 c. 10 L.
   580/1993): 20% for the 61 chambers of annex A of the decree of 17 March 2026, plus, for the
   six Sicilian chambers, another 50% (art. 1 c. 784 L. 205/2017, decree of 2 May 2025, revoked
   from the year after the new Sicilian chambers are constituted, art. 1 c. 2). The
   two Sicilian increases add up: the Sud Est Sicilia chamber prints 170 euro for the first
   tier, which is 100 times 1.70, not 100 times 1.20 times 1.50. A chamber that is not in
   annex A adds nothing (Napoli says so on its page).
4. The result is rounded to the cent (half up), then to the euro (half up), once, at the end.
   Five decimals are also kept after the cut and after the increase: the Ministry's note says
   it for the tier sum and the chambers' guides (Brindisi-Taranto) for the intermediate
   results, so that step reads «intermediate» broadly and matters only within 0.00001 of a
   cent boundary.

Not modelled: the fee of the local units of a company (20% of the head office's amount, up to
a ceiling), the fee of a company in the year it registers, the interest for paying within 30
days after the deadline, and the fee of a company with its head office abroad. PigroCRM has no
local units, and the later cards that need the others read their own figures.

Which chamber a province belongs to
-----------------------------------
`CHAMBERS` lists every chamber of the Unioncamere list read on 2026-11-11 with the provinces it
covers; `ChamberFeeLaw.chamber_for` is the join from the two-letter province a legal entity
stores. `BT` (Barletta-Andria-Trani) and `SU` (Sud Sardegna) are not in the table: no primary
source read says which chamber covers them. The Unioncamere list has no chamber of its own for
either; the Bari chamber lists an office in Barletta on its «Uffici e sedi» page, and the
Cagliari-Oristano page names its four territories without naming `SU`. So the fee of a company
there is `None`, to verify with the accountant (A5, REB-975), never a guess.

Sources, read on 2026-11-11
---------------------------
* The Ministry of Enterprises and Made in Italy, nota n. 9347 of 16 January 2026, «Misure del
  diritto annuale anno 2026»: the 50% cut, the tiers, the five-decimal sequence and the
  rounding of the MISE circular n. 19230 of 2009.
* The Ministry of Enterprises and Made in Italy, D.M. 17 March 2026 (in force from its
  publication of 28 April 2026) and its annex A: the 61 chambers authorised to raise the
  fee by 20% for 2026, 2027 and 2028.
* The Ministry of Enterprises and Made in Italy, D.M. 2 May 2025, art. 1 c. 1: the increase of
  50% for the chambers of Agrigento, Caltanissetta, Messina, Palermo-Enna, Sud Est Sicilia and
  Trapani for 2025, 2026 and 2027.
* The chambers of commerce of Brindisi-Taranto («Guida al pagamento diritto annuale 2026», updated
  2 November 2026), Firenze («Importi 2026» and the table of the variable fee), Sud Est Sicilia
  («Diritto Annuale 2026») and Napoli («Diritto annuale 2026»): the order of the steps, the
  amounts printed with the increases, and the chamber that does not apply one.
* Unioncamere, «Elenco delle Camere di commercio italiane», for the chambers and the
  provinces each covers.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

_FIVE_PLACES: Final = Decimal("0.00001")
_CENT: Final = Decimal("0.01")
_UNIT: Final = Decimal(1)
_HUNDRED: Final = Decimal(100)


@dataclass(frozen=True)
class RateTier:
    """A tier of turnover above `above` (up to `up_to`, `None` for the last) and the rate
    applied to the slice of the turnover that falls in it, in percent."""

    above: Decimal
    up_to: Decimal | None
    rate_percent: Decimal


@dataclass(frozen=True)
class ChamberIncrease:
    """An increase of the fee a chamber is authorised to apply, in percent of the cut amount."""

    percent: Decimal
    norm: str


@dataclass(frozen=True)
class Chamber:
    """A chamber of commerce, the provinces it covers and the increases it applies in 2026.

    `increases` is empty for a chamber that applies none (Napoli). `source` names where the
    chamber's own increase was read when it is not only annex A of the decree."""

    name: str
    provinces: tuple[str, ...]
    increases: tuple[ChamberIncrease, ...] = ()
    source: str = ""

    @property
    def increase_percent(self) -> Decimal:
        return sum((i.percent for i in self.increases), Decimal(0))


@dataclass(frozen=True)
class ChamberFeeLaw:
    """The fee of an srl for one fee year: the tiers, the cut and the chambers."""

    year: int
    # The first tier: the fixed amount owed up to the first tier's `above` of turnover, before
    # the cut.
    fixed_amount: Decimal
    tiers: tuple[RateTier, ...]
    # The ceiling of the amount before the cut.
    max_before_cut: Decimal
    cut_percent: Decimal
    chambers: tuple[Chamber, ...]
    # Provinces no chamber is mapped to: their fee is `None`, never a guess.
    unmapped_provinces: tuple[str, ...]

    def chamber_for(self, province: str) -> Chamber | None:
        """The chamber that covers a two-letter province, `None` when the table has none."""
        code = province.strip().upper()
        for chamber in self.chambers:
            if code in chamber.provinces:
                return chamber
        return None

    def increase_percent(self, province: str) -> Decimal | None:
        """The total increase, in percent, the chamber of `province` applies; `None` when the
        province has no mapped chamber."""
        chamber = self.chamber_for(province)
        return None if chamber is None else chamber.increase_percent

    def national_amount(self, turnover: Decimal) -> Decimal:
        """The amount before the cut and the increase: the fixed tier plus the slice of the
        turnover in each higher tier up to the one it falls in, each slice to five decimals,
        up to the ceiling."""
        if turnover < 0:
            raise ValueError("turnover cannot be negative")
        total = self.fixed_amount
        for tier in self.tiers:
            if turnover <= tier.above:
                break
            top = turnover if tier.up_to is None else min(turnover, tier.up_to)
            total += ((top - tier.above) * tier.rate_percent / _HUNDRED).quantize(
                _FIVE_PLACES, ROUND_HALF_UP
            )
        return min(total, self.max_before_cut)

    def fee(self, year: int, turnover: Decimal, province: str) -> Decimal | None:
        """The fee, in whole euro, of an srl whose registered office is in `province` and whose
        turnover of the year before `year` was `turnover`.

        `None` for a year the law holds no figures for and for a province with no mapped
        chamber. Raises `ValueError` for a negative turnover. The steps are the module
        docstring's, with the five-decimal sequence of the Ministry's note kept after the cut
        and after the increase."""
        if year != self.year:
            return None
        increase = self.increase_percent(province)
        if increase is None:
            return None
        amount = self.national_amount(turnover)
        amount = (amount * (_HUNDRED - self.cut_percent) / _HUNDRED).quantize(
            _FIVE_PLACES, ROUND_HALF_UP
        )
        amount = (amount * (_HUNDRED + increase) / _HUNDRED).quantize(_FIVE_PLACES, ROUND_HALF_UP)
        return amount.quantize(_CENT, ROUND_HALF_UP).quantize(_UNIT, ROUND_HALF_UP)


_ART_18_C_10 = "art. 18 c. 10 L. 580/1993"
_DM_2026 = ChamberIncrease(
    Decimal(20), f"{_ART_18_C_10}; D.M. 17 March 2026, annex A (2026, 2027 and 2028)"
)
_DM_SICILY = ChamberIncrease(
    Decimal(50), "art. 1 c. 784 L. 205/2017; D.M. 2 May 2025 art. 1 c. 1 (2025, 2026 and 2027)"
)
_ANNEX_A = "D.M. 17 March 2026, annex A"


def _c(name: str, *provinces: str, sicily: bool = False, source: str = "") -> Chamber:
    increases = (_DM_2026, _DM_SICILY) if sicily else (_DM_2026,)
    return Chamber(name, provinces, increases, source or _ANNEX_A)


# The 61 chambers of annex A, each with the provinces the Unioncamere list assigns it. Names
# are Unioncamere's; the decree writes some differently: Aosta (Valdostana), Sud Est Sicilia
# («Sicilia orientale»), «Catanzaro, Crotone e Vibo Valentia», «Ferrara e Ravenna», «Palermo
# Enna» and «Venezia Rovigo». The test pins the decree's 61 names against these.
_CHAMBERS: Final = (
    _c("Agrigento", "AG", sicily=True),
    _c("Alessandria-Asti", "AL", "AT"),
    _c("Valdostana", "AO"),
    _c("Arezzo-Siena", "AR", "SI"),
    _c("Bari", "BA"),
    _c("Basilicata", "PZ", "MT"),
    _c("Bergamo", "BG"),
    _c("Bologna", "BO"),
    _c("Bolzano", "BZ"),
    _c("Brescia", "BS"),
    _c("Brindisi-Taranto", "BR", "TA"),
    _c("Cagliari-Oristano", "CA", "OR"),
    _c("Caltanissetta", "CL", sicily=True),
    _c("Caserta", "CE"),
    _c("Catanzaro-Crotone-Vibo Valentia", "CZ", "KR", "VV"),
    _c("Chieti-Pescara", "CH", "PE"),
    _c("Como-Lecco", "CO", "LC"),
    _c("Cosenza", "CS"),
    _c("Cremona-Mantova-Pavia", "CR", "MN", "PV"),
    _c("Cuneo", "CN"),
    _c("Emilia", "PR", "PC", "RE"),
    _c("Ferrara-Ravenna", "FE", "RA"),
    _c("Firenze", "FI"),
    _c("Foggia", "FG"),
    _c("Frosinone-Latina", "FR", "LT"),
    _c("Genova", "GE"),
    _c("Gran Sasso d'Italia", "AQ", "TE"),
    _c("Irpinia-Sannio", "AV", "BN"),
    _c("Lecce", "LE"),
    _c("Marche", "AN", "AP", "FM", "MC", "PU"),
    _c("Maremma e Tirreno", "GR", "LI"),
    _c("Messina", "ME", sicily=True),
    _c("Milano Monza Brianza Lodi", "MI", "MB", "LO"),
    _c("Modena", "MO"),
    _c("Molise", "CB", "IS"),
    _c("Monte Rosa, Laghi, Alto Piemonte", "BI", "NO", "VC", "VB"),
    Chamber(
        "Napoli",
        ("NA",),
        (),
        "Camera di commercio di Napoli, «Diritto annuale 2026»: the Council's resolution n. 10 "
        "of 30 October 2025 decided not to apply the increase; the chamber is not in "
        "annex A",
    ),
    _c("Nuoro", "NU"),
    _c("Padova", "PD"),
    _c("Palermo-Enna", "PA", "EN", sicily=True),
    _c("Pistoia-Prato", "PT", "PO"),
    _c("Pordenone-Udine", "PN", "UD"),
    _c("Reggio Calabria", "RC"),
    _c("Rieti-Viterbo", "RI", "VT"),
    _c("Riviere di Liguria", "IM", "SV", "SP"),
    _c("Roma", "RM"),
    _c("Romagna", "FC", "RN"),
    _c("Salerno", "SA"),
    _c("Sassari", "SS"),
    _c("Sondrio", "SO"),
    _c("Sud Est Sicilia", "CT", "RG", "SR", sicily=True),
    _c("Torino", "TO"),
    _c("Toscana Nord Ovest", "LU", "MS", "PI"),
    _c("Trapani", "TP", sicily=True),
    _c("Trento", "TN"),
    _c("Treviso-Belluno", "TV", "BL"),
    _c("Umbria", "PG", "TR"),
    _c("Varese", "VA"),
    _c("Venezia Giulia", "GO", "TS"),
    _c("Venezia Rovigo Delta Lagunare", "VE", "RO"),
    _c("Verona", "VR"),
    _c("Vicenza", "VI"),
)

# Nothing in the table above covers `BT`; see the module docstring.
_UNMAPPED: Final = ("BT", "SU")

CHAMBERS: Final = _CHAMBERS

CHAMBER_FEE_LAW: Final = ChamberFeeLaw(
    year=2026,
    # Nota MIMIT n. 9347 of 16 January 2026, table «Fasce e aliquote 2014»: 200 euro fixed up to
    # 100,000; 0.015% above 100,000 up to 250,000; 0.013% to 500,000; 0.010% to 1,000,000; 0.009%
    # to 10,000,000; 0.005% to 35,000,000; 0.003% to 50,000,000; 0.001% above, up to a ceiling of
    # 40,000 euro. They are the tiers of D.I. 21 April 2011, as the note restates them.
    fixed_amount=Decimal("200"),
    tiers=(
        RateTier(Decimal("100000"), Decimal("250000"), Decimal("0.015")),
        RateTier(Decimal("250000"), Decimal("500000"), Decimal("0.013")),
        RateTier(Decimal("500000"), Decimal("1000000"), Decimal("0.010")),
        RateTier(Decimal("1000000"), Decimal("10000000"), Decimal("0.009")),
        RateTier(Decimal("10000000"), Decimal("35000000"), Decimal("0.005")),
        RateTier(Decimal("35000000"), Decimal("50000000"), Decimal("0.003")),
        RateTier(Decimal("50000000"), None, Decimal("0.001")),
    ),
    max_before_cut=Decimal("40000"),
    # Art. 28 c. 1 D.L. 90/2014: reduced «a decorrere dall'anno 2017, del 50 per cento»,
    # quoted in the same note.
    cut_percent=Decimal("50"),
    chambers=_CHAMBERS,
    unmapped_provinces=_UNMAPPED,
)

__all__ = [
    "CHAMBERS",
    "CHAMBER_FEE_LAW",
    "Chamber",
    "ChamberFeeLaw",
    "ChamberIncrease",
    "RateTier",
]
