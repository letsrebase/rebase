"""A regime decides three things and nothing else (spec 7.2): the default rate on a
line, the Natura/RiferimentoNormativo pair, and whether the stamp duty applies.

`ORDINARY` (RF01) ships with the product but is exercised only by tests and by
whoever changes regime: it exists so the rounding rules of spec 6.1 -- which the
forfettario reduces to zero -- are verifiable behaviour rather than documentation.
"""

from decimal import Decimal

import pytest

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.regime import FLAT_RATE, ORDINARY, frozen_pack_id, resolve_regime
from pigrocrm.core.fiscal.schemas import (
    COUNTRIES_UE,
    DEFAULT_LEGAL_REFERENCE,
    LEGAL_REFERENCE_EXTRA_UE,
    LEGAL_REFERENCE_UE,
    FiscalSnapshot,
)
from pigrocrm.core.invoices.totals import RecapGroup


def _profile(**overrides: object) -> FiscalSnapshot:
    base: dict[str, object] = {
        "regime_code": "RF19",
        "default_vat_rate": Decimal("0.00"),
        "default_vat_nature": "N2.2",
        "legal_reference": DEFAULT_LEGAL_REFERENCE,
        "apply_stamp_duty": True,
        "stamp_duty_threshold": Decimal("77.47"),
        "stamp_duty_amount": Decimal("2.00"),
        "payment_terms": "TP02",
        "payment_method": "MP05",
        "due_days": 30,
        "iban": "IT60X0542811101000000123456",
    }
    base.update(overrides)
    return FiscalSnapshot(**base)  # type: ignore[arg-type]


def _group(rate: str, taxable_amount: str, vat_nature: str | None) -> RecapGroup:
    return RecapGroup(
        vat_rate=Decimal(rate),
        vat_nature=vat_nature,
        legal_reference=None,
        taxable_amount=Decimal(taxable_amount),
        vat_amount=Decimal("0.00"),
    )


def test_resolve_regime_picks_the_strategy_from_the_code() -> None:
    assert resolve_regime("RF19", "it-flat-rate") is FLAT_RATE
    assert resolve_regime("RF01", "it-standard") is ORDINARY


def test_a_regime_belongs_to_its_own_pack_only() -> None:
    """REB-760: `RF01` is the ordinary regime of `it-standard` and `RF19` the
    forfettario of `it-flat-rate`. The pairing is refused both ways, naming the field and
    the codes the pack does carry, so an ordinary azienda is never held to the
    forfettario's ceilings by a pack it was left on."""
    with pytest.raises(ValidationFailed) as ordinary:
        resolve_regime("RF01", "it-flat-rate")
    assert ordinary.value.details["field"] == "regime_code"
    assert "it-standard" in ordinary.value.message
    with pytest.raises(ValidationFailed) as flat_rate:
        resolve_regime("RF19", "it-standard")
    assert flat_rate.value.details["field"] == "regime_code"
    assert "it-flat-rate" in flat_rate.value.message


def test_a_frozen_snapshot_of_the_ordinary_regime_reads_as_its_own_pack() -> None:
    """An invoice issued before REB-760 froze `RF01` beside `it-flat-rate`; the register
    names the pair as `it-standard`, and every other pair as it was frozen."""
    assert frozen_pack_id("RF01", "it-flat-rate") == "it-standard"
    assert frozen_pack_id("RF01", "it-standard") == "it-standard"
    assert frozen_pack_id("RF19", "it-flat-rate") == "it-flat-rate"
    assert frozen_pack_id(None, "non-it") == "non-it"


def test_an_unknown_regime_code_names_the_field() -> None:
    with pytest.raises(ValidationFailed) as caught:
        resolve_regime("RF07", "it-flat-rate")
    assert caught.value.details["entity"] == "fiscal_profile"
    assert caught.value.details["field"] == "regime_code"


