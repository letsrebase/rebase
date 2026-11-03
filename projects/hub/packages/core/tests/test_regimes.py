"""REB-687: a contract document's kind and regime name its text and its type."""

import pytest

from rebase_core.regimes import (
    OCCASIONAL,
    REGIMES,
    VAT_REGISTERED,
    as_regime,
    document_label,
    document_stem,
    text_name,
)


@pytest.mark.parametrize(
    ("kind", "regime", "text", "label"),
    [
        ("framework", "partita_iva", "framework-agreement", "Contratto quadro"),
        (
            "framework",
            "occasional",
            "framework-agreement-occasional",
            "Contratto quadro per prestazioni occasionali",
        ),
        ("letter", "partita_iva", "engagement-letter", "Lettera di incarico"),
        (
            "letter",
            "occasional",
            "engagement-letter-occasional",
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
    assert REGIMES == (VAT_REGISTERED, OCCASIONAL) == ("partita_iva", "occasional")
    assert as_regime("occasional") == OCCASIONAL
    for wrong in ("forfettario", "", "Occasionale"):
        with pytest.raises(ValueError):
            as_regime(wrong)
        with pytest.raises(ValueError):
            text_name("framework", wrong)
    with pytest.raises(ValueError):
        document_label("fattura", VAT_REGISTERED)


def test_a_documents_file_name_carries_its_own_texts_name() -> None:
    """REB-687: the two families never share a file name."""
    assert document_stem("framework", "partita_iva", None, "1.0") == "contratto-quadro-v1.0"
    assert document_stem("framework", "occasional", None, "1.0") == (
        "contratto-quadro-occasionale-v1.0"
    )
    assert document_stem("letter", "partita_iva", "2026-001", "1.1") == (
        "lettera-di-incarico-2026-001"
    )
    assert document_stem("letter", "occasional", "2026-002", "1.0") == (
        "lettera-di-incarico-occasionale-2026-002"
    )
