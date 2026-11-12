"""The fiscal parameters, as a value object and as the API's Create/Read pair.

`FiscalSnapshot` is what gets frozen onto an issued invoice and what the exporter and
the PDF read. It is a plain Pydantic model with no `id` and no timestamps precisely so
that it can be serialised into `invoices.snapshot` and read back three years later
without the row it came from still existing in its original shape.
"""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, get_args
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from pigrocrm.core.fiscal.law import DeclaredTax
from pigrocrm.core.fiscal.law_flat_rate import ANNEX_4
from pigrocrm.core.fiscal.pack import (
    PACK_IT_COMPANY,
    PACK_IT_FLAT_RATE,
    PACK_IT_STANDARD,
    PACK_NON_IT,
    current_pack,
)
from pigrocrm.core.validation import SafeStr

# The packs a profile may point at (REB-619): `pack.py` owns them, this is the shape
# the API and MCP accept. A `Literal` and not a free string, so a typo is refused by
# the schema instead of reaching `resolve_pack`'s `KeyError` at the first emission.
PackId = Literal["it-flat-rate", "it-standard", "it-company", "non-it"]
InpsCategory = Literal["gestione_separata", "artigiani_commercianti"]
InpsTrade = Literal["artigiani", "commercianti"]
# Which kind of income the activity of an ordinary-regime person produces (REB-1099): art or
# profession (art. 53 TUIR, the income art. 25 DPR 600/1973 withholds on) or business. `None` is
# «not stated» and is read as a professional's, the way the product has always read it.
IncomeKind = Literal["self_employment", "business"]
IvaFrequency = Literal["monthly", "quarterly"]
# The taxpayer facts of REB-1070 that the law of the INPS contributions reads (an ordinary or
# flat-rate person), and the ones only the flat-rate regime reads. A pack that does not use a
# fact carries none of it (`FiscalProfileService.check` refuses a value, the form hides it).
INPS_PARAMETERS = (
    "inps_trade",
    "inps_covered_elsewhere",
    "inps_before_1996",
    "flat_rate_inps_reduction",
    "inps_over_65_reduction",
    "inps_new_enrolment_reduction",
    "inps_first_enrolment_month",
)
FLAT_RATE_PARAMETERS = (
    "ateco_group",
    "flat_rate_start_year",
    "startup_no_prior_activity",
    "startup_not_a_continuation",
    "startup_predecessor_within_limit",
)
# `flat_rate_inps_reduction` is the flat-rate regime's election, so it is a flat-rate fact too.
FLAT_RATE_ONLY = ("flat_rate_inps_reduction", *FLAT_RATE_PARAMETERS)
TAXPAYER_PARAMETERS = (*INPS_PARAMETERS, *FLAT_RATE_PARAMETERS)
# The profile keys a save that does not name them leaves as they are (`stored_fields`).
KEPT_WHEN_OMITTED = ("inps_category", "isa", "income_kind", "iva_frequency", *TAXPAYER_PARAMETERS)
ATECO_GROUP_KEYS = tuple(group.key for group in ANNEX_4)
FLAT_RATE_START_YEAR_MIN = 1900
FLAT_RATE_START_YEAR_MAX = 2100
assert set(get_args(PackId)) == {  # noqa: S101
    PACK_IT_FLAT_RATE,
    PACK_IT_STANDARD,
    PACK_IT_COMPANY,
    PACK_NON_IT,
}

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