def test_a_code_that_is_not_a_regime_at_all_is_refused_by_fullmatch() -> None:
    """`.fullmatch`, never `.match` with `$`: "RF19\\n" is 5 characters and would
    reach a String(4) column as a raw DataError."""
    with pytest.raises(ValidationFailed):
        resolve_regime("RF19\n", "it-flat-rate")


def test_flat_rate_forces_zero_rate_with_the_vat_nature_and_the_reference() -> None:
    rate, vat_nature, reference = FLAT_RATE.resolve_line_vat(
        None, _profile(), country_customer="IT"
    )
    assert rate == Decimal("0.00")
    assert vat_nature == "N2.2"
    assert reference == DEFAULT_LEGAL_REFERENCE


def test_flat_rate_gives_an_extra_eu_customer_n_2_1_and_operazione_not_soggetta() -> None:
    """A service to a business established outside Italy is outside the scope of
    Italian VAT (art. 7-ter DPR 633/1972), which the SdI codes as `N2.1`; `N2.2` and
    the L. 190/2014 declaration are the domestic answer. ORB-32: the accountant's tool
    issued 13/2026 to a GB customer with N2.1 while this strategy stamped N2.2 on
    every line regardless of the customer's country. Outside the EU the annotation is
    the one of art. 21 c. 6-bis lett. b), "operazione non soggetta"."""
    rate, vat_nature, reference = FLAT_RATE.resolve_line_vat(
        None, _profile(), country_customer="GB"
    )
    assert rate == Decimal("0.00")
    assert vat_nature == "N2.1"
    assert reference == LEGAL_REFERENCE_EXTRA_UE
    assert "non soggetta" in reference
    assert "7-ter" in reference
    assert "DPR 633/1972" in reference
    assert "forfettario" in reference


def test_flat_rate_gives_an_eu_customer_n_2_1_and_inversione_contabile() -> None:
    """Art. 21 c. 6-bis lett. a): a taxable customer in another member state gets the
    same `N2.1` but the annotation "inversione contabile", because that customer
    self-assesses the VAT at home. The wording is the only thing that differs."""
    rate, vat_nature, reference = FLAT_RATE.resolve_line_vat(
        None, _profile(), country_customer="FR"
    )
    assert rate == Decimal("0.00")
    assert vat_nature == "N2.1"
    assert reference == LEGAL_REFERENCE_UE
    assert "Inversione contabile" in reference
    assert "7-ter" in reference


def test_the_eu_set_is_the_twenty_seven_member_states() -> None:
    assert len(COUNTRIES_UE) == 27
    assert {"IT", "FR", "DE", "GR", "IE"} <= COUNTRIES_UE
    assert not {"GB", "CH", "US", "NO", "EL"} & COUNTRIES_UE


def test_the_profile_defaults_do_not_override_the_non_resident_answer() -> None:
    """`default_vat_nature` and `legal_reference` on the profile describe the
    domestic case, which is the only one a profile can configure."""
    profile = _profile(default_vat_nature="N2.2", legal_reference="testo del profilo")
    assert FLAT_RATE.resolve_line_vat(None, profile, country_customer="FR") == (
        Decimal("0.00"),
        "N2.1",
        LEGAL_REFERENCE_UE,
    )
    assert FLAT_RATE.resolve_line_vat(None, profile, country_customer="US") == (
        Decimal("0.00"),
        "N2.1",
        LEGAL_REFERENCE_EXTRA_UE,
    )


def test_the_country_is_compared_after_normalisation() -> None:
    """`customers.country` is stored as typed; a lowercase or padded `it` is still
    Italy, not a foreign customer, and so is a lowercase `fr` still France."""
    for country in ("IT", "it", " It "):
        assert FLAT_RATE.resolve_line_vat(None, _profile(), country_customer=country)[1] == "N2.2"
    assert FLAT_RATE.resolve_line_vat(None, _profile(), country_customer=" fr ")[2] == (
        LEGAL_REFERENCE_UE
    )


