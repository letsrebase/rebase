"""REB-1068: the annual chamber of commerce fee of an srl for 2026, as data in the `it-company`
pack and one pure lookup (year, turnover, province give the fee). No database.

The figures are the Ministry's note n. 9347 of 16 January 2026 (tiers, 50% cut, rounding), the
decree of 17 March 2026 and its annex A (the 20% of 61 chambers) and the decree of 2 May
2025 (the 50% of the six Sicilian chambers); the first-tier and ceiling amounts printed by
the chambers of Firenze, Brindisi-Taranto and Sud Est Sicilia are the oracle (120, 120, 170 and
24,000 euro); the other cases are hand-computed.
"""

from decimal import Decimal

import pytest

from pigrocrm.core.fiscal.law_chamber_fee import CHAMBER_FEE_LAW as LAW
from pigrocrm.core.fiscal.law_chamber_fee import CHAMBERS

ISTAT_PROVINCES = set(
    [
        "AG",
        "AL",
        "AN",
        "AO",
        "AP",
        "AQ",
        "AR",
        "AT",
        "AV",
        "BA",
        "BG",
        "BI",
        "BL",
        "BN",
        "BO",
        "BR",
        "BS",
        "BT",
        "BZ",
        "CA",
        "CB",
        "CE",
        "CH",
        "CL",
        "CN",
        "CO",
        "CR",
        "CS",
        "CT",
        "CZ",
        "EN",
        "FC",
        "FE",
        "FG",
        "FI",
        "FM",
        "FR",
        "GE",
        "GO",
        "GR",
        "IM",
        "IS",
        "KR",
        "LC",
        "LE",
        "LI",
        "LO",
        "LT",
        "LU",
        "MB",
        "MC",
        "ME",
        "MI",
        "MN",
        "MO",
        "MS",
        "MT",
        "NA",
        "NO",
        "NU",
        "OR",
        "PA",
        "PC",
        "PD",
        "PE",
        "PG",
        "PI",
        "PN",
        "PO",
        "PR",
        "PT",
        "PU",
        "PV",
        "PZ",
        "RA",
        "RC",
        "RE",
        "RG",
        "RI",
        "RM",
        "RN",
        "RO",
        "SA",
        "SI",
        "SO",
        "SP",
        "SR",
        "SS",
        "SU",
        "SV",
        "TA",
        "TE",
        "TN",
        "TO",
        "TP",
        "TR",
        "TS",
        "TV",
        "UD",
        "VA",
        "VB",
        "VC",
        "VE",
        "VI",
        "VR",
        "VS",
        "VT",
        "VV",
        "OG",
        "OT",
    ]
)
SICILY = {"AG", "CL", "ME", "PA", "EN", "CT", "RG", "SR", "TP"}


def test_the_company_pack_carries_the_law() -> None:
    from pigrocrm.core.fiscal.pack import IT_COMPANY_PACK

    assert IT_COMPANY_PACK.chamber_fee_for(2026) is LAW


def test_every_province_has_one_chamber_but_og_and_ot() -> None:
    covered = [p for chamber in CHAMBERS for p in chamber.provinces]
    assert len(covered) == len(set(covered))
    assert set(covered) | set(LAW.unmapped_provinces) == ISTAT_PROVINCES
    assert LAW.unmapped_provinces == ("OG", "OT")
    assert LAW.chamber_for("OG") is None
    assert LAW.chamber_for("OT") is None


def test_the_provinces_of_barletta_and_the_south_of_sardinia_belong_to_the_chambers_read() -> None:
    """REB-1100: the national portal's chamber table (read 2026-11-12) puts BT in Bari's and
    Sud Sardegna in Cagliari-Oristano's; D.P.R. 183/2026 renames SU and adds VS, which is
    Medio Campidano, one of the four territories Cagliari-Oristano names."""
    for province, name in [
        ("BT", "Bari"),
        ("BA", "Bari"),
        ("SU", "Cagliari-Oristano"),
        ("VS", "Cagliari-Oristano"),
    ]:
        chamber = LAW.chamber_for(province)
        assert chamber is not None and chamber.name == name, province
        assert chamber.increase_percent == Decimal(20), province


# The 61 names of annex A, as the decree writes them (read as the published image), and the
# name of the same chamber in the pack where it differs (Unioncamere's).
DECREE_NAMES = [
    "Agrigento",
    "Alessandria-Asti",
    "Aosta",
    "Arezzo-Siena",
    "Bari",
    "Basilicata",
    "Bergamo",
    "Bologna",
    "Bolzano",
    "Brescia",
    "Brindisi-Taranto",
    "Cagliari-Oristano",
    "Caltanissetta",
    "Caserta",
    "Catanzaro Crotone e Vibo Valentia",
    "Chieti-Pescara",
    "Como-Lecco",
    "Cosenza",
    "Cremona-Mantova-Pavia",
    "Cuneo",
    "Emilia",
    "Ferrara e Ravenna",
    "Firenze",
    "Foggia",
    "Frosinone-Latina",
    "Genova",
    "Gran Sasso d'Italia",
    "Irpinia-Sannio",
    "Lecce",
    "Marche",
    "Maremma e Tirreno",
    "Messina",
    "Milano Monza Brianza Lodi",
    "Modena",
    "Molise",
    "Monte Rosa Laghi Alto Piemonte",
    "Nuoro",
    "Padova",
    "Palermo Enna",
    "Pistoia-Prato",
    "Pordenone-Udine",
    "Reggio Calabria",
    "Rieti-Viterbo",
    "Riviere di Liguria",
    "Roma",
    "Romagna",
    "Salerno",
    "Sassari",
    "Sicilia orientale",
    "Sondrio",
    "Torino",
    "Toscana Nord Ovest",
    "Trapani",
    "Trento",
    "Treviso-Belluno",
    "Umbria",
    "Varese",
    "Venezia Giulia",
    "Venezia Rovigo",
    "Verona",
    "Vicenza",
]
DECREE_TO_PACK = {
    "Aosta": "Valdostana",
    "Catanzaro Crotone e Vibo Valentia": "Catanzaro-Crotone-Vibo Valentia",
    "Ferrara e Ravenna": "Ferrara-Ravenna",
    "Monte Rosa Laghi Alto Piemonte": "Monte Rosa, Laghi, Alto Piemonte",
    "Palermo Enna": "Palermo-Enna",
    "Sicilia orientale": "Sud Est Sicilia",
    "Venezia Rovigo": "Venezia Rovigo Delta Lagunare",
}


