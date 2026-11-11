"""The municipal IRPEF surcharge of every municipality (REB-1063).

The three municipalities below are checked against the Dipartimento delle Finanze's own rows,
quoted in the comments exactly as the downloaded lists print them (read 2026-11-11).
"""

import csv
import importlib.util
import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import pytest

from pigrocrm.core.fiscal.law_municipal_surcharge import (
    DATASETS,
    MUNICIPAL_SURCHARGE,
)
from pigrocrm.core.fiscal.pack import (
    IT_COMPANY_PACK,
    IT_FLAT_RATE_PACK,
    IT_STANDARD_PACK,
    NON_IT_PACK,
)
from pigrocrm.core.places.locate import locate
from pigrocrm.core.places.municipalities import municipality_list

D = Decimal
LAW = MUNICIPAL_SURCHARGE

# The Finanze's own rows of the three municipalities below, exactly as the lists printed them
# when they were downloaded (2026-11-11), the header included. The importer is run on them and
# its answer is held against the shipped rows, so the three are checked against the file the
# Department publishes and not against what this suite assumes.
RAW_HEADER = (
    "CODICE_CATASTALE;COMUNE;PR;NUMERO_DELIBERA;DATA_DELIBERA;DATA_PUBBLICAZIONE;NOTE;MULTIALIQ;"
    + ";".join(
        f"ALIQUOTA{suffix};FASCIA{suffix}" for suffix in [""] + [f"_{n}" for n in range(2, 13)]
    )
    + ";FLAG_NUOVA;IMPORTO_ESENTE"
)
RAW_ROWS = {
    (2025, "F205"): (
        "F205;MILANO;MI;46;28/09/2020;20/12/2025;CONFERMA;SI;0;Esenzione per redditi imponibili "
        "fino a euro 23.000,00;,8;Aliquota unica;;;;;;;;;;;;;;;;;;;;;2;23000"
    ),
    (2025, "H501"): (
        "H501;ROMA;RM;186;19/12/2024;24/01/2025;MODIFICA;SI;0;Esenzione per redditi imponibili "
        "fino a euro 14.000,00;,9;Aliquota unica;;;;;;;;;;;;;;;;;;;;;2;14000"
    ),
    (2026, "A005"): (
        "A005;ABBADIA LARIANA;LC;43;29/12/2025;29/09/2026;CONFERMA;SI;0;Esenzione per redditi "
        "imponibili fino a euro 15.000,00;,76;Applicabile a scaglione di reddito fino a euro "
        "28.000,00;,77;Applicabile a scaglione di reddito da euro 28.000,01 fino a euro "
        "50.000,00;,8;Applicabile a scaglione di reddito oltre euro 50.000,00;;;;;;;;;;;;;;;;;"
        "4;15000"
    ),
}


IMPORTER = Path(__file__).resolve().parents[3] / "scripts" / "import_municipal_surcharge.py"


@lru_cache(maxsize=1)
def importer():  # noqa: ANN201
    spec = importlib.util.spec_from_file_location("import_municipal_surcharge", IMPORTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("year", "code"), sorted(RAW_ROWS))
def test_the_shipped_row_is_what_the_importer_makes_of_the_departments_row(
    year: int, code: str
) -> None:
    converted, _ = importer().convert(RAW_HEADER + "\n" + RAW_ROWS[(year, code)], final=True)
    shipped = LAW.lookup(year, code)
    assert shipped is not None and len(converted) == 1
    row = converted[0]
    assert row["status"] == shipped.status == "adopted"
    assert row["name"] == shipped.name and row["province"] == shipped.province
    assert D(row["exemption_up_to"]) == shipped.exemption_up_to
    assert row["brackets"] == "|".join(
        f"{'' if b.up_to is None else b.up_to}:{b.rate}" for b in shipped.brackets
    )
    assert (row["deliberation_number"], row["deliberation_date"], row["published"]) == (
        shipped.deliberation_number,
        shipped.deliberation_date,
        shipped.published,
    )


def test_the_pack_carries_the_lists_for_the_ordinary_person_only() -> None:
    assert IT_STANDARD_PACK.municipal_surcharge is LAW
    for other in (IT_FLAT_RATE_PACK, IT_COMPANY_PACK, NON_IT_PACK):
        assert other.municipal_surcharge is None


def test_two_lists_are_shipped_and_say_where_they_came_from() -> None:
    assert LAW.years() == (2025, 2026)
    for year in (2025, 2026):
        source = json.loads((DATASETS / f"municipal_surcharge_{year}.source.json").read_text())
        assert source["tax_year"] == year
        assert source["url"].endswith(f"download.php?anno={year}")
        assert source["downloaded"] == "2026-11-11" and len(source["sha256"]) == 64
        dataset = LAW.for_year(year)
        assert dataset is not None
        assert len(dataset.rows) == source["rows"]
        counts: dict[str, int] = {}
        for row in dataset.rows.values():
            counts[row.status] = counts.get(row.status, 0) + 1
        assert counts == source["by_status"]


