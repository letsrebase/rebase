"""`pigrocrm.core.fiscal.pack` -- pure data, no session anywhere in this file.

REB-361, ported against REB-344 §8 (`IT_FLAT_RATE_PACK`'s two ceilings) and §9
(the rivalsa charge). The values are checked against `it-flat-rate.ts` in
`~/projects/personal/mastro` (commit `53ef2942`), not invented here.
"""

from decimal import Decimal

import pytest

from pigrocrm.core.fiscal.pack import (
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
        "soglia_ricavi": Decimal("85000.00"),
        "soglia_fuoriuscita_immediata": Decimal("100000.00"),
    }
    consequences = {c.id: c.consequence for c in IT_FLAT_RATE_PACK.ceilings}
    assert consequences == {
        "soglia_ricavi": "leaves_next_year",
        "soglia_fuoriuscita_immediata": "leaves_immediately",
    }


def test_every_ceiling_uses_the_cash_received_calendar_year_basis() -> None:
    assert all(c.basis == "cash_received_calendar_year" for c in IT_FLAT_RATE_PACK.ceilings)


def test_the_social_security_surcharge_charge_is_four_percent_and_tagged_ceiling_only() -> None:
    """4% (`it-flat-rate.ts:278`'s `basisPoints: 400`): counts toward the ceiling
    (REB-352 §2's citation) and never toward the coefficiente base (§6's resolved
    decision) -- the two booleans must disagree, not both be true or both false."""
    charge = IT_FLAT_RATE_PACK.charge(SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID)
    assert charge.rate == Decimal("0.04")
    assert charge.count_by_the_limit is True
    assert charge.count_by_the_coefficient is False


def test_the_social_security_surcharge_charge_carries_its_legal_basis_in_its_own_description() -> (
    None
):
    """The exact text the invoice-line-construction step writes, and the one marker
    `ceiling.py` reads back -- see `pack.py::StatutoryCharge`'s own docstring for why
    `description`, not `vat_nature`/`legal_reference`, carries this."""
    charge = IT_FLAT_RATE_PACK.charge(SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID)
    assert "art. 1, comma 212, legge 662/1996" in charge.description_line


def test_an_unknown_charge_id_is_refused_rather_than_returning_none() -> None:
    with pytest.raises(KeyError):
        IT_FLAT_RATE_PACK.charge("a_charge_that_does_not_exist")


def test_resolve_pack_finds_every_shipped_pack() -> None:
    assert resolve_pack("it-flat-rate", "1") is IT_FLAT_RATE_PACK
    assert resolve_pack("it-standard", "1") is IT_STANDARD_PACK
    assert resolve_pack("non-it", "1") is NON_IT_PACK


def test_only_the_flat_rate_declares_to_ceiling_or_to_charge() -> None:
    """REB-760: the 85,000 and 100,000 euro thresholds and the social-security surcharge are the
    flat-rate regime's alone. The ordinary regime and the foreign pack declare neither, so a
    ceiling is a fact read off the pack and never a branch on the regime code."""
    assert [c.id for c in IT_FLAT_RATE_PACK.ceilings] == [
        "soglia_ricavi",
        "soglia_fuoriuscita_immediata",
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