# The ordinary regime's own annotations for a service to a business abroad (REB-994): the
# same two readings of art. 21 c. 6-bis DPR 633/72 as above, without the flat-rate regime's
# mention.
LEGAL_REFERENCE_UE_ORDINARY = "Inversione contabile - art. 7-ter DPR 633/1972"
LEGAL_REFERENCE_EXTRA_UE_ORDINARY = (
    "Operazione non soggetta a IVA in Italia ai sensi dell'art. 7-ter DPR 633/1972"
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
    # REB-1099. `None` (not answered, and every snapshot frozen before the field) keeps the
    # withholding: the product has always read the ordinary regime as a professional's.
    income_kind: str | None = None


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
        (REB-760) and `it-company` (REB-1005) are the ordinary regime, `RF01` at the standard VAT
        rate. The rule lives
        here, where the API and MCP deserialise, and not only in the SPA's form."""
        if not isinstance(data, dict):
            return data
        pack_id = data.get("pack_id")
        if pack_id not in (PACK_NON_IT, PACK_IT_STANDARD, PACK_IT_COMPANY):
            return data
        data = dict(data)
        if pack_id in (PACK_IT_STANDARD, PACK_IT_COMPANY):
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
        if pack_id == PACK_NON_IT:
            # A company abroad has no Italian INPS category and no ISA.
            data.setdefault("inps_category", None)
            data.setdefault("isa", None)
        # The taxpayer facts of REB-1070 follow the pack: a pack that does not read one has
        # it cleared by a save that names no value, so moving a profile to it leaves none.
        if pack_id in (PACK_NON_IT, PACK_IT_COMPANY):
            for key in TAXPAYER_PARAMETERS:
                data.setdefault(key, None)
        elif pack_id == PACK_IT_STANDARD:
            for key in FLAT_RATE_ONLY:
                data.setdefault(key, None)
        return data

    @field_validator("inps_first_enrolment_month")
    @classmethod
    def _month_of_enrolment(cls, value: date | None) -> date | None:
        """The month INPS counts the 36 months from: any day of it names it, the first day is
        what is stored."""
        return value.replace(day=1) if value is not None else None

    @field_validator("ateco_group")
    @classmethod
    def _known_ateco_group(cls, value: str | None) -> str | None:
        if value is not None and value not in ATECO_GROUP_KEYS:
            raise ValueError(
                "gruppo ATECO sconosciuto: l'allegato 4 della legge 190/2014 ha "
                + ", ".join(ATECO_GROUP_KEYS)
            )
        return value

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
    # REB-974, taxes design § 1: the taxpayer's other parameters. `None` is «not stated»,
    # never a default: a figure that needs one gives no figure. The category's values are
    # the design's; its rates, minimums and dates are the pack's (`FiscalPack.inps_for`, A5
    # verified by REB-1064). The ISA flag decides 50/50 or 40/60 income tax advances (A5,
    # verified).
    inps_category: InpsCategory | None = None
    isa: bool | None = None
    # REB-1099: whether the income is an art or profession or a business. Only the ordinary
    # regime of a person (`it-standard`) is asked; the 20% withholding of art. 25 DPR 600/1973
    # is owed on the first alone. `None` keeps today's reading, a professional.
    income_kind: IncomeKind | None = None
    # REB-1070: the facts the INPS and flat-rate law read. `None` is «not stated», never a
    # default: a figure that needs one names it as missing. Kept when a save leaves them out.
    inps_trade: InpsTrade | None = None
    inps_covered_elsewhere: bool | None = None
    inps_before_1996: bool | None = None
    flat_rate_inps_reduction: bool | None = None
    # REB-1097: the facts of the INPS reductions, each «granted by INPS», never inferred. The
    # month is any day of the month of the first enrolment; the first day is stored.
    inps_over_65_reduction: bool | None = None
    inps_new_enrolment_reduction: bool | None = None
    inps_first_enrolment_month: date | None = None
    ateco_group: SafeStr | None = Field(default=None, max_length=60)
    flat_rate_start_year: int | None = Field(
        default=None, ge=FLAT_RATE_START_YEAR_MIN, le=FLAT_RATE_START_YEAR_MAX
    )
    # The three conditions of c. 65 for the 5% rate, answered as the law states them:
    # `True` when the condition is met.
    startup_no_prior_activity: bool | None = None
    startup_not_a_continuation: bool | None = None
    startup_predecessor_within_limit: bool | None = None
    # REB-1021: how often the entity settles its IVA. Kept as it is when a save leaves it out,
    # like the two above; only a pack with a periodic IVA may say `quarterly`.
    iva_frequency: IvaFrequency = "monthly"
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

    def stored_fields(self) -> dict[str, Any]:
        """The columns a save writes: the body without its version, and without the two
        taxpayer parameters (`KEPT_WHEN_OMITTED`: REB-974's `inps_category` and `isa`, the
        facts of REB-1070) when the caller did not name them. The rest of the profile is a
        whole-row replacement, so a key left out goes back to its default; these have no
        default worth restoring, so a save that does not mention them leaves what the row
        holds."""
        payload = self.model_dump(exclude={"updated_at"})
        for key in KEPT_WHEN_OMITTED:
            if key not in self.model_fields_set:
                del payload[key]
        if self.pack_id != PACK_IT_STANDARD and "income_kind" not in self.model_fields_set:
            # Moving a person to another pack leaves no answer behind: the question is only
            # asked of the ordinary regime (REB-1099). A value sent for another pack is
            # refused by `FiscalProfileService.check`, not dropped here.
            payload["income_kind"] = None
        return payload


class FlatRateGroupRead(BaseModel):
    """One row of annex 4 of L. 190/2014 as the form lists it."""

    key: str
    number: int
    name: str
    ateco_2007: list[str]
    coefficient: Decimal


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
    inps_category: str | None
    isa: bool | None
    income_kind: str | None
    inps_trade: str | None
    inps_covered_elsewhere: bool | None
    inps_before_1996: bool | None
    flat_rate_inps_reduction: bool | None
    inps_over_65_reduction: bool | None
    inps_new_enrolment_reduction: bool | None
    inps_first_enrolment_month: date | None
    ateco_group: str | None
    flat_rate_start_year: int | None
    startup_no_prior_activity: bool | None
    startup_not_a_continuation: bool | None
    startup_predecessor_within_limit: bool | None
    iva_frequency: str
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def flat_rate_groups(self) -> list["FlatRateGroupRead"]:
        """Annex 4's groups, from the newest law of the flat-rate pack, whichever pack this
        profile is on: a form that switches the regime to the flat-rate one before saving
        needs the list too. What the form's group list and the coefficient it shows come
        from, so the web carries no figure of law."""
        flat_rate = next(
            (
                law.flat_rate
                for law in reversed(current_pack(PACK_IT_FLAT_RATE).laws)
                if law.flat_rate is not None
            ),
            None,
        )
        if flat_rate is None:
            return []
        return [
            FlatRateGroupRead(
                key=group.key,
                number=group.number,
                name=group.name,
                ateco_2007=list(group.ateco_2007),
                coefficient=group.coefficient,
            )
            for group in flat_rate.coefficients
        ]


# The taxes a declared figure may be typed for: the design's own names (taxes design § 1 and
# § 2). Validated here, in code, like the due items' source kinds.
DECLARED_TAXES: tuple[DeclaredTax, ...] = get_args(DeclaredTax)
# How a declared figure came to be: typed by an administrator today; a figure read from a
# document and accepted by a person is a later card.
DeclaredSource = Literal["typed"]


class DeclaredTaxFigureSet(BaseModel):
    """What an administrator types: the figures of the tax return as the accountant computed
    them. Whatever is left out is recorded as not given (never as zero), and typing the same
    tax and year again replaces the three together. `tax_due` and `advances` are never
    negative; `balance` is signed, a credit being negative."""

    model_config = ConfigDict(extra="forbid")

    tax: DeclaredTax
    year: int = Field(ge=2000, le=2200)
    tax_due: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    balance: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    advances: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)

    @model_validator(mode="after")
    def _at_least_one_figure(self) -> "DeclaredTaxFigureSet":
        if self.tax_due is None and self.balance is None and self.advances is None:
            raise ValueError("indica almeno una cifra: imposta dovuta, saldo o acconti")
        return self


