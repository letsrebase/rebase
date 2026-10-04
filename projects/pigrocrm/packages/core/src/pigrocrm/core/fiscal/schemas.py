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

from pigrocrm.core.fiscal.pack import PACK_IT_FLAT_RATE, PACK_NON_IT
from pigrocrm.core.validation import SafeStr

# The packs a profile may point at (REB-619): `pack.py` owns them, this is the shape
# the API and MCP accept. A `Literal` and not a free string, so a typo is refused by
# the schema instead of reaching `resolve_pack`'s `KeyError` at the first emission.
PackId = Literal["it-flat-rate", "non-it"]
assert set(get_args(PackId)) == {PACK_IT_FLAT_RATE, PACK_NON_IT}  # noqa: S101

# `RF01`..`RF19`, the codes FPR12's own `RegimeFiscaleType` enumerates. `.fullmatch`
# is what the callers use, never `.match` with `$`: "RF19\n" is five characters and
# would reach the String(4) column as a raw, session-poisoning DataError.
CODICE_REGIME_RE = re.compile(r"RF(0[1-9]|1[0-9])")

CODICE_REGIME_MAX_LENGTH = 4
NATURA_MAX_LENGTH = 4
CONDIZIONI_PAGAMENTO_MAX_LENGTH = 4
MODALITA_PAGAMENTO_MAX_LENGTH = 4
IBAN_MAX_LENGTH = 34
MONEY_MAX_DIGITS = 12
MONEY_DECIMAL_PLACES = 2
RATE_MAX_DIGITS = 5
RATE_DECIMAL_PLACES = 2
GIORNI_SCADENZA_MIN = 0
GIORNI_SCADENZA_MAX = 365

# The previous system shipped `RiferimentoNormativo` as "N2.2 (non soggette - altri casi)", which is
# the *description of the code*, not a normative reference. This is the real one for
# the forfettario, and it is a default rather than a constant because the article
# numbers have changed before.
DEFAULT_RIFERIMENTO_NORMATIVO = (
    "Operazione non soggetta a IVA ai sensi dell'art. 1, commi 54-89, "
    "L. 190/2014 - regime forfettario"
)

# A customer established outside Italy (ORB-32). A service to a business abroad is
# outside the territorial scope of Italian VAT under art. 7-ter DPR 633/1972, which the
# SdI codes as `N2.1`, not as the `N2.2` the forfettario's own declaration covers: the
# accountant's tool issued 13/2026 to a GB company with N2.1 while every domestic
# invoice carries N2.2, and the two registers have to agree. The annotation the invoice
# must carry is dictated by art. 21, comma 6-bis, DPR 633/1972 and differs by where the
# customer is established: lett. a), a taxable customer in another EU member state,
# "inversione contabile"; lett. b), a customer outside the EU, "operazione non
# soggetta". Constants rather than profile columns because `fiscal_profile` describes
# the issuer and this depends on the customer; both texts stay within FPR12's 100
# characters for `RiferimentoNormativo`, which is why neither also spells out L. 190/2014.
NATURA_NON_RESIDENTE = "N2.1"
RIFERIMENTO_NORMATIVO_UE = "Inversione contabile - art. 7-ter DPR 633/1972 - regime forfettario"
RIFERIMENTO_NORMATIVO_EXTRA_UE = (
    "Operazione non soggetta a IVA in Italia ai sensi dell'art. 7-ter DPR 633/1972 "
    "- regime forfettario"
)

# The 27 member states, ISO 3166-1 alpha-2 (Greece is `GR`, not the VAT prefix `EL`).
# Italy is in the set because it is a member state; the strategy handles it first, so
# membership here only ever decides between the two texts above for a foreign customer.
PAESI_UE = frozenset(
    {
        "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU",
        "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK",
    }
)  # fmt: skip

# Values of law, not preferences: 77.47 EUR is the threshold above which the stamp
# duty is due and 2.00 EUR is its amount. Configurable because the law has already
# changed them once.
DEFAULT_SOGLIA_BOLLO = Decimal("77.47")
DEFAULT_IMPORTO_BOLLO = Decimal("2.00")

