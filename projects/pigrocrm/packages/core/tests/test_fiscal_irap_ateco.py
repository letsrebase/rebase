"""REB-1086: the regional IRAP rows an srl's ATECO codes select, and the two points of D.L.
21/2026 on the energy activities of its table 1. Pure lookups: no database.

The figures under test are rows of the Dipartimento delle Finanze's 2026 dataset (`irp2026.csv`,
read 2026-11-12) and of the regional laws it cites; each test names the row it proves.
"""

from decimal import Decimal

import pytest

from pigrocrm.core.fiscal.law_irap import IRAP_LAW, IrapActivity
from pigrocrm.core.fiscal.law_irap_ateco import AtecoMatch, digits
from pigrocrm.core.fiscal.law_irap_ateco_lists import (
    EMILIA_ROMAGNA_ANNEX_A_ATECO_2007,
    TOSCANA_007_ATECO_2007,
    UMBRIA_002_ATECO_2007,
    VENETO_017_ATECO_2007,
)

M = Decimal


def _answer(region: str, ateco_2007: str | None = None, ateco_2025: str | None = None):  # type: ignore[no-untyped-def]
    answer = IRAP_LAW.rate_for(2026, region, IrapActivity(ateco_2007, ateco_2025))
    assert answer is not None
    return answer


# --- the data ------------------------------------------------------------------------------


def test_every_general_table_1_row_is_the_ordinary_rate_plus_two_points() -> None:
    """Art. 3 c. 1 D.L. 21/2026 raises the rate of table 1 by two points, and every region that
    publishes one general row for it publishes exactly that."""
    (table,) = IRAP_LAW.regional_rates
    checked = 0
    for region in table.rates:
        general = [row for row in region.ateco.rows if row.table_1 is True and not row.matches]
        for row in general:
            assert row.rate == region.rate + IRAP_LAW.table_1.points, (region.key, row.code)
            checked += 1
    # Veneto, Liguria, Emilia-Romagna, Toscana and Basilicata have no general row.
    assert checked == 16


def test_the_lists_are_complete_and_well_formed() -> None:
    assert len(EMILIA_ROMAGNA_ANNEX_A_ATECO_2007) == 1118
    assert len(set(EMILIA_ROMAGNA_ANNEX_A_ATECO_2007)) == 1118
    assert all(
        len(code) == 8 and code[2] == "." and code[5] == "."
        for code in EMILIA_ROMAGNA_ANNEX_A_ATECO_2007
    )
    assert len(VENETO_017_ATECO_2007) == 101
    assert len(TOSCANA_007_ATECO_2007) == 62
    assert len(UMBRIA_002_ATECO_2007) == 20


def test_a_list_entry_is_a_prefix_of_the_codes_it_covers() -> None:
    match = AtecoMatch("ateco_2007", ("52.21.2", "61", "35.11.00"))
    assert match.holds(digits("52.21.20"), None) is True
    assert match.holds(digits("52.21.10"), None) is False
    assert match.holds(digits("61.20.00"), None) is True
    assert match.holds(digits("35.11.00"), None) is True
    assert match.holds(digits("35.12.00"), None) is False
    assert match.holds(None, digits("35.11.00")) is None  # the 2007 code is not stated


# --- no code stated -----------------------------------------------------------------------


@pytest.mark.parametrize("region", ["01", "05", "07", "08", "09", "10", "17"])
def test_a_region_with_a_list_gives_no_rate_to_a_company_that_states_no_code(region: str) -> None:
    answer = _answer(region)
    assert answer.rate is None and answer.unanswered == "ateco_2007_needed"


@pytest.mark.parametrize(
    ("region", "ordinary"),
    [("03", "3.90"), ("12", "4.82"), ("11", "4.73"), ("TN", "2.68"), ("20", "2.93")],
)
def test_a_region_with_no_list_gives_its_ordinary_rate_and_says_table_1_was_not_checked(
    region: str, ordinary: str
) -> None:
    answer = _answer(region)
    assert answer.rate == M(ordinary) and answer.row is None
    assert answer.notes == ("table_1_not_checked",)