class DeclaredTaxFigureRead(BaseModel):
    """A declared figure as stored. `reading` says which of the three readings of a year it
    is (estimate, declared, paid), so a screen never presents it as an estimate."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    reading: Literal["declared"] = "declared"
    legal_entity_id: UUID
    tax: str
    year: int = Field(validation_alias="tax_year")
    tax_due: Decimal | None
    balance: Decimal | None
    advances: Decimal | None
    source: str
    updated_at: datetime


# Which of a tax line's readings counts: the highest stage that exists, never the latest one.
ReadingStage = Literal["estimate", "declared", "paid"]


class TaxReadingLine(BaseModel):
    """One tax of the year with its three readings (REB-1026, `fiscal/readings.py`). A figure
    that does not exist is `None` and a screen draws it as «no reading yet» or «not
    computable», never as zero. `declared` is the tax due of the typed return; its balance and
    advances stay on the declared rows beside it."""

    model_config = ConfigDict(frozen=True)

    tax: DeclaredTax
    # Whether the code has an estimate for this tax in this year. When it has and `estimate` is
    # `None`, a profile parameter is missing; when it has none (IRPEF, IRES, IRAP, the
    # ordinary regime's INPS: their rules are «to verify»), `estimate` is `None` for good.
    estimable: bool
    estimate: Decimal | None
    declared: Decimal | None
    # No source writes a paid figure yet: always `None` until the F24 due items exist.
    paid: Decimal | None = None
    # The reading that counts for this tax, `None` when none of the three exists.
    counts: ReadingStage | None


class TaxReadingTotals(BaseModel):
    """A total per column, each adding readings of one kind only: the lines with no figure of
    that kind are left out of it and counted in `*_left_out`, so a screen says «does not
    include N lines». A total is `None` when no line has a figure of its kind."""

    model_config = ConfigDict(frozen=True)

    estimate: Decimal | None
    estimate_left_out: int
    declared: Decimal | None
    declared_left_out: int
    paid: Decimal | None
    paid_left_out: int


class TaxReadings(BaseModel):
    """The year's taxes of one legal entity, as the pack lists them, with their readings."""

    model_config = ConfigDict(frozen=True)

    lines: list[TaxReadingLine]
    totals: TaxReadingTotals


