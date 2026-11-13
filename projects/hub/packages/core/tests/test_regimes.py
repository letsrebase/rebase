"""REB-687: a contract document's kind and regime name the type the member's page shows."""

import pytest

from rebase_core.regimes import (
    OCCASIONAL,
    REGIMES,
    VAT_REGISTERED,
    as_regime,
    document_label,
)


@pytest.mark.parametrize(
    ("kind", "regime", "label"),
    [
        ("framework", "vat_registered", "Contratto quadro"),
        ("framework", "occasional", "Contratto quadro per prestazioni occasionali"),
        ("letter", "vat_registered", "Lettera di incarico"),
        ("letter", "occasional", "Lettera di incarico occasionale"),
    ],
)
def test_kind_and_regime_name_one_type(kind: str, regime: str, label: str) -> None:
    assert document_label(kind, regime) == label


def test_the_two_regimes_and_nothing_else() -> None:
    assert REGIMES == (VAT_REGISTERED, OCCASIONAL) == ("vat_registered", "occasional")
    assert as_regime("occasional") == OCCASIONAL
    for wrong in ("forfettario", "", "Occasionale"):
        with pytest.raises(ValueError):
            as_regime(wrong)
    with pytest.raises(ValueError):
        document_label("fattura", VAT_REGISTERED)
