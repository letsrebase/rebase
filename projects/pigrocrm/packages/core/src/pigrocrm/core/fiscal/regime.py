"""A regime decides three things and nothing else: the rate a line defaults to, the
`Natura`/`RiferimentoNormativo` pair, and whether the stamp duty applies (spec 7.2).

The previous system hardcoded all three in the generator, which is why "what regime was this
invoice in" had no answer other than reading the source at the time. Here they come
from `fiscal_profile` through one of these objects, so a different regime is a second
object -- no new column, no migration -- and the rounding rules of `totals.py`, already
written, simply start producing non-zero values.

Deliberately out of scope and named rather than half-designed: withholding tax,
social-security fund, split payment, reverse charge, deferred or cash-basis VAT
liability. Each is a distinct XML block the forfettario never exercises.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol, runtime_checkable

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.pack import PACK_IT_FLAT_RATE, PACK_IT_STANDARD, PACK_NON_IT
from pigrocrm.core.fiscal.schemas import (
    CODICE_REGIME_RE,
    DEFAULT_RIFERIMENTO_NORMATIVO,
    NATURA_NON_RESIDENTE,
    PAESI_UE,
    RIFERIMENTO_NORMATIVO_EXTRA_UE,
    RIFERIMENTO_NORMATIVO_UE,
    FiscalSnapshot,
)
from pigrocrm.core.invoices.totals import RiepilogoGroup, round_money

ENTITY = "fiscal_profile"
ZERO = Decimal("0.00")


@runtime_checkable
class RegimeStrategy(Protocol):
    codice: str
    # The pack this strategy belongs to: a regime and a pack are chosen together, and a
    # profile that pairs one with the other's is refused (`resolve_regime`).
    pack_id: str

    def resolve_line_vat(
        self, requested: Decimal | None, profile: FiscalSnapshot, *, nazione_cliente: str
    ) -> tuple[Decimal, str | None, str | None]:
        """`(aliquota_iva, natura, riferimento_normativo)` for one line.

        The three are returned together because the SdI validates them together: a
        zero rate without a `Natura` is rejected, and a `Natura` alongside a non-zero
        rate is rejected too. Returning them separately would let a caller pair them
        wrongly, and the table constraint `(aliquota_iva = 0) = (natura IS NOT NULL)`
        would then be the first thing to notice.

        `nazione_cliente` is the customer's ISO 3166-1 country, because the `Natura`
        of an untaxed operation depends on where the customer is established and not
        only on the issuer's regime (ORB-32). Keyword-only and without a default, so
        a caller cannot forget it and silently get the domestic answer.
        """
        ...

    def bollo(self, riepilogo: Sequence[RiepilogoGroup], profile: FiscalSnapshot) -> Decimal:
        """The virtual stamp duty for the whole invoice, or `0.00`.

        Reads the summary groups rather than a single total because the duty is owed on
        the portion **not subject to VAT**: a large taxed amount must not push a
        mostly-taxed invoice over the threshold.
        """
        ...


class _Forfettario:
    """`RF19`. No VAT, and on every line and every summary group a `Natura` with its
    normative declaration: `N2.2` and the profile's L. 190/2014 text for an Italian
    customer; `N2.1` for a customer established abroad, whose service is outside the
    territorial scope of Italian VAT under art. 7-ter DPR 633/1972 (ORB-32), with the
    annotation art. 21 c. 6-bis prescribes for where they are: "inversione contabile"
    inside the EU, "operazione non soggetta" outside it.

    The country is the only thing read off the customer. A private consumer abroad
    would be a different case again (7-ter is a business-to-business rule), and the
    customer model does not tell a company from a person, so that case is named here
    rather than guessed at: today every foreign customer of this product is a company.
    """

    codice = "RF19"
    pack_id = PACK_IT_FLAT_RATE

    def resolve_line_vat(
        self, requested: Decimal | None, profile: FiscalSnapshot, *, nazione_cliente: str
    ) -> tuple[Decimal, str | None, str | None]:
        if requested is not None and requested != ZERO:
            raise ValidationFailed(
                "invoice_line",
                "aliquota_iva",
                f"il regime {self.codice} non applica IVA, quindi l'aliquota deve essere zero",
                expected="0.00",
            )
        # Normalised the way `_cessionario` reads it in `fatturapa.py`: `customers.nazione`
        # is stored as typed, a lowercase `it` is still Italy, and an empty value is
        # Italy too -- the column's own default -- rather than a foreign customer.
        paese = (nazione_cliente or "").strip().upper() or "IT"
        if paese != "IT":
            riferimento = (
                RIFERIMENTO_NORMATIVO_UE if paese in PAESI_UE else RIFERIMENTO_NORMATIVO_EXTRA_UE
            )
            return ZERO, NATURA_NON_RESIDENTE, riferimento
        natura = profile.natura_default or "N2.2"
        riferimento = profile.riferimento_normativo or DEFAULT_RIFERIMENTO_NORMATIVO
        return ZERO, natura, riferimento

    def bollo(self, riepilogo: Sequence[RiepilogoGroup], profile: FiscalSnapshot) -> Decimal:
        if not profile.applica_bollo:
            return ZERO
        untaxed = sum(
            (group.imponibile for group in riepilogo if group.aliquota_iva == ZERO),
            start=ZERO,
        )
        if round_money(untaxed) > profile.soglia_bollo:
            return round_money(profile.importo_bollo)
        return ZERO


class _Ordinario:
    """`RF01`, the ordinary regime, on the `it-standard` pack (REB-760). Every operation
    is taxed at the rate entered, or the profile's default, and the invoice carries no
    `Natura`, no `RiferimentoNormativo` and no stamp duty: there is no untaxed base for
    the duty to be owed on. Its arithmetic is spec 6.1's, the rounding of `totals.py`
    producing a VAT figure for every group (a 22% and a 10% line on one invoice).
    """

    codice = "RF01"
    pack_id = PACK_IT_STANDARD

    def resolve_line_vat(
        self, requested: Decimal | None, profile: FiscalSnapshot, *, nazione_cliente: str
    ) -> tuple[Decimal, str | None, str | None]:
        # The country is accepted and ignored: an ordinary regime taxes the operation
        # whoever the customer is, and a foreign business customer would need the
        # reverse-charge or non-taxable blocks this strategy deliberately does not have.
        aliquota = requested if requested is not None else profile.aliquota_iva_default
        if aliquota == ZERO:
            # The table constraint requires a `natura` whenever the rate is zero, and
            # an ordinary regime has none to offer: refusing here names the field,
            # instead of letting the insert fail on a CHECK whose message names a
            # constraint.
            raise ValidationFailed(
                "invoice_line",
                "aliquota_iva",
                f"il regime {self.codice} non ha una natura da abbinare a un'aliquota zero",
                expected="un'aliquota maggiore di zero",
            )
        return aliquota, None, None

    def bollo(self, riepilogo: Sequence[RiepilogoGroup], profile: FiscalSnapshot) -> Decimal:
        # An ordinary regime taxes its operations, so there is no untaxed base for the
        # duty to be owed on. Returning zero unconditionally is the honest answer, not
        # a placeholder: an `RF01` invoice with an exempt line would need that line's
        # own `Natura`, which this strategy refuses to invent (see resolve_line_vat).
        return ZERO


class _Estero:
    """The `non-it` pack's regime (REB-619, spec 2026-10-03 §1.3): a company
    established outside Italy, which issues no FatturaPA and owes no Italian duty.

    The rate is the one entered, or the profile's default: the strategy knows nothing
    of the local VAT law and does not pretend to. No bollo, ever. No `Natura`
    default of its own, because a `Natura` is a FatturaPA code: a zero rate is
    accepted only when the profile names one, since `invoice_lines` requires a
    `natura` beside a zero rate whoever the issuer is; a foreign invoice that needs a
    legal mention beside its untaxed line puts it in the profile's default.
    """

    codice = PACK_NON_IT
    pack_id = PACK_NON_IT

    def resolve_line_vat(
        self, requested: Decimal | None, profile: FiscalSnapshot, *, nazione_cliente: str
    ) -> tuple[Decimal, str | None, str | None]:
        aliquota = requested if requested is not None else profile.aliquota_iva_default
        if aliquota == ZERO:
            if not profile.natura_default:
                raise ValidationFailed(
                    "invoice_line",
                    "aliquota_iva",
                    "un'azienda estera non ha una natura da abbinare a un'aliquota zero: "
                    "imposta una natura di default nel profilo fiscale oppure un'aliquota "
                    "maggiore di zero",
                    expected="un'aliquota maggiore di zero",
                )
            return ZERO, profile.natura_default, profile.riferimento_normativo
        return aliquota, None, None

    def bollo(self, riepilogo: Sequence[RiepilogoGroup], profile: FiscalSnapshot) -> Decimal:
        return ZERO


FORFETTARIO: RegimeStrategy = _Forfettario()
ORDINARIO: RegimeStrategy = _Ordinario()
ESTERO: RegimeStrategy = _Estero()

_BY_CODE: dict[str, RegimeStrategy] = {
    FORFETTARIO.codice: FORFETTARIO,
    ORDINARIO.codice: ORDINARIO,
}


def resolve_regime(codice_regime: str | None, pack_id: str) -> RegimeStrategy:
    """The strategy for a stored `codice_regime` and pack, or `ValidationFailed`
    naming the field.

    The pack decides first (REB-619): `non-it` is `ESTERO` and carries no code, since
    RF01..RF19 are FatturaPA values; a code beside it is refused rather than ignored,
    because a profile that says both «estera» and «RF19» is a configuration nobody
    meant. On an Italian pack the code is required, then three checks, not one: the
    value must be a syntactically valid `RF01`-`RF19` code (`.fullmatch`, so a
    trailing newline is refused rather than accepted and passed on), a strategy
    must exist for it (a code such as `RF07` is real FPR12 and still has no
    implementation here, and saying so is more useful than resolving it to the
    forfettario and silently issuing an invoice under the wrong regime), and that
    strategy must belong to the pack (REB-760): `RF01` is the ordinary regime of
    `it-standard` and `RF19` the forfettario of `it-flat-rate`, so a profile that names
    one with the other's pack is refused instead of being held to the other regime's
    ceilings.
    """
    if pack_id == PACK_NON_IT:
        if codice_regime is not None:
            raise ValidationFailed(
                ENTITY,
                "codice_regime",
                "un'azienda estera non ha un codice regime FatturaPA",
                expected="nessun codice con il pacchetto non-it",
            )
        return ESTERO
    if codice_regime is None:
        raise ValidationFailed(
            ENTITY,
            "codice_regime",
            "il regime italiano richiede un codice",
            expected="un codice da RF01 a RF19",
        )
    if not CODICE_REGIME_RE.fullmatch(codice_regime):
        raise ValidationFailed(
            ENTITY,
            "codice_regime",
            f"codice regime non valido: {codice_regime!r}",
            expected="un codice da RF01 a RF19",
        )
    strategy = _BY_CODE.get(codice_regime)
    if strategy is None:
        raise ValidationFailed(
            ENTITY,
            "codice_regime",
            f"il regime {codice_regime} non e' implementato in questa versione",
            expected=f"uno tra: {', '.join(sorted(_BY_CODE))}",
        )
    if strategy.pack_id != pack_id:
        belonging = sorted(code for code, known in _BY_CODE.items() if known.pack_id == pack_id)
        raise ValidationFailed(
            ENTITY,
            "codice_regime",
            f"il regime {codice_regime} appartiene al pacchetto {strategy.pack_id}, "
            f"non a {pack_id}",
            expected=f"uno tra: {', '.join(belonging)}" if belonging else "un pacchetto italiano",
        )
    return strategy


def frozen_pack_id(codice_regime: str | None, pack_id: str) -> str:
    """The pack an invoice's frozen snapshot belongs to, read the way it is today.

    An invoice issued before REB-760 under the ordinary regime froze `RF01` beside
    `it-flat-rate`, the only Italian pack there was. The snapshot is never rewritten, so
    the register names that pair as what it is now, `it-standard`, and a year with
    invoices of both kinds has one `RF01` row and not two.
    """
    strategy = _BY_CODE.get(codice_regime) if codice_regime is not None else None
    if strategy is not None and pack_id == PACK_IT_FLAT_RATE:
        return strategy.pack_id
    return pack_id


__all__ = [
    "ESTERO",
    "FORFETTARIO",
    "ORDINARIO",
    "RegimeStrategy",
    "frozen_pack_id",
    "resolve_regime",
]
