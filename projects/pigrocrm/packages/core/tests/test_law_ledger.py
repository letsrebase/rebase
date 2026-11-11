"""REB-1084: a year that is recorded in the law ledger keeps the law it was recorded with.

The first test is the gate itself: it recomputes every fingerprint of every shipped pack and names
the pack, the section and the year of any that moved. The rest prove the mechanism on fixture packs
(an edit fails, a new year passes, a correction lets a deliberate fix through), so none of them
depends on the shipped data staying as it is.
"""

import copy
from dataclasses import replace
from decimal import Decimal

import pytest

from pigrocrm.core.fiscal import law_ledger
from pigrocrm.core.fiscal.irpef_2026 import IRPEF_2026
from pigrocrm.core.fiscal.law_irpef import RateBracket
from pigrocrm.core.fiscal.pack import (
    IT_COMPANY_PACK,
    IT_FLAT_RATE_PACK,
    IT_STANDARD_PACK,
    FiscalPack,
)

STANDARD = "it-standard:1"


def _packs(standard: FiscalPack = IT_STANDARD_PACK) -> dict[str, FiscalPack]:
    return {STANDARD: standard}


def _recorded(standard: FiscalPack = IT_STANDARD_PACK) -> law_ledger.Ledger:
    """A ledger that records `standard` as it is, the way `--add-year` would."""
    ledger = law_ledger.empty_ledger()
    law_ledger.add_missing(ledger, _packs(standard))
    return ledger


def _edited_2026_bracket() -> FiscalPack:
    brackets = (RateBracket(Decimal("28000"), Decimal("24")), *IRPEF_2026.brackets[1:])
    return replace(IT_STANDARD_PACK, irpef=(replace(IRPEF_2026, brackets=brackets),))


def test_the_shipped_law_matches_the_committed_ledger() -> None:
    verdict = law_ledger.check(law_ledger.load())
    assert [str(violation) for violation in verdict.violations] == []


def test_every_shipped_pack_year_with_law_is_recorded() -> None:
    """A year added without `--add-year` would be unprotected: the ledger must hold it."""
    verdict = law_ledger.check(law_ledger.load())
    assert verdict.added == [], (
        f"run `python -m pigrocrm.core.fiscal.law_ledger --add-year`: {verdict.added}"
    )


def test_the_ledger_records_each_yearly_section_by_its_own_years() -> None:
    recorded = law_ledger.load()["fingerprints"]
    assert set(recorded[STANDARD]["irpef"]) == {"2026"}
    # The 2026 list is not final (its sidecar says so), so it is not recorded yet.
    assert set(recorded[STANDARD]["municipal_surcharge"]) == {"2025"}
    assert set(recorded["it-company:1"]["chamber_fee"]) == {"2026"}
    assert set(recorded["it-company:1"]["irap_rates"]) == {"2026"}
    assert set(recorded["it-flat-rate:1"]["ceilings"]) == {"2023", "2024", "2025", "2026"}


def test_editing_a_recorded_irpef_bracket_fails_and_names_the_year_and_the_section() -> None:
    verdict = law_ledger.check(_recorded(), _packs(_edited_2026_bracket()))
    assert [(v.pack, v.section, v.year) for v in verdict.violations] == [(STANDARD, "irpef", 2026)]
    assert "it-standard:1 / irpef / 2026" in str(verdict.violations[0])
    assert "correction" in verdict.violations[0].problem


def test_adding_a_2027_entry_is_an_addition_and_passes() -> None:
    ledger = _recorded()
    later = replace(IRPEF_2026, year=2027, brackets=(RateBracket(None, Decimal("20")),))
    pack = replace(IT_STANDARD_PACK, irpef=(IRPEF_2026, later))
    verdict = law_ledger.check(ledger, _packs(pack))
    assert verdict.violations == []
    assert (STANDARD, "irpef", 2027) in verdict.added
    # Recording it leaves what was recorded untouched.
    before = copy.deepcopy(ledger["fingerprints"][STANDARD]["irpef"])
    law_ledger.add_missing(ledger, _packs(pack))
    assert ledger["fingerprints"][STANDARD]["irpef"]["2026"] == before["2026"]
    assert set(ledger["fingerprints"][STANDARD]["irpef"]) == {"2026", "2027"}


def test_a_correction_with_its_source_and_reason_lets_a_deliberate_fix_through() -> None:
    ledger = _recorded()
    fixed = _packs(_edited_2026_bracket())
    law_ledger.add_correction(
        ledger, STANDARD, "irpef", 2026, "Gazzetta Ufficiale, correzione di errata", "typo", fixed
    )
    assert law_ledger.check(ledger, fixed).violations == []
    # The correction is for that fix only: the original data now differs from it.
    assert [(v.section, v.year) for v in law_ledger.check(ledger, _packs()).violations] == [
        ("irpef", 2026)
    ]


