"""A regime decides three things and nothing else (spec 7.2): the default rate on a
line, the Natura/RiferimentoNormativo pair, and whether the stamp duty applies.

`ORDINARIO` (RF01) ships with the product but is exercised only by tests and by
whoever changes regime: it exists so the rounding rules of spec 6.1 -- which the
forfettario reduces to zero -- are verifiable behaviour rather than documentation.
"""

from decimal import Decimal

import pytest

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.regime import FORFETTARIO, ORDINARIO, frozen_pack_id, resolve_regime
from pigrocrm.core.fiscal.schemas import (
    DEFAULT_RIFERIMENTO_NORMATIVO,
    PAESI_UE,
    RIFERIMENTO_NORMATIVO_EXTRA_UE,
    RIFERIMENTO_NORMATIVO_UE,
    FiscalSnapshot,
)
from pigrocrm.core.invoices.totals import RiepilogoGroup


def _profile(**overrides: object) -> FiscalSnapshot:
    base: dict[str, object] = {
        "codice_regime": "RF19",
        "aliquota_iva_default": Decimal("0.00"),
        "natura_default": "N2.2",
        "riferimento_normativo": DEFAULT_RIFERIMENTO_NORMATIVO,
        "applica_bollo": True,
        "soglia_bollo": Decimal("77.47"),
        "importo_bollo": Decimal("2.00"),
        "condizioni_pagamento": "TP02",
        "modalita_pagamento": "MP05",
        "giorni_scadenza": 30,
        "iban": "IT60X0542811101000000123456",
    }
    base.update(overrides)
    return FiscalSnapshot(**base)  # type: ignore[arg-type]


def _group(aliquota: str, imponibile: str, natura: str | None) -> RiepilogoGroup:
    return RiepilogoGroup(
        aliquota_iva=Decimal(aliquota),
        natura=natura,
        riferimento_normativo=None,
        imponibile=Decimal(imponibile),
        imposta=Decimal("0.00"),
    )


def test_resolve_regime_picks_the_strategy_from_the_code() -> None:
    assert resolve_regime("RF19", "it-flat-rate") is FORFETTARIO
    assert resolve_regime("RF01", "it-standard") is ORDINARIO


def test_a_regime_belongs_to_its_own_pack_only() -> None:
    """REB-760: `RF01` is the ordinary regime of `it-standard` and `RF19` the
    forfettario of `it-flat-rate`. The pairing is refused both ways, naming the field and
    the codes the pack does carry, so an ordinary azienda is never held to the
    forfettario's ceilings by a pack it was left on."""
    with pytest.raises(ValidationFailed) as ordinary:
        resolve_regime("RF01", "it-flat-rate")
    assert ordinary.value.details["field"] == "codice_regime"
    assert "it-standard" in ordinary.value.message
    with pytest.raises(ValidationFailed) as forfettario:
        resolve_regime("RF19", "it-standard")
    assert forfettario.value.details["field"] == "codice_regime"
    assert "it-flat-rate" in forfettario.value.message


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
    assert caught.value.details["field"] == "codice_regime"


def test_a_code_that_is_not_a_regime_at_all_is_refused_by_fullmatch() -> None:
    """`.fullmatch`, never `.match` with `$`: "RF19\\n" is 5 characters and would
    reach a String(4) column as a raw DataError."""
    with pytest.raises(ValidationFailed):
        resolve_regime("RF19\n", "it-flat-rate")


def test_forfettario_forces_zero_rate_with_the_natura_and_the_reference() -> None:
    aliquota, natura, riferimento = FORFETTARIO.resolve_line_vat(
        None, _profile(), nazione_cliente="IT"
    )
    assert aliquota == Decimal("0.00")
    assert natura == "N2.2"
    assert riferimento == DEFAULT_RIFERIMENTO_NORMATIVO


def test_forfettario_gives_an_extra_eu_customer_n2_1_and_operazione_non_soggetta() -> None:
    """A service to a business established outside Italy is outside the scope of
    Italian VAT (art. 7-ter DPR 633/1972), which the SdI codes as `N2.1`; `N2.2` and
    the L. 190/2014 declaration are the domestic answer. ORB-32: the accountant's tool
    issued 13/2026 to a GB customer with N2.1 while this strategy stamped N2.2 on
    every line regardless of the customer's country. Outside the EU the annotation is
    the one of art. 21 c. 6-bis lett. b), "operazione non soggetta"."""
    aliquota, natura, riferimento = FORFETTARIO.resolve_line_vat(
        None, _profile(), nazione_cliente="GB"
    )
    assert aliquota == Decimal("0.00")
    assert natura == "N2.1"
    assert riferimento == RIFERIMENTO_NORMATIVO_EXTRA_UE
    assert "non soggetta" in riferimento
    assert "7-ter" in riferimento
    assert "DPR 633/1972" in riferimento
    assert "forfettario" in riferimento


def test_forfettario_gives_an_eu_customer_n2_1_and_inversione_contabile() -> None:
    """Art. 21 c. 6-bis lett. a): a taxable customer in another member state gets the
    same `N2.1` but the annotation "inversione contabile", because that customer
    self-assesses the VAT at home. The wording is the only thing that differs."""
    aliquota, natura, riferimento = FORFETTARIO.resolve_line_vat(
        None, _profile(), nazione_cliente="FR"
    )
    assert aliquota == Decimal("0.00")
    assert natura == "N2.1"
    assert riferimento == RIFERIMENTO_NORMATIVO_UE
    assert "Inversione contabile" in riferimento
    assert "7-ter" in riferimento


