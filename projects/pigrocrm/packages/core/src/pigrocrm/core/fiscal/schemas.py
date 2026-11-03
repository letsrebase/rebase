"""The fiscal parameters, as a value object and as the API's Create/Read pair.

`FiscalSnapshot` is what gets frozen onto an issued invoice and what the exporter and
the PDF read. It is a plain Pydantic model with no `id` and no timestamps precisely so
that it can be serialised into `invoices.snapshot` and read back three years later
without the row it came from still existing in its original shape.
"""

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pigrocrm.core.fiscal.pack import PACK_IT_FLAT_RATE, PACK_IT_STANDARD, PACK_NON_IT
from pigrocrm.core.validation import SafeStr

# The packs a profile may point at (REB-619): `pack.py` owns them, this is the shape
# the API and MCP accept. A `Literal` and not a free string, so a typo is refused by
# the schema instead of reaching `resolve_pack`'s `KeyError` at the first emission.
PackId = Literal["it-flat-rate", "it-standard", "non-it"]
assert set(get_args(PackId)) == {PACK_IT_FLAT_RATE, PACK_IT_STANDARD, PACK_NON_IT}  # noqa: S101

# `RF01`..`RF19`, the codes FPR12's own `RegimeFiscaleType` enumerates. `.fullmatch`
# is what the callers use, never `.match` with `$`: "RF19\n" is five characters and
# would reach the String(4) column as a raw, session-poisoning DataError.
REGIME_CODE_RE = re.compile(r"RF(0[1-9]|1[0-9])")

REGIME_CODE_MAX_LENGTH = 4
VAT_NATURE_MAX_LENGTH = 4
PAYMENT_TERMS_MAX_LENGTH = 4
PAYMENT_METHOD_MAX_LENGTH = 4
IBAN_MAX_LENGTH = 34
MONEY_MAX_DIGITS = 12
MONEY_DECIMAL_PLACES = 2
RATE_MAX_DIGITS = 5
RATE_DECIMAL_PLACES = 2
DUE_DAYS_MIN = 0
DUE_DAYS_MAX = 365

# The previous system shipped `RiferimentoNormativo` as "N2.2 (non soggette - altri casi)", which is
# the *description of the code*, not a normative reference. This is the real one for
# the flat-rate regime, and it is a default rather than a constant because the article
# numbers have changed before.
DEFAULT_LEGAL_REFERENCE = (
    "Operazione non soggetta a IVA ai sensi dell'art. 1, commi 54-89, "
    "L. 190/2014 - regime forfettario"
)

# A customer established outside Italy (ORB-32). A service to a business abroad is
# outside the territorial scope of Italian VAT under art. 7-ter DPR 633/1972, which the
# SdI codes as `N2.1`, not as the `N2.2` the flat-rate regime's own declaration covers: the
# accountant's tool issued 13/2026 to a GB company with N2.1 while every domestic
# invoice carries N2.2, and the two registers have to agree. The annotation the invoice
# must carry is dictated by art. 21, comma 6-bis, DPR 633/1972 and differs by where the
# customer is established: lett. a), a taxable customer in another EU member state,
# "inversione contabile"; lett. b), a customer outside the EU, "operazione non
# soggetta". Constants rather than profile columns because `fiscal_profile` describes
# the issuer and this depends on the customer; both texts stay within FPR12's 100
# characters for `RiferimentoNormativo`, which is why neither also spells out L. 190/2014.
VAT_NATURE_NOT_RESIDENT = "N2.1"
LEGAL_REFERENCE_UE = "Inversione contabile - art. 7-ter DPR 633/1972 - regime forfettario"
LEGAL_REFERENCE_EXTRA_UE = (
    "Operazione non soggetta a IVA in Italia ai sensi dell'art. 7-ter DPR 633/1972 "
    "- regime forfettario"
)

# The 27 member states, ISO 3166-1 alpha-2 (Greece is `GR`, not the VAT prefix `EL`).
# Italy is in the set because it is a member state; the strategy handles it first, so
# membership here only ever decides between the two texts above for a foreign customer.
COUNTRIES_UE = frozenset(
    {
        "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU",
        "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK",
    }
)  # fmt: skip

# Values of law, not preferences: 77.47 EUR is the threshold above which the stamp
# duty is due and 2.00 EUR is its amount. Configurable because the law has already
# changed them once.
DEFAULT_STAMP_DUTY_THRESHOLD = Decimal("77.47")
DEFAULT_STAMP_DUTY_AMOUNT = Decimal("2.00")

# §4.5. The estimate's three rates (profitability 0.67, substitute tax 0.05, INPS 0.2607),
# expressed as percentages, which is how their owner reads and types them. Defaults
# rather than constants for the same reason the stamp duty values above are: the ATECO
# coefficient depends on the activity code, and the INPS gestione separata rate is
# re-set by the Legge di Bilancio most years.
DEFAULT_PROFITABILITY_COEFFICIENT = Decimal("67.00")
DEFAULT_SUBSTITUTE_TAX_RATE = Decimal("5.00")
DEFAULT_SOCIAL_SECURITY_RATE = Decimal("26.07")