# §4.5. The three constants that used to live nel gestionale precedente's `App.jsx` --
# `FORFETTARIO_PROFITABILITY_RATE = 0.67`, `FORFETTARIO_SUBSTITUTE_TAX_RATE = 0.05`,
# `FORFETTARIO_INPS_RATE = 0.2607` -- expressed as percentages, which is how their owner
# reads and types them. Defaults rather than constants for the same reason the bollo
# values above are: the ATECO coefficient depends on the activity code, and the INPS
# gestione separata rate is re-set by the Legge di Bilancio most years.
DEFAULT_COEFFICIENTE_REDDITIVITA = Decimal("67.00")
DEFAULT_ALIQUOTA_IMPOSTA_SOSTITUTIVA = Decimal("5.00")
DEFAULT_ALIQUOTA_INPS = Decimal("26.07")

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

    # `None` on a foreign azienda (REB-619): RF01..RF19 are FatturaPA values and mean
    # nothing abroad. `pack_id` defaults to the Italian pack so every snapshot frozen
    # before the column reached the snapshot still validates, and reads as what it was.
    codice_regime: str | None = Field(default=None, max_length=CODICE_REGIME_MAX_LENGTH)
    pack_id: str = PACK_IT_FLAT_RATE
    aliquota_iva_default: Decimal = Field(
        max_digits=RATE_MAX_DIGITS, decimal_places=RATE_DECIMAL_PLACES
    )
    natura_default: str | None = Field(default=None, max_length=NATURA_MAX_LENGTH)
    riferimento_normativo: str | None = None
    applica_bollo: bool
    soglia_bollo: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    importo_bollo: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    condizioni_pagamento: str = Field(max_length=CONDIZIONI_PAGAMENTO_MAX_LENGTH)
    modalita_pagamento: str = Field(max_length=MODALITA_PAGAMENTO_MAX_LENGTH)
    giorni_scadenza: int = Field(ge=GIORNI_SCADENZA_MIN, le=GIORNI_SCADENZA_MAX)
    iban: str | None = Field(default=None, max_length=IBAN_MAX_LENGTH)