IvaOutcome = Literal["due", "carried", "credit", "nil"]


class IvaPeriod(BaseModel):
    """One IVA period: a month (1 to 12) or a quarter (1 to 4) of a calendar year, in the
    frequency the legal entity settles in."""

    model_config = ConfigDict(extra="forbid")

    year: int = Field(ge=2000, le=2100)
    number: int = Field(ge=1, le=12)


class IvaPeriodLockCreate(IvaPeriod):
    """Close one period of one legal entity's IVA cycle (REB-1036). `legal_entity_id` is required
    when the space has more than one legal entity."""

    legal_entity_id: UUID | None = None


class IvaPeriodLockRead(BaseModel):
    """A closed IVA period: while it is closed no document that changes its registers is written.
    `closed_by` is the person who closed it (the owner of the token, for an agent)."""

    id: UUID
    legal_entity_id: UUID
    frequency: IvaFrequency
    year: int
    number: int
    label: str
    period_start: date
    period_end: date
    closed_at: datetime
    closed_by: UUID | None


class IvaSettlementRead(BaseModel):
    """A period's settlement as stored (REB-1021). Positive `result` is owed, negative is a
    credit carried forward; `due_item_id` is the item written for an `outcome` of `due`."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    legal_entity_id: UUID
    frequency: IvaFrequency
    year: int
    period_number: int
    period_start: date
    period_end: date
    pack_id: str
    pack_version: str
    sales_vat: Decimal
    reverse_charge_vat: Decimal
    purchase_vat: Decimal
    credit_in: Decimal
    debit_in: Decimal
    surcharge: Decimal
    result: Decimal
    outcome: IvaOutcome
    payment_due_on: date
    due_item_id: UUID | None = None


IvaPeriodState = Literal["future", "open", "ended"]
IvaPaymentState = Literal["expected", "settled"]
IvaExigibility = Literal["immediate", "split_payment"]
IvaSalesRowKind = Literal["invoice", "gap"]
IvaSalesStatus = Literal["issued", "cancelled"]


class IvaEntityRead(BaseModel):
    """A legal entity whose pack settles IVA by period, so it has registers (REB-1024)."""

    legal_entity_id: UUID
    name: str
    frequency: IvaFrequency


class IvaLadderRead(BaseModel):
    """The settlement's figures, as `IvaSettlementRead` carries them. A positive `result` is
    owed, a negative one is a credit carried forward."""

    sales_vat: Decimal
    reverse_charge_vat: Decimal
    purchase_vat: Decimal
    credit_in: Decimal
    debit_in: Decimal
    surcharge: Decimal
    result: Decimal
    outcome: IvaOutcome


class IvaPeriodRead(BaseModel):
    """One period of a legal entity's IVA cycle (REB-1024). `ladder` is the stored settlement's
    when `settled`, otherwise the running figures of the period computed with the same code
    from the previous period's stored result, and `None` when the server cannot know them: a
    period that has not started, or one whose previous period is not settled while an earlier one
    is (its carry-over would be a guess). `state` is `open` until the period's last day
    included. The payment date is the pack's calendar's and `payment_state` the due item's;
    `carry_forward_limit` is the pack's limit under which a small debit waits. `closed` is whether
    a person closed the period (REB-1036): no document that changes its registers is written
    until it is reopened, and `closed_at` and `closed_by` say when and by whom."""

    legal_entity_id: UUID
    frequency: IvaFrequency
    year: int
    number: int
    label: str
    period_start: date
    period_end: date
    state: IvaPeriodState
    settled: bool
    ladder: IvaLadderRead | None
    payment_due_on: date | None
    due_item_id: UUID | None
    payment_state: IvaPaymentState | None
    # A settled period whose registers now add up to other lines than the stored ones.
    registers_changed: bool
    carry_forward_limit: Decimal | None
    closed: bool
    closed_at: datetime | None
    closed_by: UUID | None


class IvaRateGroupRead(BaseModel):
    """One VAT recap group of a document: a rate (and its nature at zero), the taxable amount
    and the VAT."""

    rate: Decimal
    nature: str | None
    taxable_amount: Decimal
    vat_amount: Decimal


class IvaPurchaseGroupRead(IvaRateGroupRead):
    deductible_share: Decimal
    deductible_vat: Decimal


class IvaSalesRowRead(BaseModel):
    """A line of the sales register: an issued or annulled invoice, or a number the legal
    entity declared its register will never carry (`kind` `gap`, with its reason). The VAT of
    a document under split payment is shown and is not owed (`exigibility`)."""

    kind: IvaSalesRowKind
    invoice_id: UUID | None
    year: int
    number: int
    issue_date: date | None
    customer_id: UUID | None
    customer_name: str | None
    status: IvaSalesStatus | None
    document_type: str | None
    exigibility: IvaExigibility | None
    groups: list[IvaRateGroupRead]
    taxable_amount: Decimal | None
    vat_amount: Decimal | None
    total: Decimal | None
    gap_reason: str | None


class IvaSalesSummaryLine(BaseModel):
    rate: Decimal
    nature: str | None
    exigibility: IvaExigibility
    taxable_amount: Decimal
    vat_amount: Decimal


class IvaSalesTotals(BaseModel):
    """The period's, over every issued document and not the page's. `vat_due` is the sales VAT
    of the settlement: the VAT of the documents not under split payment."""

    taxable_amount: Decimal
    vat_amount: Decimal
    vat_due: Decimal
    total: Decimal


class IvaSalesRegisterRead(BaseModel):
    items: list[IvaSalesRowRead]
    total_count: int
    next_offset: int | None
    summary: list[IvaSalesSummaryLine]
    totals: IvaSalesTotals


class IvaPurchaseRowRead(BaseModel):
    """A line of the purchase register: a registered invoice or credit note placed in the period
    by its `iva_period`, with its groups at their deductible share."""

    purchase_id: UUID
    registered_on: date
    document_date: date
    number: str | None
    kind: Literal["invoice", "credit_note"]
    supplier_id: UUID | None
    supplier_name: str | None
    reverse_charge: bool
    split_payment: bool
    groups: list[IvaPurchaseGroupRead]
    taxable_amount: Decimal
    vat_amount: Decimal
    deductible_vat: Decimal
    total: Decimal


class IvaPurchaseSummaryLine(BaseModel):
    rate: Decimal
    nature: str | None
    deductible_share: Decimal
    taxable_amount: Decimal
    vat_amount: Decimal
    deductible_vat: Decimal


class IvaPurchaseTotals(BaseModel):
    """The period's. `deductible_vat` and `reverse_charge_vat` are the settlement's lines."""

    taxable_amount: Decimal
    vat_amount: Decimal
    deductible_vat: Decimal
    reverse_charge_vat: Decimal
    total: Decimal


class IvaPurchaseRegisterRead(BaseModel):
    items: list[IvaPurchaseRowRead]
    total_count: int
    next_offset: int | None
    summary: list[IvaPurchaseSummaryLine]
    totals: IvaPurchaseTotals


__all__ = [
    "IvaEntityRead",
    "IvaExigibility",
    "IvaFrequency",
    "IvaLadderRead",
    "IvaOutcome",
    "IvaPaymentState",
    "IvaPeriod",
    "IvaPeriodRead",
    "IvaPeriodState",
    "IvaPurchaseGroupRead",
    "IvaPurchaseRegisterRead",
    "IvaPurchaseRowRead",
    "IvaPurchaseSummaryLine",
    "IvaPurchaseTotals",
    "IvaRateGroupRead",
    "IvaSalesRegisterRead",
    "IvaSalesRowKind",
    "IvaSalesRowRead",
    "IvaSalesStatus",
    "IvaSalesSummaryLine",
    "IvaSalesTotals",
    "IvaSettlementRead",
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
    "DECLARED_TAXES",
    "DeclaredSource",
    "DeclaredTax",
    "DeclaredTaxFigureRead",
    "DeclaredTaxFigureSet",
    "ReadingStage",
    "TaxReadingLine",
    "TaxReadingTotals",
    "TaxReadings",
    "FiscalProfileRead",
    "FiscalProfileUpsert",
    "FiscalSnapshot",
    "VAT_NATURE_NOT_RESIDENT",
    "COUNTRIES_UE",
    "LEGAL_REFERENCE_EXTRA_UE",
    "LEGAL_REFERENCE_EXTRA_UE_ORDINARY",
    "LEGAL_REFERENCE_UE",
    "LEGAL_REFERENCE_UE_ORDINARY",
    "SafeStr",
]