def test_the_2025_list_is_final_and_the_2026_list_is_not_yet() -> None:
    """The Finanze's own rule: until 20 December a municipality with no deliberation shows
    `0*` whether it has one to come or never instituted the surcharge, so 2026 read on 11
    November has no figure for 3,793 of them, Milano and Roma among them."""
    final_2025, partial_2026 = LAW.for_year(2025), LAW.for_year(2026)
    assert final_2025 is not None and partial_2026 is not None
    assert final_2025.final and not partial_2026.final
    assert not any(r.status == "pending" for r in final_2025.rows.values())
    assert not any(r.status == "none" for r in partial_2026.rows.values())
    assert sum(r.status == "pending" for r in partial_2026.rows.values()) == 3_793


def test_every_municipality_of_istats_list_is_in_the_lists_under_the_same_code() -> None:
    """REB-1069's `Place.cadastral_code` leads to a row: no municipality of ISTAT's list of 21
    February 2026 is missing from the 2026 list, and none but Castegnero Nanto (born that day)
    from the 2025 one."""
    istat = {m.cadastral_code for m in municipality_list().municipalities}
    for year, missing in ((2026, set()), (2025, {"M439"})):
        dataset = LAW.for_year(year)
        assert dataset is not None
        assert istat - set(dataset.rows) == missing


def test_a_legal_entity_address_leads_to_its_row() -> None:
    place = locate("Milano", "MI", "IT")
    assert place.cadastral_code is not None
    assert LAW.rate(2025, place.cadastral_code, D(30000)) == D("0.8")


# -- Milano, from the 2025 list -----------------------------------------------------------
def test_milano_2025_is_0_8_percent_above_an_exemption_of_23000() -> None:
    row = LAW.lookup(2025, "F205")
    assert row is not None
    assert (row.name, row.province, row.status) == ("MILANO", "MI", "adopted")
    assert row.exemption_up_to == D(23000)
    assert [(b.up_to, b.rate) for b in row.brackets] == [(None, D("0.8"))]
    assert (row.deliberation_number, row.deliberation_date, row.published) == (
        "46",
        "2020-09-28",
        "2025-12-20",
    )
    assert LAW.rate(2025, "F205", D(0)) == 0
    assert LAW.rate(2025, "F205", D(23000)) == 0
    assert LAW.rate(2025, "F205", D("23000.01")) == D("0.8")
    assert LAW.rate(2025, "F205", D(500000)) == D("0.8")


# -- Roma, from the 2025 list -------------------------------------------------------------
def test_roma_2025_is_0_9_percent_above_an_exemption_of_14000() -> None:
    row = LAW.lookup(2025, "H501")
    assert row is not None and row.exemption_up_to == D(14000)
    assert [(b.up_to, b.rate) for b in row.brackets] == [(None, D("0.9"))]
    assert LAW.rate(2025, "H501", D(14000)) == 0
    assert LAW.rate(2025, "H501", D("14000.01")) == D("0.9")


# -- Abbadia Lariana (LC), a small one with three rates, in both lists -----------------------
@pytest.mark.parametrize("year", [2025, 2026])
@pytest.mark.parametrize(
    ("income", "rate"),
    [
        ("0", "0"),
        ("15000", "0"),
        ("15000.01", "0.76"),
        ("28000", "0.76"),
        ("28000.01", "0.77"),
        ("50000", "0.77"),
        ("50000.01", "0.8"),
        ("120000", "0.8"),
    ],
)
def test_abbadia_lariana_brackets(year: int, income: str, rate: str) -> None:
    assert LAW.rate(year, "A005", D(income)) == D(rate)


def test_abbadia_lariana_deliberations_are_each_years_own() -> None:
    assert LAW.lookup(2026, "A005").deliberation_number == "43"  # type: ignore[union-attr]
    assert LAW.lookup(2025, "A005").deliberation_number == "7"  # type: ignore[union-attr]


# -- what is not a figure --------------------------------------------------------------------
def test_milano_2026_has_no_figure_yet() -> None:
    """F205;MILANO;MI;;;;;NO;0*;...;0 in the 2026 list read on 11 November: no deliberation."""
    row = LAW.lookup(2026, "F205")
    assert row is not None and row.status == "pending" and row.brackets == ()
    assert LAW.rate(2026, "F205", D(30000)) is None
    assert LAW.rate(2026, "H501", D(30000)) is None


