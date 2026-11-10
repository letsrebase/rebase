"""A regime decides three things and nothing else: the rate a line defaults to, the
`Natura`/`RiferimentoNormativo` pair, and whether the stamp duty applies (spec 7.2).

The previous system hardcoded all three in the generator, which is why "what regime was this
invoice in" had no answer other than reading the source at the time. Here they come
from `fiscal_profile` through one of these objects, so a different regime is a second
object -- no new column, no migration -- and the rounding rules of `totals.py`, already
written, simply start producing non-zero values.

Withholding and split payment are not a regime's decision: they depend on
the customer as much as on the issuer and are computed from the stored lines by
`invoices/charges.py`, on the data of the pack (`FiscalPack.sales_withholding`). Still out of
scope and named rather than half-designed: reverse charge, deferred or cash-basis VAT
liability.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol, runtime_checkable

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.pack import (
    PACK_IT_COMPANY,
    PACK_IT_FLAT_RATE,
    PACK_IT_STANDARD,
    PACK_NON_IT,
)
from pigrocrm.core.fiscal.schemas import (
    COUNTRIES_UE,
    DEFAULT_LEGAL_REFERENCE,
    LEGAL_REFERENCE_EXTRA_UE,
    LEGAL_REFERENCE_EXTRA_UE_ORDINARY,
    LEGAL_REFERENCE_UE,
    LEGAL_REFERENCE_UE_ORDINARY,
    REGIME_CODE_RE,
    VAT_NATURE_NOT_RESIDENT,
    FiscalSnapshot,
)
from pigrocrm.core.invoices.totals import RecapGroup
from pigrocrm.core.money import round_money

ENTITY = "fiscal_profile"
ZERO = Decimal("0.00")


@runtime_checkable
class RegimeStrategy(Protocol):
    code: str
    # The pack this strategy belongs to: a regime and a pack are chosen together, and a
    # profile that pairs one with the other's is refused (`resolve_regime`). `pack_id` is the
    # pack the regime was written for, the one a legacy snapshot is read as (`frozen_pack_id`);
    # `pack_ids` is every pack that may carry it: `RF01` belongs to `it-standard` and to
    # `it-company`, which invoice alike and differ only in what they owe (REB-1005).
    pack_id: str
    pack_ids: frozenset[str]

    def resolve_line_vat(
        self,
        requested: Decimal | None,
        profile: FiscalSnapshot,
        *,
        country_customer: str,
        customer_kind: str | None = None,
    ) -> tuple[Decimal, str | None, str | None]:
        """`(aliquota_iva, natura, riferimento_normativo)` for one line.

        The three are returned together because the SdI validates them together: a
        zero rate without a `Natura` is rejected, and a `Natura` alongside a non-zero
        rate is rejected too. Returning them separately would let a caller pair them
        wrongly, and the table constraint `(aliquota_iva = 0) = (natura IS NOT NULL)`
        would then be the first thing to notice.

        `country_customer` is the customer's ISO 3166-1 country, because the `Natura`
        of an untaxed operation depends on where the customer is established and not
        only on the issuer's regime (ORB-32). Keyword-only and without a default, so
        a caller cannot forget it and silently get the domestic answer.

        `customer_kind` is the customer's kind (`organizations.customer_kind`, `None` when it
        was not said): the ordinary regime tells a business abroad, whose service is outside
        Italian VAT, from a consumer abroad, which has its own card and is refused for now.
        The flat-rate regime has always read every foreign customer as a company.
        """
        ...

    def stamp_duty(self, recap: Sequence[RecapGroup], profile: FiscalSnapshot) -> Decimal:
        """The virtual stamp duty for the whole invoice, or `0.00`.

        Reads the summary groups rather than a single total because the duty is owed on
        the portion **not subject to VAT**: a large taxed amount must not push a
        mostly-taxed invoice over the threshold.
        """
        ...


class _flat_rate:
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

    code = "RF19"
    pack_id = PACK_IT_FLAT_RATE
    pack_ids = frozenset({PACK_IT_FLAT_RATE})

    def resolve_line_vat(
        self,
        requested: Decimal | None,
        profile: FiscalSnapshot,
        *,
        country_customer: str,
        customer_kind: str | None = None,
    ) -> tuple[Decimal, str | None, str | None]:
        if requested is not None and requested != ZERO:
            raise ValidationFailed(
                "invoice_line",
                "vat_rate",
                f"il regime {self.code} non applica IVA, quindi l'aliquota deve essere zero",
                expected="0.00",
            )
        # Normalised the way `_buyer` reads it in `fatturapa/exporter.py`: `organizations.country`
        # is stored as typed, a lowercase `it` is still Italy, and an empty value is
        # Italy too -- the column's own default -- rather than a foreign customer.
        country = (country_customer or "").strip().upper() or "IT"
        if country != "IT":
            reference = LEGAL_REFERENCE_UE if country in COUNTRIES_UE else LEGAL_REFERENCE_EXTRA_UE
            return ZERO, VAT_NATURE_NOT_RESIDENT, reference
        vat_nature = profile.default_vat_nature or "N2.2"
        reference = profile.legal_reference or DEFAULT_LEGAL_REFERENCE
        return ZERO, vat_nature, reference

    def stamp_duty(self, recap: Sequence[RecapGroup], profile: FiscalSnapshot) -> Decimal:
        if not profile.apply_stamp_duty:
            return ZERO
        untaxed = sum(
            (group.taxable_amount for group in recap if group.vat_rate == ZERO),
            start=ZERO,
        )
        if round_money(untaxed) > profile.stamp_duty_threshold:
            return round_money(profile.stamp_duty_amount)
        return ZERO


class _ordinary:
    """`RF01`, the ordinary regime, on the `it-standard` pack (REB-760) and on `it-company`
    (REB-1005), which invoice the same way. Every operation to a customer in Italy is taxed
    at the rate entered, or the profile's default, and the invoice carries no `Natura`, no
    `RiferimentoNormativo` and no stamp duty: there is
    no untaxed base for the duty to be owed on. Its arithmetic is spec 6.1's, the rounding
    of `totals.py` producing a VAT figure for every group (a 22% and a 10% line on one
    invoice).

    A service to a business established abroad is outside Italian VAT (REB-994): art. 7-ter
    c. 1 DPR 633/72 makes a service territorial in Italy when it is rendered to a taxable
    person established there, so one rendered to a taxable person established elsewhere is
    not, and the invoice says so under art. 21 c. 6-bis (A5, verified): `N2.1` with
    «inversione contabile» inside the EU and «operazione non soggetta» outside it. A consumer
    abroad is another case (the same article's c. 1 lett. b) can tax it in Italy), told apart
    by `customer_kind`, and has its own card: it is refused here.
    """

    code = "RF01"
    pack_id = PACK_IT_STANDARD
    pack_ids = frozenset({PACK_IT_STANDARD, PACK_IT_COMPANY})

    def resolve_line_vat(
        self,
        requested: Decimal | None,
        profile: FiscalSnapshot,
        *,
        country_customer: str,
        customer_kind: str | None = None,
    ) -> tuple[Decimal, str | None, str | None]:
        country = (country_customer or "").strip().upper() or "IT"
        if country != "IT":
            if customer_kind == "consumer":
                raise ValidationFailed(
                    "customer",
                    "customer_kind",
                    "una fattura a un consumatore estero non e' ancora supportata: "
                    "la tassazione dipende dal tipo di servizio",
                    expected="un cliente estero che sia un'impresa",
                )
            if requested is not None and requested != ZERO:
                raise ValidationFailed(
                    "invoice_line",
                    "vat_rate",
                    "un servizio a un'impresa estera non e' soggetto a IVA italiana "
                    "(art. 7-ter DPR 633/72): l'aliquota deve essere zero",
                    expected="0.00",
                )
            reference = (
                LEGAL_REFERENCE_UE_ORDINARY
                if country in COUNTRIES_UE
                else LEGAL_REFERENCE_EXTRA_UE_ORDINARY
            )
            return ZERO, VAT_NATURE_NOT_RESIDENT, reference
        rate = requested if requested is not None else profile.default_vat_rate
        if rate == ZERO:
            # The table constraint requires a `vat_nature` whenever the rate is zero, and
            # an ordinary regime has none to offer: refusing here names the field,
            # instead of letting the insert fail on a CHECK whose message names a
            # constraint.
            raise ValidationFailed(
                "invoice_line",
                "vat_rate",
                f"il regime {self.code} non ha una natura da abbinare a un'aliquota zero",
                expected="un'aliquota maggiore di zero",
            )
        return rate, None, None

    def stamp_duty(self, recap: Sequence[RecapGroup], profile: FiscalSnapshot) -> Decimal:
        # An ordinary regime taxes its operations to a customer in Italy, so there is no
        # untaxed base for the duty to be owed on. The one untaxed line it writes, a service
        # to a business abroad (`N2.1`), is not charged duty either: whether a document to a
        # customer abroad owes it is not in the register (A5), so it is left to the
        # accountant (REB-975) and not computed.
        return ZERO


class _foreign:
    """The `non-it` pack's regime (REB-619, spec 2026-10-03 §1.3): a company
    established outside Italy, which issues no FatturaPA and owes no Italian duty.

    The rate is the one entered, or the profile's default: the strategy knows nothing
    of the local VAT law and does not pretend to. No stamp duty, ever. No `Natura`
    default of its own, because a `Natura` is a FatturaPA code: a zero rate is
    accepted only when the profile names one, since `invoice_lines` requires a
    `vat_nature` beside a zero rate whoever the issuer is; a foreign invoice that needs a
    legal mention beside its untaxed line puts it in the profile's default.
    """

    code = PACK_NON_IT
    pack_id = PACK_NON_IT
    pack_ids = frozenset({PACK_NON_IT})

    def resolve_line_vat(
        self,
        requested: Decimal | None,
        profile: FiscalSnapshot,
        *,
        country_customer: str,
        customer_kind: str | None = None,
    ) -> tuple[Decimal, str | None, str | None]:
        rate = requested if requested is not None else profile.default_vat_rate
        if rate == ZERO:
            if not profile.default_vat_nature:
                raise ValidationFailed(
                    "invoice_line",
                    "vat_rate",
                    "un'azienda estera non ha una natura da abbinare a un'aliquota zero: "
                    "imposta una natura di default nel profilo fiscale oppure un'aliquota "
                    "maggiore di zero",
                    expected="un'aliquota maggiore di zero",
                )
            return ZERO, profile.default_vat_nature, profile.legal_reference
        return rate, None, None

    def stamp_duty(self, recap: Sequence[RecapGroup], profile: FiscalSnapshot) -> Decimal:
        return ZERO


FLAT_RATE: RegimeStrategy = _flat_rate()
ORDINARY: RegimeStrategy = _ordinary()
FOREIGN: RegimeStrategy = _foreign()

_BY_CODE: dict[str, RegimeStrategy] = {
    FLAT_RATE.code: FLAT_RATE,
    ORDINARY.code: ORDINARY,
}


def resolve_regime(regime_code: str | None, pack_id: str) -> RegimeStrategy:
    """The strategy for a stored `regime_code` and pack, or `ValidationFailed`
    naming the field.

    The pack decides first (REB-619): `non-it` is `FOREIGN` and carries no code, since
    RF01..RF19 are FatturaPA values; a code beside it is refused rather than ignored,
    because a profile that says both «estera» and «RF19» is a configuration nobody
    meant. On an Italian pack the code is required, then three checks, not one: the
    value must be a syntactically valid `RF01`-`RF19` code (`.fullmatch`, so a
    trailing newline is refused rather than accepted and passed on), a strategy
    must exist for it (a code such as `RF07` is real FPR12 and still has no
    implementation here, and saying so is more useful than resolving it to the
    flat-rate regime and silently issuing an invoice under the wrong regime), and that
    strategy must belong to the pack (REB-760): `RF01` is the ordinary regime of
    `it-standard` and `it-company`, and `RF19` the flat-rate regime of `it-flat-rate`, so a
    profile that names one with the other's pack is refused instead of being held to the other
    regime's ceilings.
    """
    if pack_id == PACK_NON_IT:
        if regime_code is not None:
            raise ValidationFailed(
                ENTITY,
                "regime_code",
                "un'azienda estera non ha un codice regime FatturaPA",
                expected="nessun codice con il pacchetto non-it",
            )
        return FOREIGN
    if regime_code is None:
        raise ValidationFailed(
            ENTITY,
            "regime_code",
            "il regime italiano richiede un codice",
            expected="un codice da RF01 a RF19",
        )
    if not REGIME_CODE_RE.fullmatch(regime_code):
        raise ValidationFailed(
            ENTITY,
            "regime_code",
            f"codice regime non valido: {regime_code!r}",
            expected="un codice da RF01 a RF19",
        )
    strategy = _BY_CODE.get(regime_code)
    if strategy is None:
        raise ValidationFailed(
            ENTITY,
            "regime_code",
            f"il regime {regime_code} non e' implementato in questa versione",
            expected=f"uno tra: {', '.join(sorted(_BY_CODE))}",
        )
    if pack_id not in strategy.pack_ids:
        belonging = sorted(code for code, known in _BY_CODE.items() if pack_id in known.pack_ids)
        raise ValidationFailed(
            ENTITY,
            "regime_code",
            f"il regime {regime_code} appartiene al pacchetto "
            f"{' o '.join(sorted(strategy.pack_ids))}, non a {pack_id}",
            expected=f"uno tra: {', '.join(belonging)}" if belonging else "un pacchetto italiano",
        )
    return strategy


def frozen_pack_id(regime_code: str | None, pack_id: str) -> str:
    """The pack an invoice's frozen snapshot belongs to, read the way it is today.

    An invoice issued before REB-760 under the ordinary regime froze `RF01` beside
    `it-flat-rate`, the only Italian pack there was. The snapshot is never rewritten, so
    the register names that pair as what it is now, `it-standard`, and a year with
    invoices of both kinds has one `RF01` row and not two.
    """
    strategy = _BY_CODE.get(regime_code) if regime_code is not None else None
    if strategy is not None and pack_id == PACK_IT_FLAT_RATE:
        return strategy.pack_id
    return pack_id


__all__ = [
    "FOREIGN",
    "FLAT_RATE",
    "ORDINARY",
    "RegimeStrategy",
    "frozen_pack_id",
    "resolve_regime",
]
