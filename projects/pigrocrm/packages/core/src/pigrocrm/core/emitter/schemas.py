from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.validation import SafeStr

# Mirror LegalEntity's column widths exactly (emitter/models.py). Without these an
# over-long value reaches Postgres and raises sqlalchemy.exc.DataError, which is not
# an IntegrityError subclass, so no handler catches it and the session is poisoned.
NOME_MAX_LENGTH = 80
RAGIONE_SOCIALE_MAX_LENGTH = 255
CODICE_FISCALE_MAX_LENGTH = 16
INDIRIZZO_MAX_LENGTH = 255
CAP_MAX_LENGTH = 10
COMUNE_MAX_LENGTH = 120
PROVINCIA_MAX_LENGTH = 2
NAZIONE_MAX_LENGTH = 2
PEC_MAX_LENGTH = 320
TELEFONO_MAX_LENGTH = 40
EMAIL_MAX_LENGTH = 320
SITO_WEB_MAX_LENGTH = 255
STORAGE_KEY_MAX_LENGTH = 255
REGIME_FISCALE_MAX_LENGTH = 200
# `firma_email` is a `Text` column, so it has no width for this to mirror and nothing
# above breaks if it is exceeded. Bounded anyway, on `templates/schemas.py`'s own
# reasoning about a string nested in JSONB: an unbounded text field on a signature
# block is still an unbounded text field, and a signature has no more use for 100 000
# characters than a template variable name does.
FIRMA_EMAIL_MAX_LENGTH = 2_000


class LegalEntityUpsert(BaseModel):
    """One shape for the first save and for every update: the fields are the same and
    all of them are required or defaulted, so a write is always a whole row.

    `nome` is optional on purpose: a space with one azienda never typed one, and the
    service derives it from `ragione_sociale` (cut to the column width) when it is
    missing, which is also what the migration did for the row every space already had.

    `partita_iva` and `codice_sdi` carry no `max_length`, exactly as `CustomerCreate`
    does: the service's `_check_fiscal` already requires an exact 11-digit / 7-character
    match, which is stricter, and adding a Pydantic bound would make a 12-digit input
    raise pydantic's own `ValidationError` instead of this project's `ValidationFailed`
    -- a regression, not a fix.
    """

    model_config = ConfigDict(extra="forbid")

    nome: SafeStr | None = Field(default=None, max_length=NOME_MAX_LENGTH)
    ragione_sociale: SafeStr = Field(max_length=RAGIONE_SOCIALE_MAX_LENGTH)
    partita_iva: SafeStr | None = None
    codice_fiscale: SafeStr | None = Field(default=None, max_length=CODICE_FISCALE_MAX_LENGTH)
    indirizzo: SafeStr | None = Field(default=None, max_length=INDIRIZZO_MAX_LENGTH)
    cap: SafeStr | None = Field(default=None, max_length=CAP_MAX_LENGTH)
    comune: SafeStr | None = Field(default=None, max_length=COMUNE_MAX_LENGTH)
    provincia: SafeStr | None = Field(default=None, max_length=PROVINCIA_MAX_LENGTH)
    nazione: SafeStr = Field(default="IT", max_length=NAZIONE_MAX_LENGTH)
    pec: SafeStr | None = Field(default=None, max_length=PEC_MAX_LENGTH)
    codice_sdi: SafeStr | None = None
    telefono: SafeStr | None = Field(default=None, max_length=TELEFONO_MAX_LENGTH)
    email: SafeStr | None = Field(default=None, max_length=EMAIL_MAX_LENGTH)
    sito_web: SafeStr | None = Field(default=None, max_length=SITO_WEB_MAX_LENGTH)
    # No `logo_key` and no `firma_key` since REB-627: the two images are written by
    # `LegalEntityAssets` under keys the server chooses, so a whole-row `PUT` can neither
    # clear them nor point them at somebody else's file. `update` leaves them as they are.
    firma_email: SafeStr | None = Field(default=None, max_length=FIRMA_EMAIL_MAX_LENGTH)
    regime_fiscale: SafeStr | None = Field(default=None, max_length=REGIME_FISCALE_MAX_LENGTH)


class LegalEntityCreate(LegalEntityUpsert):
    """A second azienda, born with its fiscal profile (spec 2026-10-03 §1.2, §3, §9
    milestone 5): the upsert's fields, a required short name, and the profile body
    `PUT /api/aziende/{id}/fiscal-profile` takes. One request, one transaction, so no
    azienda ever exists that `issue` would refuse with `NotFound("fiscal_profile")`.

    `nome` is required here where `LegalEntityUpsert` derives it: the first azienda of a
    space was never named, but the second is created to be told apart from the first,
    in the sidebar and on every list, and a derived name is a ragione sociale cut to
    eighty characters."""

    nome: SafeStr = Field(max_length=NOME_MAX_LENGTH)
    fiscal_profile: FiscalProfileUpsert


class LegalEntityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    predefinita: bool
    attiva: bool
    ragione_sociale: str
    partita_iva: str | None
    codice_fiscale: str | None
    indirizzo: str | None
    cap: str | None
    comune: str | None
    provincia: str | None
    nazione: str
    pec: str | None
    codice_sdi: str | None
    telefono: str | None
    email: str | None
    sito_web: str | None
    logo_key: str | None
    firma_key: str | None
    firma_email: str | None
    regime_fiscale: str | None
    created_at: datetime
    updated_at: datetime


class LegalEntityDeactivated(LegalEntityRead):
    """What `DELETE /api/aziende/{id}` answers (spec §3): the row, switched off, and
    how many live customers still point at it. Nothing new is born under such a
    customer until it is moved («sposta prima il cliente su un'azienda attiva»), so the
    count is the work the person has left to do, said once, in the same answer."""

    clienti_collegati: int


# The fields `as_template_values` and the MCP `describe` leave out: identity, state and
# timestamps are facts about the row, not about the business a template prints.
TEMPLATE_EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {"id", "predefinita", "attiva", "created_at", "updated_at"}
)