def test_a_municipality_that_never_instituted_it_owes_nothing_in_a_final_list() -> None:
    """A022;CERMES;BZ;...;0*: in the 2025 list, read after 20 December 2025, `0*` is «not
    instituted»."""
    row = LAW.lookup(2025, "A022")
    assert row is not None and row.status == "none"
    assert LAW.rate(2025, "A022", D(80000)) == 0


def test_a_missing_municipality_year_or_code_is_no_figure() -> None:
    assert LAW.lookup(2025, "ZZZZ") is None
    assert LAW.rate(2025, "ZZZZ", D(30000)) is None
    assert LAW.lookup(2024, "F205") is None
    assert LAW.rate(2027, "F205", D(30000)) is None
    assert LAW.lookup(2025, " f205 ") is not None


def test_a_row_the_publication_garbled_is_no_figure() -> None:
    """A112;AIRUNO;LC in 2026: the third bracket is published as starting at 15000,01 after one
    that ended at 28.000,00."""
    row = LAW.lookup(2026, "A112")
    assert row is not None and row.status == "irregular"
    assert LAW.rate(2026, "A112", D(30000)) is None


def test_a_negative_income_is_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        LAW.rate(2025, "F205", D(-1))


def test_the_general_exemption_is_read_from_its_sentence_when_the_lists_column_is_zero() -> None:
    """L219;TORINO;TO in 2025 is flag 0 with the amount column at 0, and its pair says «Esenzione
    per redditi imponibili fino a euro 11.790,00»: the threshold is the sentence's, and an
    income under it is not given a positive rate. A038 (Acquanegra sul Chiese), same shape,
    10,000."""
    row = LAW.lookup(2025, "L219")
    assert row is not None and row.exemption_up_to == D(11790) and not row.category_exemption
    assert LAW.rate(2025, "L219", D(10000)) == 0
    assert LAW.rate(2025, "L219", D(11790)) == 0
    assert LAW.rate(2025, "L219", D("11790.01")) == D("0.8")
    assert LAW.rate(2025, "L219", D(60000)) == D("1.2")
    assert LAW.rate(2025, "A038", D(8000)) == 0
    assert LAW.rate(2025, "A038", D(12000)) == D("0.55")


def test_a_category_exemption_is_flagged_and_the_general_rates_are_kept() -> None:
    """L245;TORRE ANNUNZIATA;NA in 2026 (flag 5): the only exemption is for pensioners, so no
    general threshold exists, the row says it has an exemption, and the rate is the general one."""
    row = LAW.lookup(2026, "L245")
    assert row is not None and row.category_exemption and row.exemption_up_to is None
    assert LAW.rate(2026, "L245", D(5000)) == D("0.8")


def test_every_adopted_row_ends_with_an_open_bracket_in_increasing_order() -> None:
    for year in LAW.years():
        dataset = LAW.for_year(year)
        assert dataset is not None
        for row in dataset.rows.values():
            if row.status != "adopted":
                assert row.brackets == ()
                continue
            uppers = [b.up_to for b in row.brackets]
            assert uppers[-1] is None and None not in uppers[:-1], row
            assert uppers[:-1] == sorted(set(u for u in uppers[:-1] if u is not None)), row
            assert all(0 <= b.rate <= D("1.5") for b in row.brackets), row
            assert row.exemption_up_to is None or row.exemption_up_to > 0