def test_an_energy_code_of_2007_alone_cannot_rule_table_1_out() -> None:
    answer = _answer("03", ateco_2007="35.11.00")
    assert answer.rate is None and answer.unanswered == "ateco_2025_needed"
    # A code outside the energy divisions can: the ordinary rate, and the note.
    assert _answer("03", ateco_2007="62.01.00").rate == M("3.90")


# --- table 1 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("region", "rate", "row"),
    [("03", "5.90", "016"), ("12", "6.82", "002"), ("TN", "4.68", "015"), ("20", "4.93", "009")],
)
def test_a_table_1_company_pays_the_regions_ordinary_rate_plus_two_points(
    region: str, rate: str, row: str
) -> None:
    answer = _answer(region, "35.11.00", "35.11.00")
    assert answer.rate == M(rate) and answer.row is not None and answer.row.code == row
    assert answer.on_table_1 is True and answer.notes == ()


def test_table_1_is_read_in_the_2025_classification() -> None:
    # 35.4 is a class of ATECO 2025 only; 35.13 of ATECO 2007 is not on table 1 by its own code.
    assert _answer("03", "35.13.00", "35.40.00").rate == M("5.90")
    assert _answer("03", "62.01.00", "62.10.00").rate == M("3.90")


def test_table_1_covers_only_its_two_years() -> None:
    assert IRAP_LAW.table_1.years == (2026, 2027)
    assert IRAP_LAW.rate_for(2027, "03") is None  # the pack holds no 2027 table yet


# --- Toscana ------------------------------------------------------------------------------


def test_a_tuscan_company_in_a_listed_activity_pays_4_82() -> None:
    answer = _answer("09", "62.01.00", "62.10.00")  # software, code 62 of row 007
    assert answer.rate == M("4.82") and answer.row is not None and answer.row.code == "007"


def test_a_tuscan_company_outside_the_list_pays_the_ordinary_3_90() -> None:
    answer = _answer("09", "10.11.00", "10.11.00")  # meat processing
    assert answer.rate == M("3.90") and answer.row is None


def test_the_tuscan_list_is_read_by_prefix_and_not_wider() -> None:
    assert _answer("09", "52.21.20", "52.21.20").rate == M("4.82")  # 52.21.2
    assert _answer("09", "52.21.10", "52.21.10").rate == M("3.90")  # not 52.21.2


def test_tuscany_publishes_no_row_for_a_table_1_company() -> None:
    answer = _answer("09", "06.10.00", "06.10.00")
    assert answer.rate is None and answer.unanswered == "table_1_unpublished"


def test_a_reduction_that_needs_state_aid_is_named_and_never_applied() -> None:
    answer = _answer("09", "77.11.00", "77.11.00")  # rental of cars: row 005, not on row 007
    assert answer.rate == M("3.90")
    assert [(note.code, note.rate, note.aid) for note in answer.reductions] == [
        ("005", M("2.98"), True)
    ]
    assert "reduction_exists" in answer.notes


# --- Emilia-Romagna -------------------------------------------------------------------------


def test_most_activities_of_emilia_romagna_pay_the_annex_rate_4_20() -> None:
    answer = _answer("08", "62.01.00", "62.10.00")
    assert answer.rate == M("4.20") and answer.row is not None and answer.row.code == "002"


def test_an_activity_off_the_annex_pays_the_ordinary_3_90() -> None:
    assert _answer("08", "85.59.30", "85.59.30").rate == M("3.90")  # training: not in the annex


def test_the_atecofin_2004_sectors_of_emilia_romagna_give_no_rate() -> None:
    answer = _answer("08", "35.30.00", "35.30.00")
    assert answer.rate is None and answer.unanswered == "list_not_decidable"
    assert answer.blocked_reason == "emilia_romagna_atecofin_2004"


