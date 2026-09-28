"""Every shape campaigns read and answer, API and service alike."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    ValidationInfo,
    computed_field,
    field_validator,
)
from pydantic_core import PydanticCustomError

from rebase_core.models import (
    CAMPAIGN_BUTTON_MAX_LENGTH,
    CAMPAIGN_NAME_MAX_LENGTH,
    CAMPAIGN_SUBJECT_MAX_LENGTH,
    CAMPAIGN_TEXT_MAX_LENGTH,
)
from rebase_core.search import SEARCH_MAX_LENGTH

# A filter's amount is kept like every other amount the hub stores: euro with cents at
# most and never below zero, with no ceiling the editor's own field lacks. The editor
# sends two decimals (REB-485) and reads a stored one back with two decimals, which is
# exact only because nothing here keeps a third (REB-526, Greptile on #432).
FilterAmount = Annotated[Decimal, Field(ge=0, decimal_places=2)]


class TalentiFiltri(BaseModel):
    """The Talenti list's own filters (`GET /api/hub/talent`, `talenti.py:191`), stored
    on the campaign and replayed through `TalentiService` when the list is shown and when
    a mail is about to leave."""

    lista: Literal["talenti"]
    stato: str | None = Field(default=None, max_length=20)
    q: str | None = Field(default=None, max_length=SEARCH_MAX_LENGTH)
    posizione: str | None = Field(default=None, max_length=160)
    remoto: str | None = Field(default=None, max_length=10)
    tariffa_min: FilterAmount | None = None
    tariffa_max: FilterAmount | None = None
    origine: str | None = Field(default=None, max_length=40)
    utm_source: str | None = Field(default=None, max_length=200)
    has_cv: bool | None = None
    con_accessi: bool | None = None
    creato_da: datetime | None = None
    creato_a: datetime | None = None


class AziendeFiltri(BaseModel):
    """The company list's own filters (`companies.py:160`)."""

    lista: Literal["aziende"]
    stato: str | None = Field(default=None, max_length=20)
    q: str | None = Field(default=None, max_length=SEARCH_MAX_LENGTH)
    budget_min: FilterAmount | None = None
    budget_max: FilterAmount | None = None
    periodo_da: date | None = None
    origine: str | None = Field(default=None, max_length=40)
    creato_da: datetime | None = None
    creato_a: datetime | None = None


Filtri = Annotated[TalentiFiltri | AziendeFiltri, Field(discriminator="lista")]

NAME_IS_BLANK = "Il nome della campagna non può essere vuoto."


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _not_blank(value: str) -> str:
    if not value:
        # A custom error, not a `ValueError`: its message reaches the page as it
        # stands, with no «Value error,» in front of the sentence.
        raise PydanticCustomError("nome_vuoto", NAME_IS_BLANK)
    return value


# Stripped first, then checked (REB-524): `min_length=1` on the raw value let «   »
# through, and the service's own strip stored it empty. `max_length` is checked on the
# stripped name too.
CampaignName = Annotated[
    str,
    BeforeValidator(_strip),
    Field(max_length=CAMPAIGN_NAME_MAX_LENGTH),
    AfterValidator(_not_blank),
]

Azione = Literal[
    "entrato", "cv", "scheda_completa", "profilo_creato", "richiesta_aggiornata", "pigro_cliente"
]
Meta = Literal["area", "wizard", "pigro", "richiesta"]


class CampaignDraft(BaseModel):
    nome: CampaignName
    fonte: Literal["stato", "filtri"]
    stato_percorso: str | None = Field(default=None, max_length=30)
    filtri: Filtri | None = None
    oggetto: str = Field(default="", max_length=CAMPAIGN_SUBJECT_MAX_LENGTH)
    testo: str = Field(default="", max_length=CAMPAIGN_TEXT_MAX_LENGTH)
    bottone_testo: str = Field(default="", max_length=CAMPAIGN_BUTTON_MAX_LENGTH)
    bottone_meta: Meta
    azione: Azione


# The `CampaignPatch` fields whose column is `NOT NULL` in `models.py`'s `Campaign`:
# `None` on one of them is how a field is typed here (so leaving it out of the request
# means "no change"), never a value the row can hold. Left unchecked, an explicit
# `null` on one reaches `CampaignService.update`'s blanket `setattr` and raises an
# `IntegrityError`, a 500, instead of a sentence naming the field. `stato_percorso` and
# `filtri` are not here: the row legitimately holds `NULL` in one of them, depending on
# `fonte`.
_NOT_NULLABLE = ("nome", "fonte", "oggetto", "testo", "bottone_testo", "bottone_meta", "azione")


