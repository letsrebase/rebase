"""`pigrocrm.core.fiscal.pack` -- pure data, no session anywhere in this file.

REB-361, ported against REB-344 §8 (`IT_FLAT_RATE_PACK`'s two ceilings) and §9
(the rivalsa charge). The values are checked against `it-flat-rate.ts` in
`~/projects/personal/mastro` (commit `53ef2942`), not invented here.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from pigrocrm.core.fiscal.irpef_2026 import IRPEF_2026
from pigrocrm.core.fiscal.pack import (
    IT_COMPANY_PACK,
    IT_FLAT_RATE_PACK,
    IT_STANDARD_PACK,
    NON_IT_PACK,
    SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID,
    resolve_pack,
)


def test_the_pack_declares_exactly_the_two_flat_rate_ceilings() -> None:
    """€85,000, loses the regime from the following year; €100,000, loses it
    immediately -- `it-flat-rate.ts:128,161`, ported verbatim."""
    thresholds = {c.id: c.threshold for c in IT_FLAT_RATE_PACK.ceilings}
    assert thresholds == {
        "revenue_ceiling": Decimal("85000.00"),
        "immediate_exit_ceiling": Decimal("100000.00"),
    }
    consequences = {c.id: c.consequence for c in IT_FLAT_RATE_PACK.ceilings}
    assert consequences == {
        "revenue_ceiling": "leaves_next_year",
        "immediate_exit_ceiling": "leaves_immediately",
    }


def test_every_ceiling_uses_the_cash_received_calendar_year_basis() -> None:
    assert all(c.basis == "cash_received_calendar_year" for c in IT_FLAT_RATE_PACK.ceilings)


def test_the_social_security_surcharge_charge_is_four_percent_and_counts_toward_the_ceiling() -> (
    None
):
    """4% (`it-flat-rate.ts:278`'s `basisPoints: 400`): counts toward the ceiling, and in the
    coefficient base too (REB-1100), which is why the charge has no second flag."""
    charge = IT_FLAT_RATE_PACK.charge(SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID, 2026)
    assert charge.rate == Decimal("0.04")
    assert charge.count_by_the_limit is True


def test_the_social_security_surcharge_charge_carries_its_legal_basis_in_its_own_description() -> (
    None
):
    """The exact text the invoice-line-construction step writes, and the one marker
    `ceiling.py` reads back -- see `pack.py::StatutoryCharge`'s own docstring for why
    `description`, not `vat_nature`/`legal_reference`, carries this."""
    charge = IT_FLAT_RATE_PACK.charge(SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID, 2026)
    assert "art. 1, comma 212, legge 662/1996" in charge.description_line


def test_an_unknown_charge_id_is_refused_rather_than_returning_none() -> None:
    with pytest.raises(KeyError):
        IT_FLAT_RATE_PACK.charge("a_charge_that_does_not_exist", 2026)


def test_resolve_pack_finds_every_shipped_pack() -> None:
    assert resolve_pack("it-flat-rate", "1") is IT_FLAT_RATE_PACK
    assert resolve_pack("it-standard", "1") is IT_STANDARD_PACK
    assert resolve_pack("non-it", "1") is NON_IT_PACK


def test_only_the_flat_rate_declares_to_ceiling_or_to_charge() -> None:
    """REB-760: the 85,000 and 100,000 euro thresholds and the social-security surcharge are the
    flat-rate regime's alone. The ordinary regime and the foreign pack declare neither, so a
    ceiling is a fact read off the pack and never a branch on the regime code."""
    assert [c.id for c in IT_FLAT_RATE_PACK.ceilings] == [
        "revenue_ceiling",
        "immediate_exit_ceiling",
    ]
    assert IT_STANDARD_PACK.ceilings == () and IT_STANDARD_PACK.charges == ()
    assert NON_IT_PACK.ceilings == () and NON_IT_PACK.charges == ()


def test_resolve_pack_refuses_an_unknown_id_or_version() -> None:
    with pytest.raises(KeyError):
        resolve_pack("generic", "1")
    with pytest.raises(KeyError):
        resolve_pack("it-flat-rate", "0")
    with pytest.raises(KeyError):
        resolve_pack("it-standard", "0")


def test_the_pack_is_frozen_data_with_no_database_import() -> None:
    """Asserted so nobody quietly turns this back into a DB row: REB-344 §8's own
    decision is "no new DB row for the pack itself." A frozen dataclass refuses
    attribute assignment, which is the closest a plain object gets to that guarantee."""
    with pytest.raises(AttributeError):
        IT_FLAT_RATE_PACK.id = "something-else"  # type: ignore[misc]


# --- the yearly sections (REB-1083) ------------------------------------------------------


def test_a_ceiling_read_for_a_year_before_a_change_reads_the_older_value() -> None:
    revenue = IT_FLAT_RATE_PACK.ceilings[0]
    raised = replace(revenue, from_year=2030, threshold=Decimal("100000.00"))
    pack = replace(IT_FLAT_RATE_PACK, ceilings=(revenue, raised, IT_FLAT_RATE_PACK.ceilings[1]))
    by_id = {
        year: {c.id: c.threshold for c in pack.ceilings_for(year)} for year in (2023, 2029, 2030)
    }
    assert by_id[2029]["revenue_ceiling"] == Decimal("85000.00")
    assert by_id[2030]["revenue_ceiling"] == Decimal("100000.00")
    assert by_id[2023]["immediate_exit_ceiling"] == Decimal("100000.00")
    assert [c.id for c in pack.ceilings_for(2030)] == ["revenue_ceiling", "immediate_exit_ceiling"]


def test_a_year_before_the_first_cited_entry_has_no_ceiling_and_no_charge() -> None:
    assert IT_FLAT_RATE_PACK.ceilings_for(2022) == ()
    assert len(IT_FLAT_RATE_PACK.ceilings_for(2023)) == 2
    assert IT_FLAT_RATE_PACK.charges_for(2011) == ()
    with pytest.raises(KeyError):
        IT_FLAT_RATE_PACK.charge(SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID, 2011)


def test_ceiling_entries_of_one_id_must_have_increasing_from_year() -> None:
    revenue = IT_FLAT_RATE_PACK.ceilings[0]
    with pytest.raises(ValueError, match="increasing from_year"):
        replace(IT_FLAT_RATE_PACK, ceilings=(replace(revenue, from_year=2030), revenue))


def test_irpef_and_chamber_fee_entries_are_read_by_exact_year_and_ordered() -> None:
    assert IT_STANDARD_PACK.irpef_for(2026) is IRPEF_2026
    assert IT_STANDARD_PACK.irpef_for(2027) is None and IT_STANDARD_PACK.irpef_for(2025) is None
    assert IT_COMPANY_PACK.chamber_fee_for(2026) is not None
    assert IT_COMPANY_PACK.chamber_fee_for(2027) is None
    with pytest.raises(ValueError, match="IRPEF entries"):
        replace(IT_STANDARD_PACK, irpef=(replace(IRPEF_2026, year=2027), IRPEF_2026))
    fee = IT_COMPANY_PACK.chamber_fees[0]
    with pytest.raises(ValueError, match="chamber fee entries"):
        replace(IT_COMPANY_PACK, chamber_fees=(replace(fee, year=2027), fee))