def test_an_annex_activity_on_table_1_pays_row_003() -> None:
    answer = _answer("08", "06.10.00", "06.10.00")
    assert answer.rate == M("6.20") and answer.row is not None and answer.row.code == "003"


# --- Umbria, Veneto, Piemonte, Liguria, Basilicata -------------------------------------------


def test_umbria_pays_4_82_on_its_list_and_6_30_on_table_1() -> None:
    assert _answer("10", "68.20.01", "68.20.01").rate == M("4.82")  # real estate letting
    assert _answer("10", "62.01.00", "62.10.00").rate == M("4.30")
    table_1 = _answer("10", "06.10.00", "06.10.00")
    assert table_1.rate == M("6.30") and table_1.row is not None and table_1.row.code == "013"


def test_veneto_reads_its_two_lists_and_its_three_table_1_rows() -> None:
    assert _answer("05", "35.30.00", "35.30.00").rate == M("4.82")  # division 35, not table 1
    assert _answer("05", "20.11.00", "20.11.00").rate == M("4.55")  # row 017
    assert _answer("05", "62.01.00", "62.10.00").rate == M("4.08")
    assert _answer("05", None, "06.10.00").rate == M("6.08")  # 06: row 019
    assert _answer("05", None, "09.10.00").rate == M("6.55")  # 09.1: row 020
    assert _answer("05", None, "19.20.00").rate == M("6.82")  # 19.2: row 021


def test_piemonte_reads_its_retail_and_finance_rows_and_its_energy_row() -> None:
    assert _answer("01", "47.11.20", "47.11.20").rate == M("4.82")  # 47.1
    assert _answer("01", "62.01.00", "62.10.00").rate == M("3.90")
    assert _answer("01", "35.11.00", "35.11.00").rate == M("6.82")  # row 026
    assert _answer("01", "06.10.00", "06.10.00").rate == M("5.90")  # row 031


def test_liguria_reads_the_codes_of_its_energy_rows_in_ateco_2007() -> None:
    assert _answer("07", "35.14.00", "35.40.00").rate == M("6.82")  # row 014
    assert _answer("07", "35.12.00", "35.12.00").rate == M("5.90")  # row 012
    assert _answer("07", "19.20.10", "19.20.10").rate == M(
        "6.82"
    )  # 19.20.10 is row 014's, not 012's
    assert _answer("07", "53.10.00", "53.10.00").rate == M("4.82")  # row 006
    unknown = _answer("07", "62.01.00", "62.10.00")
    assert unknown.rate == M("3.90")
    # A table 1 company whose 2007 code the energy rows do not list gets no rate.
    assert _answer("07", "62.01.00", "35.11.00").unanswered == "table_1_unpublished"


def test_basilicata_decides_nothing_for_the_energy_sectors() -> None:
    assert _answer("17", "62.01.00", "62.10.00").rate == M("3.90")
    energy = _answer("17", "35.11.00", "35.11.00")
    assert energy.rate is None and energy.unanswered == "list_not_decidable"


# --- the period of art. 11 c. 4-bis.2 -------------------------------------------------------


def test_the_small_base_deduction_of_a_short_period_is_prorated_on_the_calendar_year() -> None:
    # 100 days with a base of 40,000: annualised 146,000, in the first row (8,000 a year),
    # which is 8,000 * 100 / 365 = 2,191.78 for the period.
    deduction = IRAP_LAW.small_base_deduction(M("40000"), 100, 365)
    assert deduction.quantize(M("0.01")) == M("2191.78")
    # A full calendar year is not prorated, in a leap year either.
    assert IRAP_LAW.small_base_deduction(M("40000"), 366, 366) == M("8000")
    # The row is chosen on the annual base: 100 days of 50,000 is 182,500 a year, above the limit.
    assert IRAP_LAW.small_base_deduction(M("50000"), 100, 365) == M("0")