class FiscalProfileUpsert(BaseModel):
    """One shape for create and update: there is only ever one row, so "create" and
    "update" are the same operation with the same required fields -- the same decision
    `AziendaUpsert` already made.

    `codice_regime` carries `max_length` because the column is `String(4)` and the
    service's own `.fullmatch` check is *not* a length check on its own for a value
    that fails the pattern for another reason. Every `Numeric` carries
    `max_digits`/`decimal_places` mirroring its column, and `giorni_scadenza` carries
    a bound, because none of the three has a service-level range check that would make
    a schema bound redundant.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _foreign_defaults(cls, data: Any) -> Any:
        """On the `non-it` pack a key the caller leaves out is empty, not the
        forfettario's (REB-619): `natura_default` «N2.2», the bollo on and the three
        income parameters are Italian facts, and a foreign profile saved with them would
        be refused («una natura con un'aliquota diversa da zero») or, worse, would have
        the fiscal estimate compute Italian taxes. The rule lives here, where the API
        and MCP deserialise, and not only in the SPA's form."""
        if isinstance(data, dict) and data.get("pack_id") == PACK_NON_IT:
            data = dict(data)
            for key, value in (
                ("natura_default", None),
                ("riferimento_normativo", None),
                ("applica_bollo", False),
                ("coefficiente_redditivita", None),
                ("aliquota_imposta_sostitutiva", None),
                ("aliquota_inps", None),
            ):
                data.setdefault(key, value)
        return data

    # Optional in the schema and required by the service on an Italian pack
    # (`resolve_regime`): the same field is legitimately empty on `non-it`, and a
    # schema cannot say «required unless that other field says otherwise» without
    # the error losing the field's name. The service's refusal names `codice_regime`.
    codice_regime: SafeStr | None = Field(default=None, max_length=CODICE_REGIME_MAX_LENGTH)
    pack_id: PackId = PACK_IT_FLAT_RATE
    aliquota_iva_default: Decimal = Field(
        default=Decimal("0.00"), max_digits=RATE_MAX_DIGITS, decimal_places=RATE_DECIMAL_PLACES
    )
    natura_default: SafeStr | None = Field(default="N2.2", max_length=NATURA_MAX_LENGTH)
    riferimento_normativo: SafeStr | None = Field(default=DEFAULT_RIFERIMENTO_NORMATIVO)
    applica_bollo: bool = True
    soglia_bollo: Decimal = Field(
        default=DEFAULT_SOGLIA_BOLLO,
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
    )
    importo_bollo: Decimal = Field(
        default=DEFAULT_IMPORTO_BOLLO,
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
    )
    condizioni_pagamento: SafeStr = Field(
        default="TP02", max_length=CONDIZIONI_PAGAMENTO_MAX_LENGTH
    )
    modalita_pagamento: SafeStr = Field(default="MP05", max_length=MODALITA_PAGAMENTO_MAX_LENGTH)
    giorni_scadenza: int = Field(default=30, ge=GIORNI_SCADENZA_MIN, le=GIORNI_SCADENZA_MAX)
    iban: SafeStr | None = Field(default=None, max_length=IBAN_MAX_LENGTH)
    # Nullable in the column, and still defaulted here: a profile saved without them
    # would otherwise leave the fiscal estimate with nothing to compute from, and "the
    # forfettario's own numbers" is a far better answer than NULL for the only regime
    # this project implements. Clearing them is still possible -- an explicit `null`.
    coefficiente_redditivita: Decimal | None = Field(
        default=DEFAULT_COEFFICIENTE_REDDITIVITA,
        max_digits=RATE_MAX_DIGITS,
        decimal_places=RATE_DECIMAL_PLACES,
        ge=RATE_MIN,
        le=RATE_MAX,
    )
    aliquota_imposta_sostitutiva: Decimal | None = Field(
        default=DEFAULT_ALIQUOTA_IMPOSTA_SOSTITUTIVA,
        max_digits=RATE_MAX_DIGITS,
        decimal_places=RATE_DECIMAL_PLACES,
        ge=RATE_MIN,
        le=RATE_MAX,
    )
    aliquota_inps: Decimal | None = Field(
        default=DEFAULT_ALIQUOTA_INPS,
        max_digits=RATE_MAX_DIGITS,
        decimal_places=RATE_DECIMAL_PLACES,
        ge=RATE_MIN,
        le=RATE_MAX,
    )


class FiscalProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    codice_regime: str | None
    aliquota_iva_default: Decimal
    natura_default: str | None
    riferimento_normativo: str | None
    applica_bollo: bool
    soglia_bollo: Decimal
    importo_bollo: Decimal
    condizioni_pagamento: str
    modalita_pagamento: str
    giorni_scadenza: int
    iban: str | None
    # Bare `Decimal | None`, deliberately: a Read schema validates values the database
    # produced, so a bound here would reject a row the column legitimately holds.
    coefficiente_redditivita: Decimal | None
    aliquota_imposta_sostitutiva: Decimal | None
    aliquota_inps: Decimal | None
    # REB-361: the jurisdiction pack pointer. Written through `FiscalProfileUpsert`
    # since REB-619 brought the second pack, `non-it`; the version is still the one
    # each pack ships.
    pack_id: str
    pack_version: str
    created_at: datetime
    updated_at: datetime


__all__ = [
    "CODICE_REGIME_RE",
    "PackId",
    "DEFAULT_ALIQUOTA_IMPOSTA_SOSTITUTIVA",
    "DEFAULT_ALIQUOTA_INPS",
    "DEFAULT_COEFFICIENTE_REDDITIVITA",
    "DEFAULT_IMPORTO_BOLLO",
    "DEFAULT_RIFERIMENTO_NORMATIVO",
    "DEFAULT_SOGLIA_BOLLO",
    "FiscalProfileRead",
    "FiscalProfileUpsert",
    "FiscalSnapshot",
    "NATURA_NON_RESIDENTE",
    "PAESI_UE",
    "RIFERIMENTO_NORMATIVO_EXTRA_UE",
    "RIFERIMENTO_NORMATIVO_UE",
    "SafeStr",
]
