"""The two families of contract documents a freelancer signs (REB-687).

`vat_registered` is the ordinary pair, the framework agreement and the letter of engagement
of a member who works and invoices with a partita IVA; `occasional` is the pair of a
member who works without one, paid as a prestazione occasionale. Every contract document
carries its regime (`contract_documents.regime`), and its kind and its regime together
name the text it is typeset from and the type the pages show. A match's regime is its
letter's. Pure: every module of the contracts flow imports it, the models included.
"""

from typing import Final, Literal

Regime = Literal["vat_registered", "occasional"]
VAT_REGISTERED: Final[Literal["vat_registered"]] = "vat_registered"
OCCASIONAL: Final[Literal["occasional"]] = "occasional"
REGIMES: Final[tuple[Regime, ...]] = (VAT_REGISTERED, OCCASIONAL)

# The text each (kind, regime) is typeset from: the stem of its Markdown in
# `rebase_core/contracts/texts/`.
TEXT_NAMES: Final[dict[tuple[str, str], str]] = {
    ("framework", VAT_REGISTERED): "framework-agreement",
    ("framework", OCCASIONAL): "framework-agreement-occasional",
    ("letter", VAT_REGISTERED): "engagement-letter",
    ("letter", OCCASIONAL): "engagement-letter-occasional",
}
# The type of each document, as «Match e contratti», «Contratti» and the MCP tools name it.
DOCUMENT_LABELS: Final[dict[tuple[str, str], str]] = {
    ("framework", VAT_REGISTERED): "Contratto quadro",
    ("framework", OCCASIONAL): "Contratto quadro per prestazioni occasionali",
    ("letter", VAT_REGISTERED): "Lettera di incarico",
    ("letter", OCCASIONAL): "Lettera di incarico occasionale",
}


def as_regime(value: str) -> Regime:
    """A stored regime as the type the read models carry. Anything else is a bug: the
    column's own check allows the two values only."""
    if value == VAT_REGISTERED:
        return VAT_REGISTERED
    if value == OCCASIONAL:
        return OCCASIONAL
    raise ValueError(f"unknown contract regime: {value!r}")


def text_name(kind: str, regime: str) -> str:
    """The text a document of `kind` (`framework`, `letter`) and `regime` is typeset from."""
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


def document_stem(kind: str, regime: str, number: str | None, version: str) -> str:
    """A document's file name without `.pdf`: its own text's name, then the letter's
    number or the framework agreement's text version, so the two families never share a
    name (`framework-agreement-occasional-v1.0`, `engagement-letter-occasional-2026-001`)."""
    text = text_name(kind, regime)
    return f"{text}-{number}" if kind == "letter" else f"{text}-v{version}"
