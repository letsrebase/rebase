"""The jurisdiction pack -- pure data, no database row (REB-344 §8, REB-361).

`FiscalProfile` stays exactly what it already is: a singleton, non-historicised row
of *emission-time defaults* for the one issuer (`fiscal/models.py`'s own docstring).
A ceiling and a statutory charge are not emission defaults -- they are a fact about
the jurisdiction's regime, ported from mastro's own `FiscalPack`/`Ceiling`/
`StatutoryCharge` interfaces (`fiscal/pack.ts`, `packs/it-flat-rate.ts` in
`~/projects/personal/mastro`, commit `53ef2942`) and trimmed to exactly what a
ceiling engine and a rivalsa line need: no `TaxTreatment`/`resolveTaxTreatment`
machinery, since `FiscalProfile.natura_default`/`aliquota_iva_default` already solve
that problem for the one regime this product supports.

Three shipped packs. `IT_FLAT_RATE_PACK` is the forfettario, the currently-in-force version
(`it-flat-rate.ts`'s `effectiveFrom: '2023-01-01'`, not the pre-2023
`itFlatRatePackV0`, since PigroCRM has no history to reconcile against an older
regime). `IT_STANDARD_PACK` is Italy's ordinary regime (mastro's `it-standard.ts`):
no revenue ceiling and no statutory charge, because the forfettario's thresholds and
its rivalsa are facts of that regime alone. `NON_IT_PACK` is mastro's `generic` pack
under the name this product gave it (REB-619): a company outside Italy, no ceiling,
no charge, no national invoice format; there is no second no-ceiling pack beside it,
since one that declared nothing and did nothing else would only be a duplicate.
`FiscalProfile.pack_id`/`pack_version` is the pointer to *which* pack
governs -- a plain string pair, no historicisation, matching `FiscalProfile`'s own
un-historicised shape -- read once by the ceiling service at evaluation time. A
further pack is a new module and a new registry entry here, never a branch on
`FiscalProfile.codice_regime` inside the ceiling or rivalsa logic (mastro's own
invariant 1: no country-specific logic outside a jurisdiction pack).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

# The pack ids, as literals so a schema can name them in a `Literal` type
# (`fiscal/schemas.py::PackId`) and a profile cannot point at a pack that does not exist.
PACK_IT_FLAT_RATE: Final = "it-flat-rate"
PACK_IT_STANDARD: Final = "it-standard"
PACK_NON_IT: Final = "non-it"

# The only basis this pack's ceilings use today -- mastro's `cash_received_calendar_year`
# (`pack.ts`'s `CeilingBasis`). Money in the bank, within the calendar year, matching
# how the law itself measures the forfettario's own ceilings ("ricavi o compensi
# percepiti").
CeilingBasis = Literal["cash_received_calendar_year"]

# What crossing a ceiling does to the regime -- mastro's own `consequence` text,
# trimmed to the one fact the ceiling service needs to report rather than the
# bilingual prose mastro attaches to it.
CeilingConsequence = Literal["esce_dall_anno_successivo", "esce_immediatamente"]


@dataclass(frozen=True)
class CeilingAlertLevel:
    """One threshold a ceiling's own usage ratio can cross, each with its own label
    -- "approaching" reads differently from "reached" (mastro's `CeilingAlertLevel`,
    `pack.ts:67-77`)."""

    rapporto: Decimal
    etichetta: str


@dataclass(frozen=True)
class Ceiling:
    """A revenue ceiling a pack declares -- mastro's `Ceiling` (`pack.ts:79-112`),
    trimmed to `measure: 'absolute_amount'` and `perimeter: {kind: 'all_clients'}`:
    the only shapes `IT_FLAT_RATE_PACK` needs. A `percentage_share`/`client`-scoped
    ceiling (a contract's own concentration clause) is REB-352's own, later concern
    (§1.5 of the forecasting spec) and is not modelled here."""

    id: str
    etichetta: str
    soglia: Decimal
    basis: CeilingBasis
    conseguenza: CeilingConsequence
    livelli_allerta: tuple[CeilingAlertLevel, ...]


@dataclass(frozen=True)
class StatutoryCharge:
    """A statutory charge a pack requires an invoice to carry -- mastro's
    `StatutoryCharge` (`pack.ts:195-221`), trimmed to `amount: {kind: 'percentage'}`
    (`IT_FLAT_RATE_PACK`'s rivalsa is the only charge this port ships; mastro's
    virtual stamp duty is not ported, per REB-344 §8: PigroCRM never recharges its
    bollo to the client).

    `descrizione_riga` is the exact `InvoiceLine.descrizione` text the
    invoice-line-construction step writes -- naming the surcharge and its legal
    basis, per REB-361's own "Needed" -- and the one stable marker the tagged-figure
    mechanism (`ceiling.py`) reads back to identify this charge's own lines without a
    new `InvoiceLine`/`Invoice` column: `natura`/`riferimento_normativo` cannot serve
    that role, because every zero-rate line on a forfettario invoice already carries
    the *regime's* single `natura`/`riferimento_normativo` pair
    (`fiscal/regime.py::_Forfettario.resolve_line_vat`), identical whether the line is
    a fee or a statutory charge -- `descrizione` is the only column a per-charge
    identity can live on without touching the two that the SdI already governs.

    `conta_per_il_limite`/`conta_per_il_coefficiente` mirror mastro's own per-charge
    `countsTowardsRevenuePerimeter` declaration (`pack.ts:209-220`,
    `it-flat-rate.ts:283`), extended with a second boolean PigroCRM alone needs:
    mastro never computes a coefficiente-based tax estimate, so its own charge has no
    equivalent of "counts toward the ceiling but is not taxable income" (REB-352 §2,
    §6's resolved decision) -- that fact has nowhere else to live but on the charge
    itself, the same place mastro puts the first boolean.
    """

    id: str
    etichetta: str
    descrizione_riga: str
    aliquota: Decimal
    conta_per_il_limite: bool
    conta_per_il_coefficiente: bool


@dataclass(frozen=True)
class FiscalPack:
    """A jurisdiction pack: its ceilings and its statutory charges -- mastro's
    `FiscalPack` (`pack.ts:297-350`), trimmed to the two sections this port needs.
    Never named by id inside the ceiling or rivalsa logic (mastro's own `pack.ts`
    header: "nothing in this file may name a concrete pack") -- a caller resolves one
    by `id`/`version` through `resolve_pack` and passes the object on."""

    id: str
    version: str
    ceilings: tuple[Ceiling, ...]
    charges: tuple[StatutoryCharge, ...]
    # Whether the pack's regime has the income estimate (`analytics/fiscal.py`): the
    # forfettario's coefficient, substitute tax and INPS. No other pack has one, and the
    # economic overview draws no estimate for them instead of a card of zeros (REB-760).
    stima_reddito: bool = False

    def charge(self, charge_id: str) -> StatutoryCharge:
        for charge in self.charges:
            if charge.id == charge_id:
                return charge
        raise KeyError(charge_id)


RIVALSA_INPS_CHARGE_ID = "rivalsa_inps"

# 4%, `it-flat-rate.ts:278`'s `basisPoints: 400`. The base is the fee subtotal alone,
# never widened by a recharged stamp duty (`chargeSlotOrder`'s other slot): REB-344 §9
# confirms PigroCRM never recharges bollo to the client, so the base the law and
# mastro's own pack agree on collapses to the fee alone here.
RIVALSA_INPS = StatutoryCharge(
    id=RIVALSA_INPS_CHARGE_ID,
    etichetta="Rivalsa contributiva INPS 4%",
    descrizione_riga="Rivalsa contributiva INPS 4% (art. 1, comma 212, legge 662/1996)",
    aliquota=Decimal("0.04"),
    # It counts toward the ceiling in full ("l'importo concorre al limite dei ricavi
    # e compensi del regime forfettario", the circolare `it-flat-rate.ts:262` cites)
    # while never entering the coefficiente base ("pur non costituendo reddito
    # imponibile", the same citation's second half) -- REB-352 §2 and §6's resolved
    # decision, the reason this charge carries two booleans and not one.
    conta_per_il_limite=True,
    conta_per_il_coefficiente=False,
)

# it-flat-rate.ts:127-180: two ceilings, both `cash_received_calendar_year` /
# `all_clients`. €85,000 loses the regime from the *following* fiscal year
# (comma 54, l. 190/2014); €100,000 loses it *immediately*, in the same year
# (comma 71). Alert ratios ported verbatim (0.8/1.0 and 0.9/1.0).
IT_FLAT_RATE_PACK = FiscalPack(
    id=PACK_IT_FLAT_RATE,
    version="1",
    ceilings=(
        Ceiling(
            id="soglia_ricavi",
            etichetta="Soglia di ricavi e compensi del regime forfettario",
            soglia=Decimal("85000.00"),
            basis="cash_received_calendar_year",
            conseguenza="esce_dall_anno_successivo",
            livelli_allerta=(
                CeilingAlertLevel(
                    rapporto=Decimal("0.8"),
                    etichetta="In avvicinamento alla soglia di ricavi",
                ),
                CeilingAlertLevel(
                    rapporto=Decimal("1"),
                    etichetta="Soglia di ricavi raggiunta",
                ),
            ),
        ),
        Ceiling(
            id="soglia_fuoriuscita_immediata",
            etichetta="Soglia di fuoriuscita immediata dal regime forfettario",
            soglia=Decimal("100000.00"),
            basis="cash_received_calendar_year",
            conseguenza="esce_immediatamente",
            livelli_allerta=(
                CeilingAlertLevel(
                    rapporto=Decimal("0.9"),
                    etichetta="In avvicinamento alla fuoriuscita immediata dal regime",
                ),
                CeilingAlertLevel(
                    rapporto=Decimal("1"),
                    etichetta="Soglia di fuoriuscita immediata raggiunta",
                ),
            ),
        ),
    ),
    charges=(RIVALSA_INPS,),
    stima_reddito=True,
)

# A company established outside Italy (REB-619, spec 2026-10-03 §1.3): no ceiling,
# because the forfettario's thresholds are a fact of Italian law; no statutory charge,
# because the rivalsa INPS is too; and no bollo or `Natura`, which are the regime
# strategy's (`fiscal/regime.py::ESTERO`, selected by this pack's id). Its invoices
# are never FatturaPA, so `codice_regime` is `NULL` on a profile that points here.
NON_IT_PACK = FiscalPack(id=PACK_NON_IT, version="1", ceilings=(), charges=())

# Italy's ordinary regime, RF01 (mastro's `it-standard.ts`: `ceilings: []`, `charges: []`).
# No ceiling, because the 85,000 and 100,000 euro thresholds are the forfettario's
# (l. 190/2014, commi 54 and 71) and an ordinary azienda is held to neither. No statutory
# charge either, as in mastro: the rivalsa INPS this product ships is the forfettario's
# zero-VAT line, and on an ordinary invoice the surcharge would take VAT and join the
# taxable base, a rule this product has not been given, so a contract's
# `applies_social_charge` election adds no line here (`rivalsa_line_for_contract`).
# The regime strategy (`fiscal/regime.py::ORDINARIO`) is selected by this pack's id.
IT_STANDARD_PACK = FiscalPack(id=PACK_IT_STANDARD, version="1", ceilings=(), charges=())

_PACKS: dict[tuple[str, str], FiscalPack] = {
    (IT_FLAT_RATE_PACK.id, IT_FLAT_RATE_PACK.version): IT_FLAT_RATE_PACK,
    (IT_STANDARD_PACK.id, IT_STANDARD_PACK.version): IT_STANDARD_PACK,
    (NON_IT_PACK.id, NON_IT_PACK.version): NON_IT_PACK,
}


def resolve_pack(pack_id: str, pack_version: str) -> FiscalPack:
    """The pack `FiscalProfile.pack_id`/`pack_version` points at, or `KeyError`
    naming the pair -- the one place a pack is looked up by its identity, mirroring
    mastro's own registry (`registry.ts`) trimmed to a plain dict."""

    try:
        return _PACKS[(pack_id, pack_version)]
    except KeyError:
        raise KeyError(f"unknown fiscal pack {pack_id!r} version {pack_version!r}") from None


def known_charge_markers() -> frozenset[str]:
    """The `descrizione_riga` of every statutory charge any shipped pack declares. What a
    register reads to tell a charge's line from a fee's, whatever pack the azienda is on
    today: an invoice keeps the lines it was issued with after the profile moves to
    another pack."""
    return frozenset(charge.descrizione_riga for pack in _PACKS.values() for charge in pack.charges)


__all__ = [
    "IT_FLAT_RATE_PACK",
    "IT_STANDARD_PACK",
    "NON_IT_PACK",
    "PACK_IT_FLAT_RATE",
    "PACK_IT_STANDARD",
    "PACK_NON_IT",
    "RIVALSA_INPS",
    "RIVALSA_INPS_CHARGE_ID",
    "Ceiling",
    "CeilingAlertLevel",
    "CeilingBasis",
    "CeilingConsequence",
    "FiscalPack",
    "StatutoryCharge",
    "known_charge_markers",
    "resolve_pack",
]