def test_a_correction_without_a_source_or_a_reason_is_refused() -> None:
    ledger = _recorded()
    fixed = _packs(_edited_2026_bracket())
    with pytest.raises(ValueError, match="source and a reason"):
        law_ledger.add_correction(ledger, STANDARD, "irpef", 2026, "", "typo", fixed)
    law_ledger.add_correction(ledger, STANDARD, "irpef", 2026, "a source", "a reason", fixed)
    ledger["corrections"][0]["reason"] = " "
    problems = [v.problem for v in law_ledger.check(ledger, fixed).violations]
    assert problems == ["a correction needs a source and a reason"]


def test_a_correction_for_a_year_the_ledger_does_not_record_is_refused() -> None:
    with pytest.raises(ValueError, match="no irpef law for 2030"):
        law_ledger.add_correction(_recorded(), STANDARD, "irpef", 2030, "s", "r", _packs())
    ledger = _recorded()
    ledger["corrections"].append(
        {
            "pack": STANDARD,
            "section": "irpef",
            "year": 2030,
            "source": "s",
            "reason": "r",
            "fingerprint": "x",
        }
    )
    problems = [v.problem for v in law_ledger.check(ledger, _packs()).violations]
    assert problems == ["a correction for a year the ledger does not record"]


def test_dropping_a_recorded_year_is_a_change_too() -> None:
    pack = replace(IT_STANDARD_PACK, irpef=())
    verdict = law_ledger.check(_recorded(), _packs(pack))
    assert [(v.section, v.year, v.problem) for v in verdict.violations] == [
        ("irpef", 2026, "a recorded year has no law any more")
    ]


def test_a_statutory_edit_shows_as_a_change_to_every_year_it_governs() -> None:
    """The 2026 `laws` entry carries into 2027 when only another section has 2027 law, so editing
    it changes both years, while a year that has its own entry would not move."""
    later = replace(IRPEF_2026, year=2027)
    base = replace(IT_STANDARD_PACK, irpef=(IRPEF_2026, later))
    ledger = _recorded(base)
    assert set(ledger["fingerprints"][STANDARD]["laws"]) == {"2026", "2027"}
    law = base.laws[0]
    assert law.advances is not None
    edited_law = replace(law, advances=replace(law.advances, first_advance_percent=Decimal("41")))
    verdict = law_ledger.check(ledger, _packs(replace(base, laws=(edited_law,))))
    assert sorted((v.section, v.year) for v in verdict.violations) == [
        ("laws", 2026),
        ("laws", 2027),
    ]


def test_a_new_year_in_a_table_inside_a_larger_entry_does_not_move_the_old_years() -> None:
    """The IRAP rates and the legal interest rates are year tables inside the company law and the
    payment rules: adding 2027 to them must be an addition, not a change of the entry."""
    law = IT_COMPANY_PACK.laws[0]
    assert law.company_tax is not None and law.company_tax.irap is not None
    irap = law.company_tax.irap
    next_table = replace(irap.regional_rates[0], year=2027)
    grown_law = replace(
        law,
        company_tax=replace(
            law.company_tax, irap=replace(irap, regional_rates=(*irap.regional_rates, next_table))
        ),
    )
    rules = IT_COMPANY_PACK.payment_rules[0]
    next_rate = replace(rules.shortfall.legal_interest[-1], year=2027)
    grown_rules = replace(
        rules,
        shortfall=replace(
            rules.shortfall, legal_interest=(*rules.shortfall.legal_interest, next_rate)
        ),
    )
    grown = replace(IT_COMPANY_PACK, laws=(grown_law,), payment_rules=(grown_rules,))
    key = "it-company:1"
    ledger = law_ledger.empty_ledger()
    law_ledger.add_missing(ledger, {key: IT_COMPANY_PACK})
    verdict = law_ledger.check(ledger, {key: grown})
    assert verdict.violations == []
    assert {(s, y) for _, s, y in verdict.added} >= {("irap_rates", 2027), ("legal_interest", 2027)}


def test_editing_a_ceiling_or_a_charge_is_caught_in_each_year_it_governs() -> None:
    packs = {"it-flat-rate:1": IT_FLAT_RATE_PACK}
    ledger = law_ledger.empty_ledger()
    law_ledger.add_missing(ledger, packs)
    edited = replace(
        IT_FLAT_RATE_PACK,
        ceilings=(replace(IT_FLAT_RATE_PACK.ceilings[0], threshold=Decimal("86000.00")),)
        + IT_FLAT_RATE_PACK.ceilings[1:],
    )
    violations = law_ledger.check(ledger, {"it-flat-rate:1": edited}).violations
    assert {(v.section, v.year) for v in violations} == {("ceilings", y) for y in range(2023, 2027)}


def test_the_fingerprint_is_stable_and_sees_a_citation() -> None:
    assert law_ledger.fingerprint(IRPEF_2026) == law_ledger.fingerprint(replace(IRPEF_2026))
    assert law_ledger.fingerprint(IRPEF_2026) != law_ledger.fingerprint(
        replace(IRPEF_2026, brackets_source=IRPEF_2026.brackets_source + " ")
    )
    assert law_ledger.fingerprint(Decimal("1.0")) != law_ledger.fingerprint(Decimal("1.00"))


def test_a_value_with_no_stable_form_is_refused_rather_than_hashed_by_address() -> None:
    with pytest.raises(TypeError, match="no stable form"):
        law_ledger.fingerprint(object())