def test_the_raising_chambers_are_the_decrees_61() -> None:
    assert len(DECREE_NAMES) == 61
    expected = {DECREE_TO_PACK.get(name, name) for name in DECREE_NAMES}
    assert {c.name for c in CHAMBERS if c.increases} == expected


def test_sixty_one_chambers_raise_by_20_percent_and_napoli_does_not() -> None:
    assert len(CHAMBERS) == 62
    raising = [c for c in CHAMBERS if c.increases]
    assert len(raising) == 61
    assert [c.name for c in CHAMBERS if not c.increases] == ["Napoli"]


def test_the_sicilian_chambers_add_50_points() -> None:
    for province in ISTAT_PROVINCES - {"OG", "OT"}:
        expected = Decimal(70) if province in SICILY else Decimal(0 if province == "NA" else 20)
        assert LAW.increase_percent(province) == expected, province


@pytest.mark.parametrize(
    ("turnover", "province", "fee"),
    [
        ("0", "MI", "120"),  # 200 cut to 100, plus 20%
        ("100000", "BT", "120"),  # Bari's 20% (REB-1100)
        ("100000", "SU", "120"),  # Cagliari-Oristano's 20% (REB-1100)
        ("100000", "VS", "120"),
        ("100000", "FI", "120"),  # Firenze's printed 120.00
        ("100000", "TA", "120"),  # Brindisi-Taranto's printed 120
        ("100000", "NA", "100"),  # no increase
        ("100000", "CT", "170"),  # Sud Est Sicilia's printed 170
        ("250000", "MI", "134"),  # 222.5 -> 111.25 -> 133.50, half up
        ("250000", "NA", "111"),
        ("250000", "CT", "189"),  # 111.25 * 1.70 = 189.125
        ("1000000", "RM", "183"),  # 305 -> 152.5 -> 183
        ("10000000", "TO", "669"),  # 200+22.5+32.5+50+810 = 1115 -> 557.5 -> 669
        ("10000000000", "RM", "24000"),  # the ceiling of 40,000 cut to 20,000, plus 20%
        ("10000000000", "NA", "20000"),
        ("10000000000", "PA", "34000"),
    ],
)
def test_fee(turnover: str, province: str, fee: str) -> None:
    assert LAW.fee(2026, Decimal(turnover), province) == Decimal(fee)


def test_the_lookup_never_guesses() -> None:
    assert LAW.fee(2025, Decimal(1000), "MI") is None
    assert LAW.fee(2027, Decimal(1000), "MI") is None
    assert LAW.fee(2026, Decimal(1000), "OG") is None
    assert LAW.fee(2026, Decimal(1000), "OT") is None
    assert LAW.fee(2026, Decimal(1000), "ZZ") is None
    with pytest.raises(ValueError):
        LAW.fee(2026, Decimal(-1), "MI")


def test_province_is_case_and_space_insensitive() -> None:
    assert LAW.fee(2026, Decimal(0), " mi ") == Decimal(120)


def test_five_decimals_are_kept_in_the_tier_sum_and_after_the_cut_and_the_increase() -> None:
    # 1.5 euro of turnover in the second tier: 1.5 * 0.015% = 0.000225, kept as 0.00023.
    assert LAW.national_amount(Decimal("100001.5")) == Decimal("200.00023")
    assert LAW.national_amount(Decimal("100001.4")) == Decimal("200.00021")  # 0.00021 exactly
    # 0.00023 cut by half is 0.000115, kept as 0.00012; times 1.20 is 0.000144, kept as 0.00014.
    assert LAW.fee(2026, Decimal("100001.5"), "MI") == Decimal(120)


@pytest.mark.parametrize(
    ("province", "fee"),
    [
        ("MI", "120"),  # Molise's printed 120 for a new company, with the 20% increase
        ("BT", "120"),
        ("NA", "100"),  # no increase
        ("CT", "170"),  # the Sicilian 50% points added
    ],
)
def test_a_company_registered_in_the_year_pays_the_first_tier_amount(
    province: str, fee: str
) -> None:
    """REB-1100: D.I. 21 April 2011 art. 4 c. 2 gives the amount of the first tier, cut and
    increased as every fee, with no turnover and no pro-rating."""
    assert LAW.first_year_fee(2026, province) == Decimal(fee)
    assert LAW.first_year_fee(2026, province) == LAW.fee(2026, Decimal(0), province)
    assert LAW.first_year_days == 30


def test_the_first_year_fee_never_guesses() -> None:
    assert LAW.first_year_fee(2027, "MI") is None
    assert LAW.first_year_fee(2026, "OG") is None
