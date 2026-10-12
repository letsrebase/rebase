"""REB-687: a contract document's kind and regime name its text and its type."""

import pytest

from rebase_core.regimes import (
    OCCASIONALE,
    PARTITA_IVA,
    REGIMES,
    as_regime,
    document_label,
    text_name,
)


@pytest.mark.parametrize(
    ("kind", "regime", "text", "label"),
    [
        ("quadro", "partita_iva", "contratto-quadro", "Contratto quadro"),
        (
            "quadro",
            "occasionale",
            "contratto-quadro-occasionale",
            "Contratto quadro per prestazioni occasionali",
        ),
        ("lettera", "partita_iva", "lettera-di-incarico", "Lettera di incarico"),
        (
            "lettera",
            "occasionale",
            "lettera-di-incarico-occasionale",
            "Lettera di incarico occasionale",
        ),
    ],
)
def test_kind_and_regime_name_one_text_and_one_type(
    kind: str, regime: str, text: str, label: str
) -> None:
    assert text_name(kind, regime) == text
    assert document_label(kind, regime) == label


def test_the_two_regimes_and_nothing_else() -> None:
    assert REGIMES == (PARTITA_IVA, OCCASIONALE) == ("partita_iva", "occasionale")
    assert as_regime("occasionale") == OCCASIONALE
    for wrong in ("forfettario", "", "Occasionale"):
        with pytest.raises(ValueError):
            as_regime(wrong)
        with pytest.raises(ValueError):
            text_name("quadro", wrong)
    with pytest.raises(ValueError):
        document_label("fattura", PARTITA_IVA)
