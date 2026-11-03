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
        ("framework", "vat_registered", "framework-agreement", "Contratto quadro"),
        (
            "framework",
            "occasional",
            "framework-agreement-occasional",
            "Contratto quadro per prestazioni occasionali",
        ),
        ("letter", "vat_registered", "engagement-letter", "Lettera di incarico"),
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
    assert REGIMES == (VAT_REGISTERED, OCCASIONAL) == ("vat_registered", "occasional")
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
    assert document_stem("framework", "vat_registered", None, "1.0") == "framework-agreement-v1.0"
    assert document_stem("framework", "occasional", None, "1.0") == (
        "framework-agreement-occasional-v1.0"
    )
    assert document_stem("letter", "vat_registered", "2026-001", "1.1") == (
        "engagement-letter-2026-001"
    )
    assert document_stem("letter", "occasional", "2026-002", "1.0") == (
        "engagement-letter-occasional-2026-002"
    )