def test_the_csv_rows_are_in_the_importers_layout() -> None:
    for year in LAW.years():
        with (DATASETS / f"municipal_surcharge_{year}.csv").open(encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            assert reader.fieldnames == importer().FIELDS


# -- the importer's parser --------------------------------------------------------------------
def pairs(*items: tuple[str, str], flag: str = "1", exempt: str = "0") -> dict[str, str]:
    row = {name: "" for name in importer().RATE_COLUMNS + importer().BRACKET_COLUMNS}
    for (rate, text), rate_column, text_column in zip(
        items, importer().RATE_COLUMNS, importer().BRACKET_COLUMNS, strict=False
    ):
        row[rate_column], row[text_column] = rate, text
    return {**row, "FLAG_NUOVA": flag, "IMPORTO_ESENTE": exempt}


def test_the_parser_reads_a_single_rate_with_an_exemption() -> None:
    exemption, category, brackets = importer().parse_pairs(
        pairs(
            ("0", "Esenzione per redditi imponibili fino a euro 23.000,00"),
            (",8", "Aliquota unica"),
            flag="2",
            exempt="23000",
        )
    )
    assert (exemption, category, brackets) == (D(23000), False, [(None, D("0.8"))])


def test_the_parser_reads_rates_by_bracket_whichever_way_the_sentence_is_written() -> None:
    _, _, brackets = importer().parse_pairs(
        pairs(
            (",5", "Applicabile a scaglione di reddito fino a euro 15.000,00"),
            (",6", "APPLICABILE A SCAGLIONE DI REDDITO DA EURO 15.000,01 FINO A EURO 28.000,00"),
            (",7", "Applicabile a da 28.001 a 50.000"),
            (",8", "Applicabile a scaglione di reddito oltre euro 50.000,00"),
            flag="3",
        )
    )
    assert brackets == [
        (D(15000), D("0.5")),
        (D(28000), D("0.6")),
        (D(50000), D("0.7")),
        (None, D("0.8")),
    ]


def test_the_parser_flags_a_category_exemption_and_reads_a_general_one_whatever_the_flag() -> None:
    exemption, category, brackets = importer().parse_pairs(
        pairs(
            ("0", "Esenzione per pensionati con reddito fino a euro 7.500,00"),
            (",8", "Aliquota unica"),
            flag="5",
        )
    )
    assert (exemption, category, brackets) == (None, True, [(None, D("0.8"))])
    # Flag 0 and a zero amount column, the standard sentence: still the general exemption.
    exemption, category, _ = importer().parse_pairs(
        pairs(
            ("0", "Esenzione per redditi imponibili fino a euro 11.790,00 "),
            (",8", "Aliquota unica"),
            flag="0",
        )
    )
    assert (exemption, category) == (D(11790), False)
    # A flag-2 row whose municipality words its exemption its own way: the column is the threshold.
    exemption, category, _ = importer().parse_pairs(
        pairs(
            (
                "0",
                "Esenzione per tutti i contribuenti con redditi imponibili inferiori a 11.000,00",
            ),
            (",8", "Aliquota unica"),
            flag="2",
            exempt="11000",
        )
    )
    assert (exemption, category) == (D(11000), False)


@pytest.mark.parametrize(
    "bad",
    [
        pairs((",8", "Applicabile a scaglione di reddito a euro 15.000,00"), flag="3"),
        pairs(
            (",5", "Applicabile a scaglione di reddito fino a euro 15.000,00"),
            (",6", "Applicabile a scaglione di reddito da euro 20.000,00 fino a euro 28.000,00"),
            (",7", "Applicabile a scaglione di reddito oltre euro 28.000,00"),
            flag="3",
        ),
        pairs((",5", "Applicabile a scaglione di reddito fino a euro 15.000,00"), flag="3"),
        pairs((",8", "Aliquota unica"), flag="2", exempt="12000"),
        pairs(
            ("0", "Esenzione per redditi imponibili fino a euro 12.000,00"),
            (",8", "Aliquota unica"),
            flag="2",
            exempt="13000",
        ),
        # The standard words with an amount that does not read as one: no guessed threshold.
        pairs(
            ("0", "Esenzione per redditi imponibili fino a euro 12.000.00"),
            (",8", "Aliquota unica"),
            flag="0",
        ),
    ],
)
def test_the_parser_refuses_what_it_cannot_make_into_brackets(bad: dict[str, str]) -> None:
    with pytest.raises(importer().Irregular):
        importer().parse_pairs(bad)


# --- the amount (REB-1072) ----------------------------------------------------------------


def test_the_surcharge_is_the_brackets_by_slice_on_the_whole_income_above_the_exemption() -> None:
    """Albinea, 2026 list: «28000:0.55|50000:0.7|:0.8» above an exemption of 12,000. At or below the
    exemption nothing is due (art. 1 c. 11 D.L. 138/2011); above it the whole income is taxed
    slice by slice, as the IRPEF is."""
    albinea = LAW.lookup(2026, "A162")
    assert albinea is not None and albinea.exemption_up_to == D("12000")
    assert albinea.surcharge(D("12000")) == D("0.00")
    assert albinea.surcharge(D("12000.01")) == D("66.00")  # 12,000.01 x 0.55%, half up
    assert albinea.surcharge(D("28000")) == D("154.00")
    assert albinea.surcharge(D("50000")) == D("308.00")
    assert albinea.surcharge(D("60000")) == D("388.00")
    assert albinea.surcharge(D("0")) == D("0.00")
    with pytest.raises(ValueError, match="negative"):
        albinea.surcharge(D("-1"))


def test_a_flat_rate_municipality_is_its_rate_on_the_whole_income() -> None:
    palermo = LAW.lookup(2026, "G273")
    assert palermo is not None
    assert palermo.surcharge(D("50000")) == D("515.00")  # 1.03%


def test_a_municipality_with_no_surcharge_owes_zero_and_one_with_no_figure_has_none() -> None:
    never = LAW.lookup(2025, "A049")
    pending = LAW.lookup(2026, "F205")
    irregular = LAW.lookup(2026, "A112")
    assert never is not None and never.status == "none"
    assert never.surcharge(D("50000")) == D("0.00")
    assert pending is not None and pending.status == "pending"
    assert pending.surcharge(D("50000")) is None
    assert irregular is not None and irregular.status == "irregular"
    assert irregular.surcharge(D("50000")) is None
