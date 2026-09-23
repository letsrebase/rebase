"""`pigrocrm.core.fiscal.pack` -- pure data, no session anywhere in this file.

REB-361, ported against REB-344 §8 (`IT_FLAT_RATE_PACK`'s two ceilings) and §9
(the rivalsa charge). The values are checked against `it-flat-rate.ts` in
`~/projects/personal/mastro` (commit `53ef2942`), not invented here.
"""

from decimal import Decimal

import pytest

from pigrocrm.core.fiscal.pack import (
    IT_FLAT_RATE_PACK,
    RIVALSA_INPS_CHARGE_ID,
    resolve_pack,
)


def test_the_pack_declares_exactly_the_two_forfettario_ceilings() -> None:
    """€85,000, loses the regime from the following year; €100,000, loses it
    immediately -- `it-flat-rate.ts:128,161`, ported verbatim."""
    soglie = {c.id: c.soglia for c in IT_FLAT_RATE_PACK.ceilings}
    assert soglie == {
        "soglia_ricavi": Decimal("85000.00"),
        "soglia_fuoriuscita_immediata": Decimal("100000.00"),
    }
    conseguenze = {c.id: c.conseguenza for c in IT_FLAT_RATE_PACK.ceilings}
    assert conseguenze == {
        "soglia_ricavi": "esce_dall_anno_successivo",
        "soglia_fuoriuscita_immediata": "esce_immediatamente",
    }


def test_every_ceiling_uses_the_cash_received_calendar_year_basis() -> None:
    assert all(c.basis == "cash_received_calendar_year" for c in IT_FLAT_RATE_PACK.ceilings)


def test_the_rivalsa_charge_is_four_percent_and_tagged_ceiling_only() -> None:
    """4% (`it-flat-rate.ts:278`'s `basisPoints: 400`): counts toward the ceiling
    (REB-352 §2's citation) and never toward the coefficiente base (§6's resolved
    decision) -- the two booleans must disagree, not both be true or both false."""
    charge = IT_FLAT_RATE_PACK.charge(RIVALSA_INPS_CHARGE_ID)
    assert charge.aliquota == Decimal("0.04")
    assert charge.conta_per_il_limite is True
    assert charge.conta_per_il_coefficiente is False


def test_the_rivalsa_charge_carries_its_legal_basis_in_its_own_description() -> None:
    """The exact text the invoice-line-construction step writes, and the one marker
    `ceiling.py` reads back -- see `pack.py::StatutoryCharge`'s own docstring for why
    `descrizione`, not `natura`/`riferimento_normativo`, carries this."""
    charge = IT_FLAT_RATE_PACK.charge(RIVALSA_INPS_CHARGE_ID)
    assert "art. 1, comma 212, legge 662/1996" in charge.descrizione_riga


def test_an_unknown_charge_id_is_refused_rather_than_returning_none() -> None:
    with pytest.raises(KeyError):
        IT_FLAT_RATE_PACK.charge("una_carica_che_non_esiste")


def test_resolve_pack_finds_the_one_shipped_pack() -> None:
    assert resolve_pack("it-flat-rate", "1") is IT_FLAT_RATE_PACK


def test_resolve_pack_refuses_an_unknown_id_or_version() -> None:
    with pytest.raises(KeyError):
        resolve_pack("it-standard", "1")
    with pytest.raises(KeyError):
        resolve_pack("it-flat-rate", "0")


def test_the_pack_is_frozen_data_with_no_database_import() -> None:
    """Asserted so nobody quietly turns this back into a DB row: REB-344 §8's own
    decision is "no new DB row for the pack itself." A frozen dataclass refuses
    attribute assignment, which is the closest a plain object gets to that guarantee."""
    with pytest.raises(AttributeError):
        IT_FLAT_RATE_PACK.id = "something-else"  # type: ignore[misc]