def test_the_eu_set_is_the_twenty_seven_member_states() -> None:
    assert len(PAESI_UE) == 27
    assert {"IT", "FR", "DE", "GR", "IE"} <= PAESI_UE
    assert not {"GB", "CH", "US", "NO", "EL"} & PAESI_UE


def test_the_profile_defaults_do_not_override_the_non_resident_answer() -> None:
    """`natura_default` and `riferimento_normativo` on the profile describe the
    domestic case, which is the only one a profile can configure."""
    profile = _profile(natura_default="N2.2", riferimento_normativo="testo del profilo")
    assert FORFETTARIO.resolve_line_vat(None, profile, nazione_cliente="FR") == (
        Decimal("0.00"),
        "N2.1",
        RIFERIMENTO_NORMATIVO_UE,
    )
    assert FORFETTARIO.resolve_line_vat(None, profile, nazione_cliente="US") == (
        Decimal("0.00"),
        "N2.1",
        RIFERIMENTO_NORMATIVO_EXTRA_UE,
    )


def test_the_country_is_compared_after_normalisation() -> None:
    """`customers.nazione` is stored as typed; a lowercase or padded `it` is still
    Italy, not a foreign customer, and so is a lowercase `fr` still France."""
    for nazione in ("IT", "it", " It "):
        assert FORFETTARIO.resolve_line_vat(None, _profile(), nazione_cliente=nazione)[1] == "N2.2"
    assert FORFETTARIO.resolve_line_vat(None, _profile(), nazione_cliente=" fr ")[2] == (
        RIFERIMENTO_NORMATIVO_UE
    )


def test_an_empty_country_is_italy_not_a_foreign_customer() -> None:
    """`CustomerCreate.nazione` has no minimum length, so `""` can be stored. The
    column's default is `IT`, and `_cessionario` in the exporter reads an empty value
    as Italy; the strategy reads it the same way rather than answering `N2.1`."""
    for vuoto in ("", "   "):
        assert FORFETTARIO.resolve_line_vat(None, _profile(), nazione_cliente=vuoto) == (
            Decimal("0.00"),
            "N2.2",
            DEFAULT_RIFERIMENTO_NORMATIVO,
        )


@pytest.mark.parametrize("testo", [RIFERIMENTO_NORMATIVO_UE, RIFERIMENTO_NORMATIVO_EXTRA_UE])
def test_the_non_resident_references_fit_the_fpr12_width(testo: str) -> None:
    """`RiferimentoNormativo` is `String100LatinType`: a longer text would be refused
    by the exporter after the number is spent, which is exactly the failure the
    pre-issue checks exist to prevent."""
    assert len(testo) <= 100


def test_forfettario_accepts_an_explicit_zero_and_refuses_anything_else() -> None:
    """Two SdI checks applied as a pair: a zero rate without a Natura is rejected,
    and a Natura with a non-zero rate is rejected. Whoever does not know that
    discovers the second only after fixing the first."""
    assert FORFETTARIO.resolve_line_vat(Decimal("0.00"), _profile(), nazione_cliente="IT")[
        0
    ] == Decimal("0.00")
    with pytest.raises(ValidationFailed) as caught:
        FORFETTARIO.resolve_line_vat(Decimal("22.00"), _profile(), nazione_cliente="IT")
    assert caught.value.details["field"] == "aliquota_iva"


def test_forfettario_applies_the_stamp_duty_only_above_the_threshold() -> None:
    """Spec 14.1's two boundary cases: 77.47 is not above the threshold, 77.48 is."""
    profile = _profile()
    assert FORFETTARIO.bollo([_group("0.00", "77.47", "N2.2")], profile) == Decimal("0.00")
    assert FORFETTARIO.bollo([_group("0.00", "77.48", "N2.2")], profile) == Decimal("2.00")


def test_forfettario_honours_applica_bollo_false() -> None:
    profile = _profile(applica_bollo=False)
    assert FORFETTARIO.bollo([_group("0.00", "5000.00", "N2.2")], profile) == Decimal("0.00")


def test_the_stamp_duty_looks_only_at_the_untaxed_base() -> None:
    """`DatiBollo` is due on the amount not subject to VAT. A taxed group must not
    push a mostly-taxed invoice over the threshold."""
    profile = _profile()
    groups = [_group("22.00", "5000.00", None), _group("0.00", "10.00", "N2.2")]
    assert FORFETTARIO.bollo(groups, profile) == Decimal("0.00")


def test_ordinario_uses_the_requested_rate_with_no_natura() -> None:
    profile = _profile(
        codice_regime="RF01",
        aliquota_iva_default=Decimal("22.00"),
        natura_default=None,
        riferimento_normativo=None,
    )
    assert ORDINARIO.resolve_line_vat(Decimal("10.00"), profile, nazione_cliente="IT") == (
        Decimal("10.00"),
        None,
        None,
    )
    assert ORDINARIO.resolve_line_vat(None, profile, nazione_cliente="IT") == (
        Decimal("22.00"),
        None,
        None,
    )


def test_ordinario_refuses_a_zero_rate_because_it_has_no_natura_to_pair_with_it() -> None:
    profile = _profile(
        codice_regime="RF01",
        aliquota_iva_default=Decimal("22.00"),
        natura_default=None,
        riferimento_normativo=None,
    )
    with pytest.raises(ValidationFailed) as caught:
        ORDINARIO.resolve_line_vat(Decimal("0.00"), profile, nazione_cliente="IT")
    assert caught.value.details["field"] == "aliquota_iva"


def test_ordinario_never_charges_the_stamp_duty() -> None:
    profile = _profile(codice_regime="RF01", natura_default=None)
    assert ORDINARIO.bollo([_group("22.00", "9999.00", None)], profile) == Decimal("0.00")