def test_an_empty_country_is_italy_not_a_foreign_customer() -> None:
    """`CustomerCreate.country` has no minimum length, so `""` can be stored. The
    column's default is `IT`, and `_buyer` in the exporter reads an empty value
    as Italy; the strategy reads it the same way rather than answering `N2.1`."""
    for empty in ("", "   "):
        assert FLAT_RATE.resolve_line_vat(None, _profile(), country_customer=empty) == (
            Decimal("0.00"),
            "N2.2",
            DEFAULT_LEGAL_REFERENCE,
        )


@pytest.mark.parametrize("text", [LEGAL_REFERENCE_UE, LEGAL_REFERENCE_EXTRA_UE])
def test_the_non_resident_references_fit_the_fpr12_width(text: str) -> None:
    """`RiferimentoNormativo` is `String100LatinType`: a longer text would be refused
    by the exporter after the number is spent, which is exactly the failure the
    pre-issue checks exist to prevent."""
    assert len(text) <= 100


def test_flat_rate_accepts_an_explicit_zero_and_refuses_anything_else() -> None:
    """Two SdI checks applied as a pair: a zero rate without a Natura is rejected,
    and a Natura with a non-zero rate is rejected. Whoever does not know that
    discovers the second only after fixing the first."""
    assert FLAT_RATE.resolve_line_vat(Decimal("0.00"), _profile(), country_customer="IT")[
        0
    ] == Decimal("0.00")
    with pytest.raises(ValidationFailed) as caught:
        FLAT_RATE.resolve_line_vat(Decimal("22.00"), _profile(), country_customer="IT")
    assert caught.value.details["field"] == "vat_rate"


def test_flat_rate_applies_the_stamp_duty_only_above_the_threshold() -> None:
    """Spec 14.1's two boundary cases: 77.47 is not above the threshold, 77.48 is."""
    profile = _profile()
    assert FLAT_RATE.stamp_duty([_group("0.00", "77.47", "N2.2")], profile) == Decimal("0.00")
    assert FLAT_RATE.stamp_duty([_group("0.00", "77.48", "N2.2")], profile) == Decimal("2.00")


def test_flat_rate_honours_apply_stamp_duty_false() -> None:
    profile = _profile(apply_stamp_duty=False)
    assert FLAT_RATE.stamp_duty([_group("0.00", "5000.00", "N2.2")], profile) == Decimal("0.00")


def test_the_stamp_duty_looks_only_at_the_untaxed_base() -> None:
    """`DatiBollo` is due on the amount not subject to VAT. A taxed group must not
    push a mostly-taxed invoice over the threshold."""
    profile = _profile()
    groups = [_group("22.00", "5000.00", None), _group("0.00", "10.00", "N2.2")]
    assert FLAT_RATE.stamp_duty(groups, profile) == Decimal("0.00")


def test_ordinary_uses_the_requested_rate_with_no_vat_nature() -> None:
    profile = _profile(
        regime_code="RF01",
        default_vat_rate=Decimal("22.00"),
        default_vat_nature=None,
        legal_reference=None,
    )
    assert ORDINARY.resolve_line_vat(Decimal("10.00"), profile, country_customer="IT") == (
        Decimal("10.00"),
        None,
        None,
    )
    assert ORDINARY.resolve_line_vat(None, profile, country_customer="IT") == (
        Decimal("22.00"),
        None,
        None,
    )


def test_ordinary_refuses_to_zero_rate_because_it_has_no_vat_nature_to_pair_with_it() -> None:
    profile = _profile(
        regime_code="RF01",
        default_vat_rate=Decimal("22.00"),
        default_vat_nature=None,
        legal_reference=None,
    )
    with pytest.raises(ValidationFailed) as caught:
        ORDINARY.resolve_line_vat(Decimal("0.00"), profile, country_customer="IT")
    assert caught.value.details["field"] == "vat_rate"


def test_ordinary_never_charges_the_stamp_duty() -> None:
    profile = _profile(regime_code="RF01", default_vat_nature=None)
    assert ORDINARY.stamp_duty([_group("22.00", "9999.00", None)], profile) == Decimal("0.00")
