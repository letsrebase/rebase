"""The two families of contract documents a freelancer signs (REB-687).

`partita_iva` is the ordinary pair, the framework agreement and the letter of engagement
of a member who works and invoices with a partita IVA; `occasionale` is the pair of a
member who works without one, paid as a prestazione occasionale. Every contract document
carries its regime (`contract_documents.regime`), and its kind and its regime together
name the text it is typeset from and the type the pages show. A match's regime is its
letter's. Pure: every module of the contracts flow imports it, the models included.
"""

from typing import Final, Literal

Regime = Literal["partita_iva", "occasionale"]
PARTITA_IVA: Final[Literal["partita_iva"]] = "partita_iva"
OCCASIONALE: Final[Literal["occasionale"]] = "occasionale"
REGIMES: Final[tuple[Regime, ...]] = (PARTITA_IVA, OCCASIONALE)

# The text each (kind, regime) is typeset from: the stem of its Markdown in
# `rebase_core/contracts/texts/`.
TEXT_NAMES: Final[dict[tuple[str, str], str]] = {
    ("quadro", PARTITA_IVA): "contratto-quadro",
    ("quadro", OCCASIONALE): "contratto-quadro-occasionale",
    ("lettera", PARTITA_IVA): "lettera-di-incarico",
    ("lettera", OCCASIONALE): "lettera-di-incarico-occasionale",
}
# The type of each document, as «Match e contratti», «Contratti» and the MCP tools name it.
DOCUMENT_LABELS: Final[dict[tuple[str, str], str]] = {
    ("quadro", PARTITA_IVA): "Contratto quadro",
    ("quadro", OCCASIONALE): "Contratto quadro per prestazioni occasionali",
    ("lettera", PARTITA_IVA): "Lettera di incarico",
    ("lettera", OCCASIONALE): "Lettera di incarico occasionale",
}


def as_regime(value: str) -> Regime:
    """A stored regime as the type the read models carry. Anything else is a bug: the
    column's own check allows the two values only."""
    if value == PARTITA_IVA:
        return PARTITA_IVA
    if value == OCCASIONALE:
        return OCCASIONALE
    raise ValueError(f"unknown contract regime: {value!r}")


def text_name(kind: str, regime: str) -> str:
    """The text a document of `kind` (`quadro`, `lettera`) and `regime` is typeset from."""
    try:
        return TEXT_NAMES[(kind, regime)]
    except KeyError:
        raise ValueError(f"no contract text for kind {kind!r} and regime {regime!r}") from None


def document_label(kind: str, regime: str) -> str:
    """The document's type as a page names it: «Lettera di incarico occasionale»."""
    try:
        return DOCUMENT_LABELS[(kind, regime)]
    except KeyError:
        raise ValueError(f"no contract type for kind {kind!r} and regime {regime!r}") from None