class CampaignPatch(BaseModel):
    nome: CampaignName | None = None
    fonte: Literal["stato", "filtri"] | None = None
    stato_percorso: str | None = Field(default=None, max_length=30)
    filtri: Filtri | None = None
    oggetto: str | None = Field(default=None, max_length=CAMPAIGN_SUBJECT_MAX_LENGTH)
    testo: str | None = Field(default=None, max_length=CAMPAIGN_TEXT_MAX_LENGTH)
    bottone_testo: str | None = Field(default=None, max_length=CAMPAIGN_BUTTON_MAX_LENGTH)
    bottone_meta: Meta | None = None
    azione: Azione | None = None

    @field_validator(*_NOT_NULLABLE, mode="after")
    @classmethod
    def _no_explicit_null_on_a_required_field(cls, value: object, info: ValidationInfo) -> object:
        """The database's own `NOT NULL` columns, enforced here too, the same way
        `CompanyFields._giorni_presenza_matches_remoto` enforces a check constraint: a
        sentence naming the field rather than the `IntegrityError` a blanket `setattr`
        would otherwise reach. A `field_validator`, not a `model_validator`, so the
        error's `loc` ends in the field's own name: the hub web reads `ApiError.fields`
        from `detail[].loc[-1]` (`apps/web/src/lib/api.ts`), and a body-level `loc`
        pointed at nothing a wizard could show. Pydantic skips a default value's own
        validators (`validate_default` is off, the default here), so this never runs
        for a field the request left out -- only for one it set, `null` included, which
        is exactly the distinction "no change" needs."""
        if value is None:
            raise ValueError(f"{info.field_name}: il campo non può essere svuotato")
        return value


# An address as the list holds it, not as `EmailStr` would have it: the unticked are
# only compared with the list's own lowercase addresses, never mailed, so a legacy
# address `EmailStr` rejects must not 422 the whole send.
Unticked = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, max_length=320)]


class ScheduleRequest(BaseModel):
    """`giorno` and `ora` are Europe/Rome wall-clock time, both or neither; neither is
    «Invia adesso». `esclusi` are the addresses the admin unticked on step 1."""

    giorno: date | None = None
    ora: time | None = None
    esclusi: list[Unticked] = []


class NeverWriteRequest(BaseModel):
    email: EmailStr


class CampaignCounts(BaseModel):
    destinatari: int = 0
    in_coda: int = 0
    inviate: int = 0
    saltate: int = 0
    fallite: int = 0
    consegnate: int = 0
    rimbalzate: int = 0
    cliccate: int = 0
    entrate: int = 0
    azioni: int = 0


class CampaignRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    slug: str
    fonte: str
    stato_percorso: str | None
    filtri: dict[str, object] | None
    segue_id: UUID | None
    oggetto: str
    testo: str
    bottone_testo: str
    bottone_meta: str
    azione: str
    stato: str
    contenuto_at: datetime
    programmata_per: datetime | None
    prova_inviata_at: datetime | None
    inviata_at: datetime | None
    created_at: datetime
    # REB-524: set while a send is stopped on something that will not fix itself; the
    # page reads «Invio fermo: <fermo_motivo>».
    fermo_at: datetime | None
    fermo_motivo: str | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pronta(self) -> bool:
        """A test has left since the last edit: what enables «Invia» (Review Focus 1)."""
        return self.prova_inviata_at is not None and self.prova_inviata_at >= self.contenuto_at


class CampaignListItem(CampaignRead):
    conteggi: CampaignCounts


class CampaignList(BaseModel):
    items: list[CampaignListItem]


class RecipientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    nome: str | None
    tipo: str
    stato: str
    motivo: str | None
    inviata_at: datetime | None
    consegnata_at: datetime | None
    rimbalzata_at: datetime | None
    primo_clic_at: datetime | None
    reclamo_at: datetime | None
    entrato_at: datetime | None
    azione_at: datetime | None
    # Read at detail time, never stored: the login or the card carries this campaign's
    # link (REB-426), so the mail was the door (spec § 4.3).
    entrato_dalla_mail: bool = False
    azione_dalla_mail: bool = False


class CampaignDetail(BaseModel):
    campagna: CampaignRead
    conteggi: CampaignCounts
    destinatari: list[RecipientRead]


class AudienceRowRead(BaseModel):
    email: str
    nome: str | None
    tipo: str
    escluso: str | None


class AudiencePreview(BaseModel):
    righe: list[AudienceRowRead]
    incluse: int
    escluse: int


class TemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stato_percorso: str
    etichetta: str
    oggetto: str
    testo: str
    bottone_testo: str
    bottone_meta: str
    azione: str
