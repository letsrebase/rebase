"""ISTAT's list of municipalities, and the place an address names (REB-1069)."""

import csv
import json
from collections import Counter

import pytest

from pigrocrm.core.places.locate import locate, municipalities_of
from pigrocrm.core.places.municipalities import DATA_FILE, fold, municipality_list

SOURCE_FILE = DATA_FILE.with_suffix(".source.json")


def test_the_file_is_the_whole_list_and_says_where_it_came_from() -> None:
    source = json.loads(SOURCE_FILE.read_text(encoding="utf-8"))
    municipalities = municipality_list().municipalities
    # 7,894 municipalities from 21 February 2026, as ISTAT's page states.
    assert len(municipalities) == source["rows"] == 7_894
    assert source["url"].startswith("https://www.istat.it/")
    assert source["sheet"] == "CODICI al 21_02_2026"
    assert len(source["downloaded"]) == 10 and len(source["sha256"]) == 64
    with DATA_FILE.open(encoding="utf-8", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == len(municipalities)


def test_every_code_is_unique_and_in_its_shape() -> None:
    everything = municipality_list()
    assert len(everything.by_istat_code) == len(everything.municipalities)
    assert len(everything.by_cadastral_code) == len(everything.municipalities)
    for municipality in everything.municipalities:
        assert len(municipality.istat_code) == 6 and municipality.istat_code.isdigit()
        assert len(municipality.cadastral_code) == 4
        assert municipality.region_code in {f"{n:02d}" for n in range(1, 21)}


def test_a_province_belongs_to_one_region() -> None:
    regions: dict[str, set[str]] = {}
    for municipality in municipality_list().municipalities:
        regions.setdefault(municipality.province, set()).add(municipality.region_code)
    assert {province for province, codes in regions.items() if len(codes) != 1} == set()


def test_no_two_municipalities_of_a_province_read_alike() -> None:
    """The match is by name inside the province, so two municipalities that fold to the
    same name there would make it pick one for the other."""
    seen: Counter[tuple[str, str]] = Counter()
    for municipality in municipality_list().municipalities:
        row_names = {municipality.name, *municipality.name.split("/")}
        for name in {fold(n) for n in row_names}:
            seen[(municipality.province, name)] += 1
    assert [key for key, count in seen.items() if count > 1] == []


@pytest.mark.parametrize(
    ("typed", "folded"),
    [
        ("Sant'Agata de' Goti", "santagatadegoti"),
        ("SANT’AGATA DE’ GOTI", "santagatadegoti"),
        ("sant agata  de goti", "santagatadegoti"),
        ("Forlì", "forli"),
        ("  Reggio nell'Emilia ", "reggionellemilia"),
        ("Castel-San-Pietro", "castelsanpietro"),
    ],
)
def test_fold_ignores_case_accents_apostrophes_and_spacing(typed: str, folded: str) -> None:
    assert fold(typed) == folded


@pytest.mark.parametrize(
    ("city", "province", "istat", "cadastral", "region"),
    [
        ("Milano", "MI", "015146", "F205", "Lombardia"),
        ("ROMA", "rm", "058091", "H501", "Lazio"),
        ("Napoli", "NA", "063049", "F839", "Campania"),
        ("reggio nell’emilia", "RE", "035033", "H223", "Emilia-Romagna"),
        ("Reggio Nell Emilia", "RE", "035033", "H223", "Emilia-Romagna"),
        ("Bolzano", "BZ", "021008", "A952", "Trentino-Alto Adige/Südtirol"),
        ("Bozen", "BZ", "021008", "A952", "Trentino-Alto Adige/Südtirol"),
        ("Bolzano/Bozen", "BZ", "021008", "A952", "Trentino-Alto Adige/Südtirol"),
        ("Castegnero Nanto", "VI", "024129", "M439", "Veneto"),
        ("Olbia", "OT", "113017", "G015", "Sardegna"),
        ("Sant'Agata de' Goti", "BN", "062070", "I197", "Campania"),
        ("Sant Agata de Goti", "BN", "062070", "I197", "Campania"),
        ("Forli", "FC", "040012", "D704", "Emilia-Romagna"),
    ],
)
def test_a_city_is_found_in_its_province(
    city: str, province: str, istat: str, cadastral: str, region: str
) -> None:
    place = locate(city, province, "IT")
    assert place.status == "matched", place
    assert (place.municipality_code, place.cadastral_code, place.region_name) == (
        istat,
        cadastral,
        region,
    )
    assert place.region_code == municipality_list().by_istat_code[istat].region_code


def test_a_name_is_never_guessed_across_provinces() -> None:
    """Rome is in RM, not in MI, and a province nobody lists matches nothing."""
    assert locate("Roma", "MI", "IT").status == "unknown"
    assert locate("Roma", "XX", "IT").status == "unknown"


def test_an_unknown_city_still_gives_the_region_of_its_province() -> None:
    place = locate("Reggio Emilia", "RE", "IT")
    assert place.status == "unknown"
    assert place.municipality_code is None and place.cadastral_code is None
    assert (place.region_code, place.region_name) == ("08", "Emilia-Romagna")


@pytest.mark.parametrize(
    ("city", "province"), [(None, "MI"), ("", "MI"), ("Milano", None), ("Milano", ""), (None, None)]
)
def test_an_address_missing_its_city_or_province_is_unknown(
    city: str | None, province: str | None
) -> None:
    assert locate(city, province, "IT").status == "unknown"


def test_a_foreign_address_is_not_italian() -> None:
    place = locate("Milano", "MI", "fr")
    assert place.model_dump() == {
        "status": "not_italian",
        "municipality_code": None,
        "cadastral_code": None,
        "municipality_name": None,
        "region_code": None,
        "region_name": None,
    }
    # No country is Italy, the column's own default.
    assert locate("Milano", "MI", None).status == "matched"


def test_a_province_lists_its_municipalities_in_name_order() -> None:
    options = municipalities_of("bz")
    assert len(options) == 116
    assert "Bolzano/Bozen" in {o.name for o in options}
    names = [fold(o.name) for o in options]
    assert names == sorted(names)
    # Every name offered is one the match accepts back, to the same municipality.
    for option in options:
        assert locate(option.name, "BZ", "IT").municipality_code == option.municipality_code
    assert municipalities_of("XX") == []