# The ordinary regime (REB-760): `RF01` is FPR12's code for it, and 22% is the standard VAT
# rate (art. 16, comma 1, DPR 633/1972), the profile's proposed default and never a
# constant of an invoice: a line takes the rate it is given. The same figure mastro's
# `it-standard` pack carries as its `defaultTreatment`.
REGIME_CODE_ORDINARY = "RF01"
DEFAULT_VAT_RATE_ORDINARY = Decimal("22.00")

# A percentage: `Numeric(5, 2)` alone would accept `999.99`, and a rate outside 0..100 is
# not a rate. `decimal_places` matters as much -- `1.005` would otherwise be rounded by
# Postgres into a value nobody asked for.
RATE_MIN = 0
RATE_MAX = 100


class FiscalSnapshot(BaseModel):
    """The parameters as they were when an invoice was issued.

    Frozen, so nothing downstream of `InvoiceService.issue` can mutate a value the
    document was built from; `extra="forbid"` so a stored snapshot written by a later
    version of this model is a loud failure rather than a silently ignored field.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # `None` on a foreign legal entity (REB-619): RF01..RF19 are FatturaPA values and mean
    # nothing abroad. `pack_id` defaults to the Italian pack so every snapshot frozen
    # before the column reached the snapshot still validates, and reads as what it was.
    regime_code: str | None = Field(default=None, max_length=REGIME_CODE_MAX_LENGTH)
    pack_id: str = PACK_IT_FLAT_RATE
    default_vat_rate: Decimal = Field(
        max_digits=RATE_MAX_DIGITS, decimal_places=RATE_DECIMAL_PLACES
    )
    default_vat_nature: str | None = Field(default=None, max_length=VAT_NATURE_MAX_LENGTH)
    legal_reference: str | None = None
    apply_stamp_duty: bool
    stamp_duty_threshold: Decimal = Field(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES
    )
    stamp_duty_amount: Decimal = Field(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES
    )
    payment_terms: str = Field(max_length=PAYMENT_TERMS_MAX_LENGTH)
    payment_method: str = Field(max_length=PAYMENT_METHOD_MAX_LENGTH)
    due_days: int = Field(ge=DUE_DAYS_MIN, le=DUE_DAYS_MAX)
    iban: str | None = Field(default=None, max_length=IBAN_MAX_LENGTH)


class FiscalProfileUpsert(BaseModel):
    """One shape for create and update: there is only ever one row, so "create" and
    "update" are the same operation with the same required fields -- the same decision
    the legal entity's own upsert already made.

    `regime_code` carries `max_length` because the column is `String(4)` and the
    service's own `.fullmatch` check is *not* a length check on its own for a value
    that fails the pattern for another reason. Every `Numeric` carries
    `max_digits`/`decimal_places` mirroring its column, and `due_days` carries
    a bound, because none of the three has a service-level range check that would make
    a schema bound redundant.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _pack_defaults(cls, data: Any) -> Any:
        """A key the caller leaves out defaults to the pack's own value, not the
        flat-rate regime's: `default_vat_nature` «N2.2», the stamp duty on and the three income
        parameters are the flat-rate regime's facts, and a profile on another pack saved with
        them would be refused («una natura con un'aliquota diversa da zero») or, worse,
        would have the fiscal estimate compute the flat-rate regime's taxes for a legal entity it
        does not apply to. `non-it` (REB-619) is empty of all of them; `it-standard`
        (REB-760) is the ordinary regime, `RF01` at the standard VAT rate. The rule lives
        here, where the API and MCP deserialise, and not only in the SPA's form."""
        if not isinstance(data, dict):
            return data
        pack_id = data.get("pack_id")
        if pack_id not in (PACK_NON_IT, PACK_IT_STANDARD):
            return data
        data = dict(data)
        if pack_id == PACK_IT_STANDARD:
            data.setdefault("regime_code", REGIME_CODE_ORDINARY)
            data.setdefault("default_vat_rate", DEFAULT_VAT_RATE_ORDINARY)
        for key, value in (
            ("default_vat_nature", None),
            ("legal_reference", None),
            ("apply_stamp_duty", False),
            ("profitability_coefficient", None),
            ("substitute_tax_rate", None),
            ("social_security_rate", None),
        ):
            data.setdefault(key, value)
        return data

    # Optional in the schema and required by the service on an Italian pack
    # (`resolve_regime`): the same field is legitimately empty on `non-it`, and a
    # schema cannot say «required unless that other field says otherwise» without
    # the error losing the field's name. The service's refusal names `regime_code`.
    regime_code: SafeStr | None = Field(default=None, max_length=REGIME_CODE_MAX_LENGTH)
    pack_id: PackId = PACK_IT_FLAT_RATE
    default_vat_rate: Decimal = Field(
        default=Decimal("0.00"), max_digits=RATE_MAX_DIGITS, decimal_places=RATE_DECIMAL_PLACES
    )
    default_vat_nature: SafeStr | None = Field(default="N2.2", max_length=VAT_NATURE_MAX_LENGTH)
    legal_reference: SafeStr | None = Field(default=DEFAULT_LEGAL_REFERENCE)
    apply_stamp_duty: bool = True
    stamp_duty_threshold: Decimal = Field(
        default=DEFAULT_STAMP_DUTY_THRESHOLD,
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
    )
    stamp_duty_amount: Decimal = Field(
        default=DEFAULT_STAMP_DUTY_AMOUNT,
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
    )
    payment_terms: SafeStr = Field(default="TP02", max_length=PAYMENT_TERMS_MAX_LENGTH)
    payment_method: SafeStr = Field(default="MP05", max_length=PAYMENT_METHOD_MAX_LENGTH)
    due_days: int = Field(default=30, ge=DUE_DAYS_MIN, le=DUE_DAYS_MAX)
    iban: SafeStr | None = Field(default=None, max_length=IBAN_MAX_LENGTH)
    # Nullable in the column, and still defaulted here: a profile saved without them
    # would otherwise leave the fiscal estimate with nothing to compute from, and "the
    # flat-rate regime's own numbers" is a far better answer than NULL for the only regime
    # this project implements. Clearing them is still possible -- an explicit `null`.
    profitability_coefficient: Decimal | None = Field(
        default=DEFAULT_PROFITABILITY_COEFFICIENT,
        max_digits=RATE_MAX_DIGITS,
        decimal_places=RATE_DECIMAL_PLACES,
        ge=RATE_MIN,
        le=RATE_MAX,
    )
    substitute_tax_rate: Decimal | None = Field(
        default=DEFAULT_SUBSTITUTE_TAX_RATE,
        max_digits=RATE_MAX_DIGITS,
        decimal_places=RATE_DECIMAL_PLACES,
        ge=RATE_MIN,
        le=RATE_MAX,
    )
    social_security_rate: Decimal | None = Field(
        default=DEFAULT_SOCIAL_SECURITY_RATE,
        max_digits=RATE_MAX_DIGITS,
        decimal_places=RATE_DECIMAL_PLACES,
        ge=RATE_MIN,
        le=RATE_MAX,
    )
    # The version check of REB-622 (spec 2026-10-03 §11): the `updated_at` read off
    # `FiscalProfileRead`, never a column the service assigns. A save whose value is
    # not the row's is refused with `StaleRow` (409). Sent as `null` it is the version
    # of a profile not saved yet, so a first save built on «nothing there» is refused
    # once somebody else has created the row; left out, nothing is checked.
    updated_at: datetime | None = Field(
        default=None,
        description=(
            "The `updated_at` read on the profile this change starts from, `null` for a profile "
            "not yet saved: if someone else saved in the meantime, the request is rejected with "
            "409 `stale_row`. Omitted, no check."
        ),
    )


class FiscalProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    regime_code: str | None
    default_vat_rate: Decimal
    default_vat_nature: str | None
    legal_reference: str | None
    apply_stamp_duty: bool
    stamp_duty_threshold: Decimal
    stamp_duty_amount: Decimal
    payment_terms: str
    payment_method: str
    due_days: int
    iban: str | None
    # Bare `Decimal | None`, deliberately: a Read schema validates values the database
    # produced, so a bound here would reject a row the column legitimately holds.
    profitability_coefficient: Decimal | None
    substitute_tax_rate: Decimal | None
    social_security_rate: Decimal | None
    # REB-361: the jurisdiction pack pointer. Written through `FiscalProfileUpsert`
    # since REB-619 brought the second pack, `non-it`; the version is still the one
    # each pack ships.
    pack_id: str
    pack_version: str
    created_at: datetime
    updated_at: datetime


__all__ = [
    "REGIME_CODE_ORDINARY",
    "REGIME_CODE_RE",
    "PackId",
    "DEFAULT_SUBSTITUTE_TAX_RATE",
    "DEFAULT_SOCIAL_SECURITY_RATE",
    "DEFAULT_VAT_RATE_ORDINARY",
    "DEFAULT_PROFITABILITY_COEFFICIENT",
    "DEFAULT_STAMP_DUTY_AMOUNT",
    "DEFAULT_LEGAL_REFERENCE",
    "DEFAULT_STAMP_DUTY_THRESHOLD",
    "FiscalProfileRead",
    "FiscalProfileUpsert",
    "FiscalSnapshot",
    "VAT_NATURE_NOT_RESIDENT",
    "COUNTRIES_UE",
    "LEGAL_REFERENCE_EXTRA_UE",
    "LEGAL_REFERENCE_UE",
    "SafeStr",
]
