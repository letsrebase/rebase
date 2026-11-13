"""The two families of contract documents a freelancer signs (REB-687).

`vat_registered` is the ordinary pair, the framework agreement and the letter of engagement
of a member who works and invoices with a partita IVA; `occasional` is the pair of a
member who works without one, paid as a prestazione occasionale. The documents are the CRM's
(REB-1108); the hub reads them through its door and names the type a member sees from the
document's kind and its regime. Pure.
"""

from typing import Final, Literal

Regime = Literal["vat_registered", "occasional"]
VAT_REGISTERED: Final[Literal["vat_registered"]] = "vat_registered"
OCCASIONAL: Final[Literal["occasional"]] = "occasional"
REGIMES: Final[tuple[Regime, ...]] = (VAT_REGISTERED, OCCASIONAL)

# The type of each document, as «Contratti» names it.
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


def document_label(kind: str, regime: str) -> str:
    """The document's type as a page names it: «Lettera di incarico occasionale»."""
    try:
        return DOCUMENT_LABELS[(kind, regime)]
    except KeyError:
        raise ValueError(f"no contract type for kind {kind!r} and regime {regime!r}") from None
