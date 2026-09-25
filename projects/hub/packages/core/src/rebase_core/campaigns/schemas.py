"""Every shape campaigns read and answer, API and service alike."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field

from rebase_core.models import (
    CAMPAIGN_BUTTON_MAX_LENGTH,
    CAMPAIGN_NAME_MAX_LENGTH,
    CAMPAIGN_SUBJECT_MAX_LENGTH,
    CAMPAIGN_TEXT_MAX_LENGTH,
)
from rebase_core.search import SEARCH_MAX_LENGTH


class TalentiFiltri(BaseModel):
    """The Talenti list's own filters (`GET /api/hub/talent`, `talenti.py:191`), stored
    on the campaign and replayed through `TalentiService` when the list is shown and when
    a mail is about to leave."""

    lista: Literal["talenti"]
    stato: str | None = Field(default=None, max_length=20)
    q: str | None = Field(default=None, max_length=SEARCH_MAX_LENGTH)
    posizione: str | None = Field(default=None, max_length=160)
    remoto: str | None = Field(default=None, max_length=10)
    tariffa_min: Decimal | None = None
    tariffa_max: Decimal | None = None
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
    budget_min: Decimal | None = None
    budget_max: Decimal | None = None
    periodo_da: date | None = None
    origine: str | None = Field(default=None, max_length=40)
    creato_da: datetime | None = None
    creato_a: datetime | None = None


Filtri = Annotated[TalentiFiltri | AziendeFiltri, Field(discriminator="lista")]

Azione = Literal[
    "entrato", "cv", "scheda_completa", "profilo_creato", "richiesta_aggiornata", "pigro_cliente"
]
Meta = Literal["area", "wizard", "pigro", "richiesta"]


class CampaignDraft(BaseModel):
    nome: str = Field(min_length=1, max_length=CAMPAIGN_NAME_MAX_LENGTH)
    fonte: Literal["stato", "filtri"]
    stato_percorso: str | None = Field(default=None, max_length=30)
    filtri: Filtri | None = None
    oggetto: str = Field(default="", max_length=CAMPAIGN_SUBJECT_MAX_LENGTH)
    testo: str = Field(default="", max_length=CAMPAIGN_TEXT_MAX_LENGTH)
    bottone_testo: str = Field(default="", max_length=CAMPAIGN_BUTTON_MAX_LENGTH)
    bottone_meta: Meta
    azione: Azione


class CampaignPatch(BaseModel):
    nome: str | None = Field(default=None, min_length=1, max_length=CAMPAIGN_NAME_MAX_LENGTH)
    fonte: Literal["stato", "filtri"] | None = None
    stato_percorso: str | None = Field(default=None, max_length=30)
    filtri: Filtri | None = None
    oggetto: str | None = Field(default=None, max_length=CAMPAIGN_SUBJECT_MAX_LENGTH)
    testo: str | None = Field(default=None, max_length=CAMPAIGN_TEXT_MAX_LENGTH)
    bottone_testo: str | None = Field(default=None, max_length=CAMPAIGN_BUTTON_MAX_LENGTH)
    bottone_meta: Meta | None = None
    azione: Azione | None = None


class ScheduleRequest(BaseModel):
    """`giorno` and `ora` are Europe/Rome wall-clock time, both or neither; neither is
    «Invia adesso». `esclusi` are the addresses the admin unticked on step 1."""

    giorno: date | None = None
    ora: time | None = None
    esclusi: list[EmailStr] = []


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


class CampaignRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    slug: str
    fonte: str
    stato_percorso: str | None
    filtri: dict[str, object] | None
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
